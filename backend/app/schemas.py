"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------- 人员 ----------
class UserCreate(BaseModel):
    user_no: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    enabled: Optional[bool] = None


class UserOut(BaseModel):
    id: int
    user_no: str
    name: str
    enabled: bool
    recognition_threshold: Optional[float] = None
    created_at: str
    sample_count: int = 0


# ---------- 权限 ----------
class PermissionCreate(BaseModel):
    user_id: int
    device_id: int
    valid_from: str = Field(description="YYYY-MM-DD HH:MM")
    valid_to: str = Field(description="YYYY-MM-DD HH:MM")
    enabled: bool = True


class PermissionUpdate(BaseModel):
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    enabled: Optional[bool] = None


class PermissionOut(BaseModel):
    id: int
    user_id: int
    device_id: int
    device_no: str = ""
    device_name: str = ""
    user_no: str = ""
    user_name: str = ""
    valid_from: str
    valid_to: str
    enabled: bool
    created_at: str


# ---------- 设备 ----------
class DeviceCreate(BaseModel):
    device_no: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    com_port: Optional[str] = None
    baud: int = 115200


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    com_port: Optional[str] = None
    baud: Optional[int] = None


class DeviceOut(BaseModel):
    id: int
    device_no: str
    name: str
    com_port: Optional[str] = None
    baud: Optional[int] = None
    online: bool
    lock_state: str
    last_heartbeat: Optional[str] = None
    created_at: str


class BridgeModeIn(BaseModel):
    mode: str = Field(description="mock 或 serial")
    com_port: Optional[str] = None
    baud: Optional[int] = None

    @field_validator("mode")
    @classmethod
    def check_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("mock", "serial"):
            raise ValueError("mode 只允许 mock 或 serial")
        return v


class BridgeStatusOut(BaseModel):
    mode: str
    online: bool
    lock_state: str
    port: Optional[str] = None
    baud: Optional[int] = None
    serial_char_interval_ms: float = 0.0
    last_heartbeat: Optional[str] = None
    line_ending: str = ""
    last_error: str = ""
    feature_error: str = ""
    face_running: bool = False
    face_health: str = "UNCONNECTED"


# ---------- 人脸 ----------
class SampleIn(BaseModel):
    user_id: int
    image: str = Field(description="JPEG/PNG 的 base64 字符串（不含 data: 前缀）")

    @field_validator("image")
    @classmethod
    def check_image(cls, v: str) -> str:
        if not v:
            raise ValueError("image 为空")
        return v


class SampleOut(BaseModel):
    ok: bool
    message: str
    user_id: Optional[int] = None
    sample_count: Optional[int] = None
    sample_path: Optional[str] = None


class TrainOut(BaseModel):
    ok: bool
    message: str
    users: int = 0
    samples: int = 0
    model_version: Optional[str] = None


class ValidateIn(BaseModel):
    user_id: int
    image: str = Field(description="未参与注册的验证照片 base64")


class CalibrateOut(BaseModel):
    ok: bool
    message: str
    details: list[dict] = []
    summary: Optional[dict] = None


class FaceStatusOut(BaseModel):
    model_version: Optional[str] = None
    total_samples: int = 0
    users: list[dict] = []
    default_threshold: float


# ---------- 通行 ----------
class FrameIn(BaseModel):
    image: str = Field(description="当前视频帧 base64")
    device_no: str = "D001"


class FrameOut(BaseModel):
    ok: bool
    message: str
    recognition: str = ""          # no_face|multiple|unknown|recognized|error
    confidence: Optional[float] = None
    recognized_no: Optional[str] = None
    recognized_name: Optional[str] = None
    authorization: str = ""        # allow|deny|block|none
    auth_reason: str = ""
    request_id: Optional[str] = None
    execution: Optional[str] = None
    raw: Optional[str] = None
    device_online: bool = False
    device_lock_state: str = ""
    event_id: Optional[int] = None


class EventOut(BaseModel):
    id: int
    event_time: str
    user_id: Optional[int] = None
    user_no: Optional[str] = None
    user_name: Optional[str] = None
    device_no: Optional[str] = None
    recognition: str
    confidence: Optional[float] = None
    authorization: str
    auth_reason: str
    request_id: Optional[str] = None
    execution: Optional[str] = None
    raw: Optional[str] = None


# ---------- 告警 ----------
class AlertOut(BaseModel):
    id: int
    alert_time: str
    type: str
    level: str
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    message: str
    resolved: bool


class AlertUpdate(BaseModel):
    resolved: bool = True
