"""串口桥接：负责与 RISC-V 开发板的全部通信。

两种模式（可运行时切换，无需重启服务）：
- mock   ：本机模拟板端状态机，不依赖任何硬件；用于前端开发与演示。
- serial ：通过 pyserial 独占真实 COM 口（默认 COM9, 115200, 8-N-1）。

行为要点（对齐实施计划阶段 2）：
- 启动时先发 Door ping 确认通信，不依赖重新出现启动日志。
- 每 5 秒心跳一次；15 秒无有效 AC| 响应判离线。
- 命令使用 12 位十六进制请求编号，禁止 ffff 前缀。
- 断线后只重连 ping/status，绝不自动重发状态不明的 allow。
- 解析器忽略 Shell 回显/提示符/启动日志/ANSI，只认 AC| 帧。
- 换行符自动探测（\\r\\n → \\r → \\n），兼容 letter-shell 配置。
"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from .config import Settings
from .protocol import Frame, build_command, new_request_id, parse_line
from .feature_protocol import FeatureAssembler, FeaturePacket
from .serial_diagnostics import SerialDiagnostics


class _Pending:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[Frame] = None


class SerialBridge:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.mode: str = settings.bridge_mode
        self.port: Optional[str] = settings.serial_port
        self.baud: int = settings.serial_baud

        self.online: bool = False
        self.lock_state: str = "LOCKED"
        self.last_response: Optional[datetime] = None
        self.line_ending: str = "\r\n"
        self.last_error: str = ""

        # 由 main 注入
        self.device_id: Optional[int] = None
        self.on_board_frame: Optional[Callable[[Frame], None]] = None
        self.on_online_change: Optional[Callable[[bool], None]] = None
        self.on_feature: Optional[Callable[[FeaturePacket], None]] = None
        self.on_feature_cancel: Optional[Callable[[str], None]] = None
        self.features = FeatureAssembler()
        self.feature_session: Optional[str] = None
        self._hello_id: Optional[str] = None
        self.face_running: bool = False
        self.face_health: str = "UNCONNECTED"
        self._reader_thread: Optional[threading.Thread] = None

        self._ser = None  # type: ignore
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._mock_q: "queue.Queue[tuple[str, str, _Pending]]" = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._was_online: Optional[bool] = None
        self._reader_dead = threading.Event()
        self._diagnostics = SerialDiagnostics(settings.data_dir)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self.stop()
        self._diagnostics.event("start", mode=self.mode, port=self.port, baud=self.baud,
                                char_interval_ms=self._settings.serial_char_interval * 1000)
        self._stop.clear()
        self._reader_dead.clear()
        if self.mode == "serial":
            self._threads = [
                threading.Thread(target=self._maintenance_loop, name="bridge-maintenance", daemon=True),
            ]
        else:
            self.online = True
            self._threads = [
                threading.Thread(target=self._mock_loop, name="bridge-mock", daemon=True),
            ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._diagnostics.event("stop")
        self._stop.set()
        self._close_serial()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=2)
        self._threads = []
        self._pending.clear()
        self.feature_session = None
        self._hello_id = None
        self.features.reset()
        self._set_online(False, "桥接已停止")

    def send_shell(self, command: str, request_id: str, timeout: float = 2.0) -> Optional[Frame]:
        """Feature replies use a caller ID; they must not generate a new Door ID."""
        if self.mode != "serial" or not self.online or len(command) > 64:
            return None
        pending = _Pending()
        with self._lock:
            if request_id in self._pending:
                return None
            self._pending[request_id] = pending
        try:
            with self._lock:
                if self._ser is None:
                    return None
                self._send_line(self._ser, command, self.line_ending)
            if not pending.event.wait(timeout):
                self._diagnostics.event("command_timeout", request_id=request_id, command=command)
            return pending.result
        except Exception as exc:
            self._diagnostics.event("write_error", error=str(exc))
            self.last_error = f"特征命令写入失败: {exc}"
            self._set_online(False, "串口写入失败")
            self._reader_dead.set()
            return None
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def set_mode(self, mode: str, port: Optional[str] = None, baud: Optional[int] = None) -> None:
        with self._lock:
            self.mode = mode
            if port:
                self.port = port
            if baud:
                self.baud = baud
        self._set_online(False, "桥接模式切换")
        self.start()

    # ---------- 对外接口 ----------
    def send_command(self, action: str, timeout: Optional[float] = None) -> Optional[Frame]:
        """发送 Door 命令并等待匹配响应。超时/失败返回 None。

        allow/deny 的响应是 ACK；ping 的响应是 PONG；status 的响应是 STATUS。
        3 秒后自动锁定产生的 STATE 是异步事件，不在本函数返回。
        """
        timeout = timeout or self._settings.cmd_timeout
        rid = new_request_id()
        pending = _Pending()
        with self._lock:
            self._pending[rid] = pending
        try:
            if self.mode == "serial":
                self._write_command(action, rid)
            else:
                self._mock_q.put((rid, action, pending))
            if not pending.event.wait(timeout=timeout):
                self._diagnostics.event("command_timeout", request_id=rid, command=build_command(action, rid))
                return None
            return pending.result
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "online": self.online,
                "lock_state": self.lock_state,
                "port": self.port if self.mode == "serial" else None,
                "baud": self.baud if self.mode == "serial" else None,
                "serial_char_interval_ms": self._settings.serial_char_interval * 1000,
                "last_heartbeat": (
                    self.last_response.strftime("%Y-%m-%d %H:%M:%S")
                    if self.last_response else None
                ),
                "line_ending": self.line_ending if self.mode == "serial" else "",
                "last_error": self.last_error,
                "feature_session": self.feature_session,
                "feature_error": self.features.last_error,
                "face_running": self.face_running,
                "face_health": self.face_health,
                "diagnostic_log": str(self._diagnostics.path),
                "diagnostic_error": self._diagnostics.error,
            }

    # ---------- 帧分发（serial 与 mock 共用） ----------
    def _handle_frame(self, frame: Frame) -> None:
        if frame.kind == "FREADY":
            if frame.lower_id == self._hello_id and frame.payload == "lbph4-v1":
                self.feature_session = frame.lower_id
                self.features.reset(self.feature_session)
                self.face_running = True
                self.face_health = "READY"
        if frame.kind == "FPROBE":
            parts = frame.payload.split("|", 2)
            if len(parts) == 3:
                token, active, self.face_health = parts
                self.face_running = active == "1"
                if token != self.feature_session or not self.face_running:
                    self.feature_session = None
                    self.features.reset()
        if frame.kind in ("FBEGIN", "FCHUNK", "FEND", "FCANCEL"):
            self.last_response = datetime.now()
            if frame.kind == "FCANCEL" and self.on_feature_cancel:
                self.on_feature_cancel(frame.lower_id)
            packet = self.features.feed(frame)
            if packet is not None and self.on_feature:
                self.on_feature(packet)
            return
        self.last_response = datetime.now()
        # 状态同步
        if frame.kind == "STATUS" and frame.payload.split("|")[0] in (
            "LOCKED", "UNLOCKED", "FAULT"
        ):
            self.lock_state = frame.payload.split("|")[0]
        elif frame.kind == "ACK" and frame.payload.startswith("ALLOW"):
            self.lock_state = "UNLOCKED"
        elif frame.kind == "STATE":
            self.lock_state = frame.payload.split("|")[0]
        elif frame.kind == "ERR" and "FAULT" in frame.payload:
            self.lock_state = "FAULT"
        # 唤醒等待者
        with self._lock:
            pend = self._pending.get(frame.lower_id)
        if pend is not None:
            pend.result = frame
            pend.event.set()
        # 通知业务层（更新事件记录/设备状态）
        if self.on_board_frame is not None:
            self.on_board_frame(frame)
        # 收到任何有效帧即认为在线（serial 模式）
        if self.mode == "serial":
            self._set_online(True, "收到板端响应")

    def _set_online(self, online: bool, reason: str = "") -> None:
        if not online:
            self.feature_session = None
            self.features.reset()
            self.face_running = False
        changed = False
        with self._lock:
            if self.online != online:
                self.online = online
                changed = True
        if changed and self.on_online_change is not None:
            self._diagnostics.event("online_change", online=online, reason=reason, port=self.port,
                                    last_error=self.last_error)
            self.on_online_change(online)

    # ---------- serial 模式 ----------
    def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            if not self._ensure_open():
                self._set_online(False, "串口不可用")
                self._stop.wait(self._settings.reconnect_interval)
                continue
            transfer = self.features.current
            if transfer is not None:
                # Let one feature transmission finish before Shell heartbeats add output.
                if time.monotonic() - transfer["started_at"] <= 8:
                    self._stop.wait(0.25)
                    continue
                self.features.current = None
                self.features.last_error = "特征传输超时"
            # 心跳
            resp = self.send_command("ping", timeout=self._settings.cmd_timeout)
            if resp is None:
                # 等待一小段再看离线（可能只是瞬时超时）
                self._stop.wait(1.0)
            if self.last_response is None or (
                datetime.now() - self.last_response
            ).total_seconds() > self._settings.offline_after:
                if not self.last_error:
                    self.last_error = f"超过 {self._settings.offline_after:g} 秒未收到有效板端响应，请检查板端日志和 USB 连接"
                self._set_online(False, "心跳超时")
                self._close_serial()
            else:
                self._set_online(True, "心跳正常")
                if self.features.current is None:
                    self.send_command("status", timeout=self._settings.cmd_timeout)
                if self._settings.access_input == "board":
                    probe_id = new_request_id()
                    probe = self.send_shell(f"FaceProbe {probe_id}", probe_id)
                    if probe is None or probe.kind != "FPROBE":
                        self.feature_session = None
                        self.face_running = False
                        self.features.reset()
                    if not self.feature_session:
                        self._hello_id = new_request_id()
                        self.send_shell(f"FaceHello {self._hello_id}", self._hello_id)
            self._stop.wait(self._settings.heartbeat_interval)

    def _ensure_open(self) -> bool:
        if self._ser is not None and not self._reader_dead.is_set():
            return True
        self._close_serial()
        self._reader_dead.clear()
        self._diagnostics.event("open_attempt", port=self.port, baud=self.baud)
        try:
            import serial

            ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
            )
        except Exception as exc:  # noqa: BLE001 - 端口不存在/被占用等
            self._diagnostics.event("open_error", error=str(exc))
            self.last_error = f"打开串口失败: {exc}"
            return False

        # 探测换行符：逐个候选发 ping，直到收到 PONG
        for ending in self._settings.line_ending_candidates:
            rid = new_request_id()
            try:
                self._send_line(ser, build_command("ping", rid), ending, probe=True)
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    raw_line = ser.readline(1024)
                    line = raw_line.decode("ascii", errors="ignore")
                    self._diagnostics.received(len(raw_line))
                    frame = parse_line(line)
                    self._diagnostics.line(raw_line.decode("ascii", errors="backslashreplace"), frame, "connect")
                    if frame is not None and frame.kind == "PONG" and frame.lower_id == rid:
                        self.line_ending = ending
                        self._ser = ser
                        self._reader_thread = threading.Thread(
                            target=self._reader_loop, args=(ser,), name="bridge-reader", daemon=True
                        )
                        self._reader_thread.start()
                        self._handle_frame(frame)
                        self.last_error = ""
                        self._diagnostics.event("connected", port=self.port, ending=repr(ending))
                        return True
            except Exception as exc:  # noqa: BLE001
                self._diagnostics.event("probe_error", error=str(exc))
                self.last_error = f"换行符探测失败: {exc}"
                break
        self.last_error = "未收到 PONG，检查端口/波特率/板端固件"
        self._diagnostics.event("probe_failed", port=self.port)
        try:
            ser.close()
        except Exception:  # noqa: BLE001
            pass
        return False

    def _reader_loop(self, ser) -> None:  # type: ignore[no-untyped-def]
        buffer = bytearray()
        dropping = False
        try:
            while not self._stop.is_set():
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue
                self._diagnostics.received(len(chunk))
                for byte in chunk:
                    if byte in (10, 13):
                        if buffer and not dropping:
                            line = buffer.decode("ascii", errors="ignore")
                            frame = parse_line(line)
                            self._diagnostics.line(
                                buffer.decode("ascii", errors="backslashreplace"), frame, "reader"
                            )
                            if frame is not None:
                                self._handle_frame(frame)
                        buffer.clear()
                        dropping = False
                    elif not dropping:
                        buffer.append(byte)
                        if len(buffer) > 1024:
                            buffer.clear()
                            dropping = True
                            self.features.current = None
                            self.features.last_error = "串口行超出长度限制，已丢弃传输"
                            self._diagnostics.event("rx_line_too_long")
        except Exception as exc:  # noqa: BLE001 - 拔线/设备复位
            self._diagnostics.event("reader_error", error=str(exc))
            self.last_error = f"串口读取中断: {exc}"
        finally:
            self._diagnostics.event("reader_exit", stopping=self._stop.is_set())
            self._reader_dead.set()

    def _write_command(self, action: str, rid: str) -> bool:
        if self._ser is None or self._reader_dead.is_set():
            return False
        try:
            with self._lock:
                self._send_line(self._ser, build_command(action, rid), self.line_ending)
            return True
        except Exception as exc:  # noqa: BLE001
            self._diagnostics.event("write_error", error=str(exc))
            self.last_error = f"串口写入失败: {exc}"
            self._set_online(False, "串口写入失败")
            return False

    def _send_line(self, ser, command: str, ending: str, *, probe: bool = False) -> None:
        """Serialize a complete line, pacing characters to investigate missing input.

        This does not retry any command, including an authorization decision.
        Physical baud rate stays unchanged. A zero interval restores burst writes.
        """
        data = (command + ending).encode("ascii")
        interval = self._settings.serial_char_interval
        with self._lock:
            self._diagnostics.event(
                "tx_probe" if probe else "tx", command=command, ending=repr(ending),
                char_interval_ms=interval * 1000,
            )
            if self._stop.is_set():
                raise InterruptedError("桥接已停止，命令发送已取消")
            if interval == 0:
                if ser.write(data) != len(data):
                    raise OSError("串口未完整写入命令")
                ser.flush()
                return
            for index in range(len(data)):
                if ser.write(data[index:index + 1]) != 1:
                    raise OSError("串口未完整写入命令字符")
                ser.flush()
                if index + 1 < len(data) and self._stop.wait(interval):
                    raise InterruptedError("桥接已停止，命令发送已取消")

    def _close_serial(self) -> None:
        self.feature_session = None
        self.features.reset()
        ser = self._ser
        self._ser = None
        if ser is not None:
            self._diagnostics.event("close", port=self.port)
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
        reader = self._reader_thread
        if reader and reader is not threading.current_thread() and reader.is_alive():
            reader.join(timeout=1)
        self._reader_thread = None

    # ---------- mock 模式 ----------
    def _mock_loop(self) -> None:
        # 模拟板端启动日志
        while not self._stop.is_set():
            try:
                rid, action, pending = self._mock_q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.last_response = datetime.now()
            frame = self._mock_handle(action, rid)
            if frame is not None:
                pending.result = frame
                pending.event.set()
                if self.on_board_frame is not None:
                    self.on_board_frame(frame)
            if action != "allow":
                self._set_online(True, "模拟心跳")

    def _mock_handle(self, action: str, rid: str) -> Optional[Frame]:
        if action == "ping":
            return Frame("PONG", rid, "1")
        if action == "status":
            return Frame("STATUS", rid, self.lock_state)
        if action == "allow":
            if self.lock_state != "LOCKED":
                return Frame("ERR", rid, "BUSY")
            self.lock_state = "UNLOCKED"
            # 对齐板端 DOOR_UNLOCK_MS=3000：3 秒后自动锁定
            threading.Timer(3.0, self._mock_finish_allow, args=(rid,)).start()
            return Frame("ACK", rid, "ALLOW|OK")
        if action == "deny":
            if self.lock_state != "LOCKED":
                return Frame("ERR", rid, "BUSY")
            return Frame("ACK", rid, "DENY|OK")
        return None

    def _mock_finish_allow(self, rid: str) -> None:
        if self._stop.is_set():
            return
        self.lock_state = "LOCKED"
        frame = Frame("STATE", rid, "LOCKED")
        self.last_response = datetime.now()
        if self.on_board_frame is not None:
            self.on_board_frame(frame)
