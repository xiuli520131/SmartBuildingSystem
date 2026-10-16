"""FastAPI 入口：装配服务、CORS、路由，可选托管前端构建产物。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
或：python -m app.main（本机演示便捷方式）
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import database as db
from .access_service import AccessService
from .api import access, alerts, devices, faces, permissions, users
from .config import settings
from .face_service import FaceService
from .serial_bridge import SerialBridge
from .state import app_state
from .feature_service import FeatureService
from .edge_access_service import EdgeAccessService
from .image_enrollment import ImageEnrollmentService
from .api import edge

logger = logging.getLogger("smart-access")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    app_state.settings = settings
    default = db.fetch_one(
        "SELECT id FROM devices WHERE device_no=?", (settings.default_device_no,)
    )
    app_state.device_id = default["id"] if default else None

    app_state.face = FaceService()
    app_state.bridge = SerialBridge(settings)
    app_state.bridge.device_id = app_state.device_id
    app_state.access = AccessService(app_state.face, app_state.bridge)
    app_state.bridge.on_board_frame = app_state.access.on_board_frame
    app_state.bridge.on_online_change = app_state.access.on_online_change
    app_state.edge = EdgeAccessService(FeatureService(), app_state.bridge, app_state.access)
    app_state.image_enrollment = ImageEnrollmentService(app_state.edge.features)
    app_state.bridge.on_feature = app_state.edge.enqueue
    app_state.bridge.on_feature_cancel = app_state.edge.cancel
    app_state.edge.start()

    # 初始设备状态写入数据库
    if app_state.device_id:
        db.execute(
            "UPDATE devices SET online=?, lock_state=? WHERE id=?",
            (1 if app_state.bridge.online else 0, app_state.bridge.lock_state, app_state.device_id),
        )
    app_state.bridge.start()
    logger.info(
        "Smart Access 后端已启动：mode=%s port=%s",
        app_state.bridge.mode,
        app_state.bridge.port,
    )
    yield
    app_state.edge.stop()
    app_state.bridge.stop()


app = FastAPI(title="Smart Access 后端", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(permissions.router)
app.include_router(devices.router)
app.include_router(faces.router)
app.include_router(access.router)
app.include_router(alerts.router)
app.include_router(edge.router)


@app.get("/api/health")
def health() -> dict:
    s = app_state.bridge.status() if app_state.bridge else {}
    return {
        "status": "ok",
        "mode": s.get("mode"),
        "online": s.get("online"),
        "lock_state": s.get("lock_state"),
        "model_version": app_state.face.model_version if app_state.face else None,
    }


# 若前端已构建（frontend/dist 存在），由本服务一并托管，便于演示
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(_dist / "index.html"))

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str) -> FileResponse:
        candidate = _dist / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_dist / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
