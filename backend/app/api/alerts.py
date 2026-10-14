"""告警 API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import database as db
from ..schemas import AlertOut, AlertUpdate

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _to_out(row: dict) -> AlertOut:
    user = None
    if row["user_id"]:
        user = db.fetch_one("SELECT name FROM users WHERE id=?", (row["user_id"],)) or {}
    return AlertOut(
        id=row["id"],
        alert_time=row["alert_time"],
        type=row["type"],
        level=row["level"],
        user_id=row["user_id"],
        user_name=(user or {}).get("name"),
        message=row["message"],
        resolved=bool(row["resolved"]),
    )


@router.get("", response_model=list[AlertOut])
def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    resolved: Optional[bool] = Query(None),
) -> list[AlertOut]:
    sql = "SELECT * FROM alerts"
    params: list = []
    if resolved is not None:
        sql += " WHERE resolved=?"
        params.append(1 if resolved else 0)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_to_out(r) for r in db.fetch_all(sql, params)]


@router.put("/{alert_id}", response_model=AlertOut)
def update_alert(alert_id: int, body: AlertUpdate) -> AlertOut:
    row = db.fetch_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    db.execute("UPDATE alerts SET resolved=? WHERE id=?", (1 if body.resolved else 0, alert_id))
    return _to_out(db.fetch_one("SELECT * FROM alerts WHERE id=?", (alert_id,)))
