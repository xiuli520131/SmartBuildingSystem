"""人脸注册、训练、校准与验证 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..config import settings
from ..schemas import (
    CalibrateOut,
    FaceStatusOut,
    SampleIn,
    SampleOut,
    TrainOut,
    ValidateIn,
)
from ..state import app_state

router = APIRouter(prefix="/api/faces", tags=["faces"])


@router.post("/sample", response_model=SampleOut)
def add_sample(body: SampleIn) -> SampleOut:
    if not db.fetch_one("SELECT id FROM users WHERE id=?", (body.user_id,)):
        raise HTTPException(status_code=404, detail="人员不存在")
    if len(body.image) > settings.max_frame_bytes:
        raise HTTPException(status_code=413, detail="图片过大")
    result = app_state.face.save_sample(body.user_id, body.image)
    out = SampleOut(
        ok=result["ok"],
        message=result["message"],
        user_id=body.user_id,
        sample_count=result.get("sample_count"),
        sample_path=result.get("sample_path"),
    )
    # 样本 ≥ 2 后自动增量训练，识别立即可用；校准仍建议手动执行
    if result["ok"] and result.get("sample_count", 0) >= 2:
        app_state.face.retrain()
    return out


@router.post("/train", response_model=TrainOut)
def train() -> TrainOut:
    result = app_state.face.retrain()
    return TrainOut(
        ok=result["ok"],
        message=result["message"],
        users=result.get("users", 0),
        samples=result.get("samples", 0),
        model_version=result.get("model_version"),
    )


@router.post("/calibrate", response_model=CalibrateOut)
def calibrate() -> CalibrateOut:
    result = app_state.face.calibrate()
    return CalibrateOut(
        ok=result["ok"],
        message=result["message"],
        details=result.get("details", []),
        summary=result.get("summary"),
    )


@router.post("/validate")
def validate_photo(body: ValidateIn) -> dict:
    """用一张未参与注册的照片验证识别阈值（误识/拒识检验）。"""
    if not db.fetch_one("SELECT id FROM users WHERE id=?", (body.user_id,)):
        raise HTTPException(status_code=404, detail="人员不存在")
    return app_state.face.validate_photo(body.user_id, body.image)


@router.get("/status", response_model=FaceStatusOut)
def face_status() -> FaceStatusOut:
    rows = db.fetch_all(
        """SELECT u.id, u.user_no, u.name, u.recognition_threshold,
                  (SELECT COUNT(*) FROM face_samples fs WHERE fs.user_id=u.id) AS sample_count
           FROM users u ORDER BY u.id"""
    )
    total = db.fetch_one("SELECT COUNT(*) AS c FROM face_samples")["c"]
    return FaceStatusOut(
        model_version=app_state.face.model_version,
        total_samples=total,
        users=[
            {
                "user_id": r["id"],
                "user_no": r["user_no"],
                "name": r["name"],
                "threshold": r["recognition_threshold"],
                "sample_count": r["sample_count"],
            }
            for r in rows
        ],
        default_threshold=settings.default_threshold,
    )
