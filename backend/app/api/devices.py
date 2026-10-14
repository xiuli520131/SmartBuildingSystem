"""设备与桥接模式 API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import database as db
from ..schemas import BridgeModeIn, BridgeStatusOut, DeviceCreate, DeviceOut, DeviceUpdate
from ..state import app_state

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _to_out(row: dict) -> DeviceOut:
    return DeviceOut(
        id=row["id"],
        device_no=row["device_no"],
        name=row["name"],
        com_port=row["com_port"],
        baud=row["baud"],
        online=bool(row["online"]),
        lock_state=row["lock_state"],
        last_heartbeat=row["last_heartbeat"],
        created_at=row["created_at"],
    )


@router.get("", response_model=list[DeviceOut])
def list_devices() -> list[DeviceOut]:
    rows = db.fetch_all("SELECT * FROM devices ORDER BY id")
    return [_to_out(r) for r in rows]


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(body: DeviceCreate) -> DeviceOut:
    exists = db.fetch_one("SELECT id FROM devices WHERE device_no=?", (body.device_no,))
    if exists:
        raise HTTPException(status_code=409, detail=f"设备编号 {body.device_no} 已存在")
    did = db.execute(
        "INSERT INTO devices(device_no, name, com_port, baud) VALUES (?,?,?,?)",
        (body.device_no, body.name, body.com_port, body.baud),
    )
    return _to_out(db.fetch_one("SELECT * FROM devices WHERE id=?", (did,)))


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(device_id: int, body: DeviceUpdate) -> DeviceOut:
    row = db.fetch_one("SELECT * FROM devices WHERE id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    if body.name is not None:
        db.execute("UPDATE devices SET name=? WHERE id=?", (body.name, device_id))
    if body.com_port is not None:
        db.execute("UPDATE devices SET com_port=? WHERE id=?", (body.com_port, device_id))
    if body.baud is not None:
        db.execute("UPDATE devices SET baud=? WHERE id=?", (body.baud, device_id))
    return _to_out(db.fetch_one("SELECT * FROM devices WHERE id=?", (device_id,)))


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int) -> None:
    row = db.fetch_one("SELECT id FROM devices WHERE id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    if row["id"] == app_state.device_id:
        raise HTTPException(status_code=400, detail="默认门禁设备不允许删除")
    db.execute("DELETE FROM permissions WHERE device_id=?", (device_id,))
    db.execute("DELETE FROM devices WHERE id=?", (device_id,))


@router.get("/status", response_model=BridgeStatusOut)
def bridge_status() -> BridgeStatusOut:
    s = app_state.bridge.status()
    return BridgeStatusOut(**s)


@router.post("/mode", response_model=BridgeStatusOut)
def switch_mode(body: BridgeModeIn) -> BridgeStatusOut:
    app_state.bridge.set_mode(body.mode, body.com_port, body.baud)
    s = app_state.bridge.status()
    return BridgeStatusOut(**s)
