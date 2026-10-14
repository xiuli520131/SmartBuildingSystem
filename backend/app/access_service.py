"""通行决策链：识别 → 人员启用 → 权限时段 → 设备在线 → allow/deny。

同时负责：
- 把板端 ACK/STATE/ERR 异步回写 access_events 与设备状态。
- 设备离线/上线切换告警。
- 未知人员、连续失败、执行未确认等告警（带窗口去重）。
- 重复帧节流：门未锁定时不重复发 allow；deny 每 5 秒最多一次。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Optional

from . import database as db
from .config import settings
from .face_service import FaceService
from .protocol import Frame
from .serial_bridge import SerialBridge


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AccessService:
    def __init__(self, face_service: FaceService, bridge: SerialBridge) -> None:
        self.face = face_service
        self.bridge = bridge
        self._lock = threading.RLock()
        self._last_allow: dict[int, float] = {}
        self._last_deny: dict[int, float] = {}

    # ---------- 板端异步事件回写 ----------
    def on_board_frame(self, frame: Frame) -> None:
        device_id = self.bridge.device_id
        if frame.kind in ("ACK", "STATE", "ERR"):
            db.execute(
                "UPDATE access_events SET execution=?, raw=? WHERE request_id=? AND execution='pending'",
                (
                    "confirmed" if frame.kind in ("ACK", "STATE") else "error",
                    frame.describe(),
                    frame.lower_id,
                ),
            )
        if frame.kind == "ACK" and "ALLOW" in frame.payload:
            self._update_lock_state("UNLOCKED", device_id)
        elif frame.kind == "STATE":
            state = frame.payload.split("|")[0]
            if state in ("LOCKED", "UNLOCKED", "FAULT"):
                self._update_lock_state(state, device_id)
        elif frame.kind == "STATUS":
            state = frame.payload.split("|")[0]
            if state in ("LOCKED", "UNLOCKED", "FAULT"):
                self._update_lock_state(state, device_id)
        elif frame.kind == "ERR":
            reason = frame.payload.split("|")[0]
            if reason == "FAULT":
                self._update_lock_state("FAULT", device_id)
            self._add_alert(
                "serial_busy",
                "warning",
                f"板端返回错误: {reason}（请求 {frame.lower_id}）",
            )

    def on_online_change(self, online: bool) -> None:
        device_id = self.bridge.device_id
        if device_id:
            db.execute("UPDATE devices SET online=? WHERE id=?", (1 if online else 0, device_id))
        if online:
            self._resolve_alert_type("device_offline")
            self._add_alert("device_online", "info", "开发板已上线（串口通信恢复）")
        else:
            self._add_alert(
                "device_offline", "critical", f"开发板离线（{self.bridge.port or '模拟'} 通信不可用）"
            )

    def _update_lock_state(self, state: str, device_id: Optional[int]) -> None:
        self.bridge.lock_state = state
        if device_id:
            db.execute("UPDATE devices SET lock_state=? WHERE id=?", (state, device_id))

    # ---------- 主决策 ----------
    def process_frame(self, image_b64: str, device_no: str = "D001") -> dict[str, Any]:
        device = db.fetch_one("SELECT * FROM devices WHERE device_no=?", (device_no,))
        if device is None:
            return {
                "ok": False,
                "message": f"设备 {device_no} 不存在",
                "recognition": "error",
                "authorization": "block",
                "auth_reason": "设备不存在",
            }
        dev_id = device["id"]

        rec = self.face.recognize(image_b64)
        result: dict[str, Any] = {
            "ok": True,
            "message": rec["message"],
            "recognition": rec["recognition"],
            "confidence": rec.get("confidence"),
            "device_online": self.bridge.online,
            "device_lock_state": self.bridge.lock_state,
            "authorization": "none",
            "auth_reason": "",
            "request_id": None,
            "execution": None,
            "raw": None,
        }

        if rec["recognition"] == "no_face":
            result["authorization"] = "block"
            result["auth_reason"] = "未检测到人脸，不执行开门"
            self._record_event(dev_id, None, rec, result)
            return result
        if rec["recognition"] == "multiple":
            result["authorization"] = "block"
            result["auth_reason"] = "检测到多张人脸，不执行开门"
            self._record_event(dev_id, None, rec, result)
            return result
        if rec["recognition"] == "error":
            result["authorization"] = "block"
            result["auth_reason"] = rec["message"]
            self._record_event(dev_id, None, rec, result)
            return result
        if rec["recognition"] == "unknown":
            result["authorization"] = "block"
            result["auth_reason"] = "未知人员，拒绝通行"
            self._record_event(dev_id, None, rec, result)
            self._maybe_alert_unknown()
            return result

        # ----- 已识别 -----
        user = rec["user"]
        result["recognized_no"] = user["user_no"]
        result["recognized_name"] = user["name"]
        if not user["enabled"]:
            result["authorization"] = "deny"
            result["auth_reason"] = "人员已停用"
            self._maybe_send_deny(user["id"], dev_id, result)
            self._record_event(dev_id, user["id"], rec, result)
            return result

        perm = self._valid_permission(user["id"], dev_id)
        if perm is None:
            result["authorization"] = "deny"
            result["auth_reason"] = "该人员对此门无有效权限（时段外或未配置）"
            self._maybe_send_deny(user["id"], dev_id, result)
            self._record_event(dev_id, user["id"], rec, result)
            return result

        if not self.bridge.online:
            result["authorization"] = "block"
            result["auth_reason"] = "设备离线，授权结果已保存但无法执行"
            result["execution"] = "unconfirmed"
            self._record_event(dev_id, user["id"], rec, result)
            self._add_alert("unconfirmed", "warning", f"{user['name']} 已授权但设备离线，执行未确认")
            return result

        # 节流：门未锁定时不重复发 allow
        if self.bridge.lock_state != "LOCKED":
            result["authorization"] = "allow"
            result["auth_reason"] = "已授权（门锁处理中，不重复触发）"
            result["execution"] = "pending"
            self._record_event(dev_id, user["id"], rec, result)
            return result
        with self._lock:
            last = self._last_allow.get(user["id"], 0.0)
            if time.monotonic() - last < settings.allow_min_interval:
                result["authorization"] = "allow"
                result["auth_reason"] = "已授权（触发间隔内，不重复开门）"
                result["execution"] = "pending"
                self._record_event(dev_id, user["id"], rec, result)
                return result
            self._last_allow[user["id"]] = time.monotonic()

        frame = self.bridge.send_command("allow")
        if frame is None:
            result["authorization"] = "allow"
            result["auth_reason"] = "已授权，但未收到板端确认"
            result["execution"] = "timeout"
            result["message"] += "（发送 Door allow 无响应）"
            self._record_event(dev_id, user["id"], rec, result)
            self._add_alert("unconfirmed", "warning", f"{user['name']} 的开门命令未获板端确认")
            return result

        result["request_id"] = frame.lower_id
        if frame.kind == "ACK" and "ALLOW" in frame.payload:
            result["authorization"] = "allow"
            result["auth_reason"] = "已授权，板端已确认模拟解锁"
            result["execution"] = "confirmed"
            result["device_lock_state"] = "UNLOCKED"
            result["raw"] = frame.describe()
        elif frame.kind == "ERR":
            result["authorization"] = "deny"
            result["auth_reason"] = f"板端拒绝: {frame.payload}"
            result["execution"] = "error"
            result["raw"] = frame.describe()
        else:
            result["authorization"] = "allow"
            result["auth_reason"] = "已授权，等待板端确认"
            result["execution"] = "pending"
            result["raw"] = frame.describe()
        self._record_event(dev_id, user["id"], rec, result)
        return result

    # ---------- 辅助 ----------
    def _valid_permission(self, user_id: int, device_id: int) -> Optional[dict]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return db.fetch_one(
            """SELECT * FROM permissions
               WHERE user_id=? AND device_id=? AND enabled=1
                 AND valid_from <= ? AND valid_to >= ?""",
            (user_id, device_id, now, now),
        )

    def _maybe_send_deny(self, user_id: int, device_id: int, result: dict) -> None:
        if not self.bridge.online:
            result["execution"] = "unconfirmed"
            result["raw"] = None
            return
        with self._lock:
            last = self._last_deny.get(user_id, 0.0)
            if time.monotonic() - last < settings.deny_min_interval:
                result["execution"] = "not_sent"
                result["auth_reason"] += "（节流：不重复发送 deny）"
                return
            self._last_deny[user_id] = time.monotonic()
        frame = self.bridge.send_command("deny")
        if frame is None:
            result["execution"] = "timeout"
            result["auth_reason"] += "（deny 无响应）"
        elif frame.kind == "ACK" and "DENY" in frame.payload:
            result["execution"] = "confirmed"
            result["request_id"] = frame.lower_id
            result["raw"] = frame.describe()
            result["device_lock_state"] = "LOCKED"
        elif frame.kind == "ERR":
            result["execution"] = "error"
            result["request_id"] = frame.lower_id
            result["raw"] = frame.describe()

    def _record_event(
        self, device_id: int, user_id: Optional[int], rec: dict, result: dict
    ) -> None:
        event_id = db.execute(
            """INSERT INTO access_events
               (user_id, device_id, recognition, confidence, recognized_no, recognized_name,
                authorization, auth_reason, request_id, execution, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                device_id,
                rec["recognition"],
                rec.get("confidence"),
                result.get("recognized_no"),
                result.get("recognized_name"),
                result["authorization"],
                result["auth_reason"],
                result.get("request_id"),
                result.get("execution"),
                result.get("raw"),
            ),
        )
        result["event_id"] = event_id

    def _add_alert(self, alert_type: str, level: str, message: str, user_id: Optional[int] = None) -> None:
        db.execute(
            "INSERT INTO alerts(type, level, user_id, message) VALUES (?,?,?,?)",
            (alert_type, level, user_id, message),
        )

    def _resolve_alert_type(self, alert_type: str) -> None:
        db.execute(
            "UPDATE alerts SET resolved=1 WHERE type=? AND resolved=0", (alert_type,)
        )

    def _recent_count(self, alert_type: str, seconds: float) -> int:
        row = db.fetch_one(
            """SELECT COUNT(*) AS c FROM alerts
               WHERE type=? AND resolved=0
                 AND alert_time >= datetime('now','localtime', ?)""",
            (alert_type, f"-{int(seconds)} seconds"),
        )
        return row["c"] if row else 0

    def _maybe_alert_unknown(self) -> None:
        if self._recent_count("unknown_person", settings.failure_window_seconds) > 0:
            return  # 已有未处理告警，去重
        self._add_alert("unknown_person", "warning", "检测到未知人员尝试通行")

    def maybe_alert_repeated_failure(self) -> None:
        """连续失败告警：最近 60 秒内未知/无权限次数 ≥ 3。"""
        row = db.fetch_one(
            """SELECT COUNT(*) AS c FROM access_events
               WHERE (recognition='unknown' OR authorization='deny')
                 AND event_time >= datetime('now','localtime', ?)""",
            (f"-{int(settings.failure_window_seconds)} seconds",),
        )
        if row and row["c"] >= settings.failure_count_alert:
            if self._recent_count("repeated_failure", settings.failure_window_seconds) == 0:
                self._add_alert(
                    "repeated_failure", "warning", f"最近 {int(settings.failure_window_seconds)} 秒内连续失败 {row['c']} 次"
                )
