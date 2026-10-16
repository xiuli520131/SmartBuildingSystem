"""运行时服务单例容器：main 在 lifespan 中装配，路由从这里取。"""
from __future__ import annotations

from typing import Optional

from .config import Settings


class AppState:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.face = None  # FaceService
        self.bridge = None  # SerialBridge
        self.access = None  # AccessService
        self.edge = None
        self.image_enrollment = None
        self.device_id: Optional[int] = None


app_state = AppState()
