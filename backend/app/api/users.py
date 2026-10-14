"""人员管理 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..schemas import UserCreate, UserOut, UserUpdate
from ..state import app_state

router = APIRouter(prefix="/api/users", tags=["users"])


def _to_out(row: dict) -> UserOut:
    count = db.fetch_one(
        "SELECT COUNT(*) AS c FROM face_samples WHERE user_id=?", (row["id"],)
    )["c"]
    return UserOut(
        id=row["id"],
        user_no=row["user_no"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        recognition_threshold=row["recognition_threshold"],
        created_at=row["created_at"],
        sample_count=count,
    )


@router.get("", response_model=list[UserOut])
def list_users() -> list[UserOut]:
    rows = db.fetch_all("SELECT * FROM users ORDER BY id")
    return [_to_out(r) for r in rows]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate) -> UserOut:
    exists = db.fetch_one("SELECT id FROM users WHERE user_no=?", (body.user_no,))
    if exists:
        raise HTTPException(status_code=409, detail=f"人员编号 {body.user_no} 已存在")
    uid = db.execute(
        "INSERT INTO users(user_no, name, enabled) VALUES (?,?,?)",
        (body.user_no, body.name, 1 if body.enabled else 0),
    )
    row = db.fetch_one("SELECT * FROM users WHERE id=?", (uid,))
    return _to_out(row)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int) -> UserOut:
    row = db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    return _to_out(row)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate) -> UserOut:
    row = db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    if body.name is not None:
        db.execute("UPDATE users SET name=? WHERE id=?", (body.name, user_id))
    if body.enabled is not None:
        db.execute("UPDATE users SET enabled=? WHERE id=?", (1 if body.enabled else 0, user_id))
    return _to_out(db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,)))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int) -> None:
    row = db.fetch_one("SELECT id FROM users WHERE id=?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    # 删除样本文件与数据库记录，随后重训模型
    removed = app_state.face.delete_user_samples(user_id)
    db.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    if removed:
        app_state.face.retrain()


@router.get("/{user_id}/samples")
def list_samples(user_id: int) -> list[dict]:
    row = db.fetch_one("SELECT id FROM users WHERE id=?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    return app_state.face.list_samples(user_id)
