"""通行（帧提交）、记录查询与 CSV 导出 API。"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Query, Response

from .. import database as db
from ..config import settings
from ..schemas import EventOut, FrameIn
from ..state import app_state

router = APIRouter(prefix="/api/access", tags=["access"])


@router.post("/frame")
def process_frame(body: FrameIn) -> dict:
    if len(body.image) > settings.max_frame_bytes:
        return {
            "ok": False,
            "message": "帧过大",
            "recognition": "error",
            "authorization": "block",
            "auth_reason": "帧超过大小限制",
        }
    result = app_state.access.process_frame(body.image, body.device_no)
    app_state.access.maybe_alert_repeated_failure()
    return result


def _event_out(row: dict) -> EventOut:
    user = None
    if row["user_id"]:
        user = db.fetch_one("SELECT user_no, name FROM users WHERE id=?", (row["user_id"],)) or {}
    dev = None
    if row["device_id"]:
        dev = db.fetch_one(
            "SELECT device_no FROM devices WHERE id=?", (row["device_id"],)
        ) or {}
    return EventOut(
        id=row["id"],
        event_time=row["event_time"],
        user_id=row["user_id"],
        user_no=(user or {}).get("user_no"),
        user_name=(user or {}).get("name"),
        device_no=(dev or {}).get("device_no"),
        recognition=row["recognition"],
        confidence=row["confidence"],
        authorization=row["authorization"],
        auth_reason=row["auth_reason"],
        request_id=row["request_id"],
        execution=row["execution"],
        raw=row["raw"],
    )


@router.get("/events", response_model=list[EventOut])
def list_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None),
    authorization: Optional[str] = Query(None),
    recognition: Optional[str] = Query(None),
) -> list[EventOut]:
    sql = "SELECT * FROM access_events WHERE 1=1"
    params: list = []
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    if authorization:
        sql += " AND authorization=?"
        params.append(authorization)
    if recognition:
        sql += " AND recognition=?"
        params.append(recognition)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_event_out(r) for r in db.fetch_all(sql, params)]


@router.get("/events/export")
def export_events_csv() -> Response:
    rows = db.fetch_all(
        """SELECT e.id, e.event_time,
                  COALESCE(u.user_no,'') AS user_no, COALESCE(u.name,'') AS user_name,
                  COALESCE(d.device_no,'') AS device_no,
                  e.recognition, e.confidence, e.authorization, e.auth_reason,
                  COALESCE(e.request_id,'') AS request_id, COALESCE(e.execution,'') AS execution,
                  COALESCE(e.raw,'') AS raw
           FROM access_events e
           LEFT JOIN users u ON u.id = e.user_id
           LEFT JOIN devices d ON d.id = e.device_id
           ORDER BY e.id"""
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["ID", "时间", "人员编号", "姓名", "设备", "识别结果", "置信度",
         "授权结果", "原因", "请求ID", "执行状态", "板端原始帧"]
    )
    for r in rows:
        writer.writerow(
            [r["id"], r["event_time"], r["user_no"], r["user_name"], r["device_no"],
             r["recognition"], r["confidence"], r["authorization"], r["auth_reason"],
             r["request_id"], r["execution"], r["raw"]]
        )
    # utf-8-sig 保证 Excel 直接打开不乱码
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=access_events.csv"},
    )


@router.get("/summary")
def summary() -> dict:
    today = "datetime('now','localtime','start of day')"
    counts = {
        "today_events": db.fetch_one(
            f"SELECT COUNT(*) AS c FROM access_events WHERE event_time >= {today}"
        )["c"],
        "today_allow": db.fetch_one(
            f"SELECT COUNT(*) AS c FROM access_events WHERE authorization='allow' AND event_time >= {today}"
        )["c"],
        "today_deny": db.fetch_one(
            f"SELECT COUNT(*) AS c FROM access_events WHERE authorization='deny' AND event_time >= {today}"
        )["c"],
        "today_unknown": db.fetch_one(
            f"SELECT COUNT(*) AS c FROM access_events WHERE recognition='unknown' AND event_time >= {today}"
        )["c"],
        "unresolved_alerts": db.fetch_one(
            "SELECT COUNT(*) AS c FROM alerts WHERE resolved=0"
        )["c"],
        "total_users": db.fetch_one("SELECT COUNT(*) AS c FROM users")["c"],
        "total_samples": db.fetch_one("SELECT COUNT(*) AS c FROM face_samples")["c"],
    }
    s = app_state.bridge.status()
    counts["device"] = s
    return counts
