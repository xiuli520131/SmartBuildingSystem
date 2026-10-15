"""Serial feature jobs execute outside the single serial reader thread."""
from __future__ import annotations

import queue
import threading
import time

from . import database as db
from .feature_protocol import FeaturePacket
from .feature_service import ALGORITHM, FeatureService
from .protocol import new_request_id


class EdgeAccessService:
    def __init__(self, features: FeatureService, bridge, access) -> None:
        self.features, self.bridge, self.access = features, bridge, access
        self.jobs: queue.Queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.enrollment: dict | None = None
        self.last_result: dict | None = None
        self.seen: set[tuple[str, str]] = set()
        self.seen_session: str | None = None
        self.cancelled: set[str] = set()
        self.last_allow = 0.0
        self.thread = threading.Thread(target=self._worker, name="edge-feature-worker", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)

    def cancel(self, request_id: str) -> None:
        with self.lock:
            self.cancelled.add(request_id)
            if self.enrollment and self.enrollment["request_id"] == request_id:
                self.enrollment = None
            if len(self.cancelled) > 256:
                self.cancelled = {request_id}

    def enqueue(self, packet: FeaturePacket) -> None:
        try:
            self.jobs.put_nowait(packet)
        except queue.Full:
            self.cancel(packet.request_id)

    def status(self) -> dict:
        with self.lock:
            if self.enrollment and (self.enrollment["session"] != self.bridge.feature_session
                                    or self.enrollment["deadline"] <= time.monotonic()):
                self.enrollment = None
            enrollment = dict(self.enrollment) if self.enrollment else None
            if enrollment:
                enrollment["remaining_seconds"] = max(0, int(enrollment["deadline"] - time.monotonic()))
                enrollment.pop("deadline", None)
        return {**self.features.status(), "input_mode": self.bridge._settings.access_input,
                "ready": bool(self.bridge.feature_session) and self.bridge.online and self.bridge.face_running,
                "camera_state": self.bridge.face_health,
                "session": self.bridge.feature_session, "last_result": self.last_result,
                "enrollment": enrollment, "transport_error": self.bridge.features.last_error}

    def enroll(self, user_id: int | None, role: str) -> dict:
        if role not in ("train", "validation", "unknown"):
            raise ValueError("无效样本类型")
        if role != "unknown" and not db.fetch_one("SELECT id FROM users WHERE id=?", (user_id,)):
            raise ValueError("人员不存在")
        if not self.bridge.feature_session or not self.bridge.online:
            raise ValueError("板端特征模块未就绪，请先烧录摄像头固件并连接真实串口")
        if self.bridge.features.current is not None:
            raise ValueError("板端正在传输现场特征，请稍后录入")
        rid = new_request_id()
        with self.lock:
            if self.enrollment and self.enrollment["deadline"] > time.monotonic():
                raise ValueError("已有录入正在进行")
            self.enrollment = {"request_id": rid, "session": self.bridge.feature_session,
                               "user_id": user_id, "role": role, "deadline": time.monotonic() + 30}
        frame = self.bridge.send_shell(f"FaceEnroll {rid}", rid)
        if frame is None or frame.kind != "FENROLL" or frame.payload != "OK":
            with self.lock:
                self.enrollment = None
            raise ValueError("板端没有接受录入命令")
        return {"ok": True, "request_id": rid, "message": "请面对板载摄像头，30 秒内采集一个合格特征"}

    def _current(self, packet: FeaturePacket) -> bool:
        return (self.bridge.mode == "serial" and self.bridge.online
                and self.bridge.feature_session == packet.session
                and time.monotonic() - packet.started_at < 8
                and packet.request_id not in self.cancelled)

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet = self.jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                key = (packet.session, packet.request_id)
                if self.seen_session != packet.session and packet.session == self.bridge.feature_session:
                    self.seen_session = packet.session
                    self.seen.clear()
                if key in self.seen or not self._current(packet):
                    continue
                if len(self.seen) >= 1024:
                    raise ValueError("会话请求数已达上限，请重新连接串口")
                self.seen.add(key)
                if packet.mode == "ENROLL":
                    self._save_enrollment(packet)
                else:
                    self._authorize(packet)
            except Exception as exc:
                if packet.mode == "ENROLL":
                    with self.lock:
                        self.enrollment = None
                self.last_result = {"ok": False, "request_id": packet.request_id,
                                    "message": str(exc), "authorization": "block", "execution": "error"}
                self.access._add_alert("feature_error", "warning", str(exc))
            finally:
                self.jobs.task_done()

    def _save_enrollment(self, packet: FeaturePacket) -> None:
        with self.lock:
            enrollment = self.enrollment
            if (not enrollment or enrollment["request_id"] != packet.request_id
                    or enrollment["session"] != packet.session
                    or time.monotonic() >= enrollment["deadline"]):
                raise ValueError("没有匹配的有效录入会话")
            if not self._current(packet):
                raise ValueError("录入传输已失效")
            result = self.features.add_sample(enrollment["user_id"], enrollment["role"], packet.raw)
            self.enrollment = None
        self.last_result = {**result, "request_id": packet.request_id, "kind": "enrollment"}
        # Acknowledging enrollment never unlocks the door.
        self.bridge.send_shell(f"FaceSaved {packet.request_id}", packet.request_id)

    def _authorize(self, packet: FeaturePacket) -> None:
        device = db.fetch_one("SELECT * FROM devices WHERE id=?", (self.bridge.device_id,))
        if device is None:
            raise ValueError("串口设备未绑定")
        rec = self.features.recognize(packet.raw)
        user = rec.get("user")
        action, reason = "DENY", rec["message"]
        if user and not user["enabled"]:
            reason = "人员已停用"
        elif user and self.access._valid_permission(user["id"], device["id"]) is None:
            reason = "无本门有效权限或已过期"
        elif user:
            action, reason = "ALLOW", "身份及通行权限核验通过"
        if action == "ALLOW" and (self.bridge.lock_state != "LOCKED" or time.monotonic() - self.last_allow < 3):
            action, reason = "DENY", "门锁处理中或触发间隔内"
        result = {"ok": True, "recognition": rec["recognition"], "confidence": rec.get("confidence"),
                  "distance": rec.get("distance"), "message": rec["message"],
                  "recognized_no": user["user_no"] if user else None,
                  "recognized_name": user["name"] if user else None,
                  "authorization": action.lower(), "auth_reason": reason,
                  "request_id": packet.request_id, "execution": "pending", "raw": None}
        # Record before writing: an ACK can arrive immediately on the reader thread.
        self.access._record_event(device["id"], user["id"] if user else None, rec, result)
        if not self._current(packet):
            result.update(execution="unconfirmed", auth_reason="请求过期或已取消，未发送授权")
        else:
            # Fetch enabled/permission again immediately before the outgoing action.
            if action == "ALLOW":
                current_user = db.fetch_one("SELECT enabled FROM users WHERE id=?", (user["id"],))
                if (not current_user or not current_user["enabled"]
                        or self.access._valid_permission(user["id"], device["id"]) is None):
                    action, reason = "DENY", "人员状态或权限已变更"
                    result.update(authorization="deny", auth_reason=reason)
                    db.execute("UPDATE access_events SET authorization='deny', auth_reason=? WHERE id=?",
                               (reason, result["event_id"]))
            if action == "ALLOW":
                self.last_allow = time.monotonic()
            frame = self.bridge.send_shell(f"FaceDecision {packet.request_id} {action}", packet.request_id, timeout=3.0)
            if frame and frame.kind == "ACK" and frame.payload == f"{action}|OK":
                result.update(execution="confirmed", raw=frame.describe())
            elif frame and frame.kind == "ERR":
                result.update(execution="error", raw=frame.describe())
            else:
                result["execution"] = "timeout"
        db.execute("UPDATE access_events SET execution=?, raw=?, auth_reason=? WHERE id=? AND execution='pending'",
                   (result["execution"], result["raw"], result["auth_reason"], result["event_id"]))
        self.last_result = result
        self.access.maybe_alert_repeated_failure()
