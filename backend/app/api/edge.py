"""Board feature enrollment and monitoring. No arbitrary HTTP unlock endpoint."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from ..state import app_state

router = APIRouter(prefix="/api/edge", tags=["edge"])


class EnrollmentIn(BaseModel):
    user_id: int | None = None
    role: Literal["train", "validation", "unknown"] = "train"


class ImageEnrollmentIn(EnrollmentIn):
    image: str = Field(min_length=1, max_length=2_000_000)
    source: Literal["upload", "webcam"] = "upload"


@router.get("/status")
def status() -> dict:
    return app_state.edge.status()


@router.post("/enroll")
def enroll(body: EnrollmentIn) -> dict:
    try:
        return app_state.edge.enroll(body.user_id, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/calibrate")
def calibrate() -> dict:
    return app_state.edge.features.calibrate()


@router.post("/image-sample")
def image_sample(body: ImageEnrollmentIn) -> dict:
    try:
        return app_state.image_enrollment.enroll(body.user_id, body.role, body.image, body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
