"""权限（人员 × 门 × 有效时段）API。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import database as db
from ..schemas import PermissionCreate, PermissionOut, PermissionUpdate
from ..state import app_state

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _to_out(row: dict) -> PermissionOut:
    user = db.fetch_one("SELECT user_no, name FROM users WHERE id=?", (row["user_id"],)) or {}
    dev = db.fetch_one(
        "SELECT device_no, name FROM devices WHERE id=?", (row["device_id"],)
    ) or {}
    return PermissionOut(
        id=row["id"],
        user_id=row["user_id"],
        device_id=row["device_id"],
        device_no=dev.get("device_no", ""),
        device_name=dev.get("name", ""),
        user_no=user.get("user_no", ""),
        user_name=user.get("name", ""),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
    )


def _validate_time(valid_from: str, valid_to: str) -> None:
    try:
        f = datetime.strptime(valid_from, "%Y-%m-%d %H:%M")
        t = datetime.strptime(valid_to, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(status_code=422, detail="时间格式应为 YYYY-MM-DD HH:MM")
    if f >= t:
        raise HTTPException(status_code=422, detail="valid_from 必须早于 valid_to")


@router.get("", response_model=list[PermissionOut])
def list_permissions(
    user_id: Optional[int] = Query(None),
    device_id: Optional[int] = Query(None),
) -> list[PermissionOut]:
    sql = "SELECT * FROM permissions WHERE 1=1"
    params: list = []
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    if device_id is not None:
        sql += " AND device_id=?"
        params.append(device_id)
    sql += " ORDER BY id DESC"
    return [_to_out(r) for r in db.fetch_all(sql, params)]


@router.post("", response_model=PermissionOut, status_code=201)
def create_permission(body: PermissionCreate) -> PermissionOut:
    if not db.fetch_one("SELECT id FROM users WHERE id=?", (body.user_id,)):
        raise HTTPException(status_code=404, detail="人员不存在")
    if not db.fetch_one("SELECT id FROM devices WHERE id=?", (body.device_id,)):
        raise HTTPException(status_code=404, detail="设备不存在")
    _validate_time(body.valid_from, body.valid_to)
    pid = db.execute(
        """INSERT INTO permissions(user_id, device_id, valid_from, valid_to, enabled)
           VALUES (?,?,?,?,?)""",
        (body.user_id, body.device_id, body.valid_from, body.valid_to, 1 if body.enabled else 0),
    )
    return _to_out(db.fetch_one("SELECT * FROM permissions WHERE id=?", (pid,)))


@router.put("/{perm_id}", response_model=PermissionOut)
def update_permission(perm_id: int, body: PermissionUpdate) -> PermissionOut:
    row = db.fetch_one("SELECT * FROM permissions WHERE id=?", (perm_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="权限不存在")
    new_from = body.valid_from or row["valid_from"]
    new_to = body.valid_to or row["valid_to"]
    _validate_time(new_from, new_to)
    if body.valid_from is not None:
        db.execute("UPDATE permissions SET valid_from=? WHERE id=?", (new_from, perm_id))
    if body.valid_to is not None:
        db.execute("UPDATE permissions SET valid_to=? WHERE id=?", (new_to, perm_id))
    if body.enabled is not None:
        db.execute("UPDATE permissions SET enabled=? WHERE id=?", (1 if body.enabled else 0, perm_id))
    return _to_out(db.fetch_one("SELECT * FROM permissions WHERE id=?", (perm_id,)))


@router.delete("/{perm_id}", status_code=204)
def delete_permission(perm_id: int) -> None:
    row = db.fetch_one("SELECT id FROM permissions WHERE id=?", (perm_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="权限不存在")
    db.execute("DELETE FROM permissions WHERE id=?", (perm_id,))
