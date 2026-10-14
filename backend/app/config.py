"""全局配置：所有路径与可调参数集中在这里。

优先级：环境变量（.env）> 代码默认值。数据默认落在
C:\\SmartBuildingSystem\\smart-access\\data（可用 SMART_ACCESS_DATA 覆盖）。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 的上一级即 smart-access/
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # ---- 目录与文件 ----
    data_dir: Path = Path(_env_str("SMART_ACCESS_DATA", str(PROJECT_DIR / "data")))
    db_path: Path = data_dir / "smart_access.db"
    faces_dir: Path = data_dir / "faces"
    model_dir: Path = data_dir / "model"
    model_path: Path = model_dir / "face_model.yml"
    label_map_path: Path = model_dir / "label_map.json"
    model_meta_path: Path = model_dir / "model_meta.json"

    # ---- API ----
    host: str = _env_str("SMART_ACCESS_HOST", "127.0.0.1")
    port: int = _env_int("SMART_ACCESS_PORT", 8000)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- 设备与串口 ----
    default_device_no: str = "D001"
    bridge_mode: str = _env_str("SMART_ACCESS_BRIDGE_MODE", "mock").lower()
    serial_port: str = _env_str("SMART_ACCESS_SERIAL_PORT", "COM9")
    serial_baud: int = _env_int("SMART_ACCESS_SERIAL_BAUD", 115200)

    # ---- 桥接行为 ----
    heartbeat_interval: float = _env_float("SMART_ACCESS_HEARTBEAT_INTERVAL", 5.0)
    offline_after: float = _env_float("SMART_ACCESS_OFFLINE_AFTER", 15.0)
    cmd_timeout: float = _env_float("SMART_ACCESS_CMD_TIMEOUT", 2.0)
    reconnect_interval: float = 3.0
    # 允许尝试的换行符；首条 ping 会逐个探测直到收到 PONG
    line_ending_candidates: list[str] = ["\r\n", "\r", "\n"]

    # ---- 人脸 ----
    face_size: tuple[int, int] = (160, 160)
    default_threshold: float = _env_float("SMART_ACCESS_DEFAULT_THRESHOLD", 80.0)
    calibrate_margin: float = 15.0  # 阈值校准：留一法最大距离 + 余量
    # 连续失败（未知/无权限）计数窗口，用于告警去重
    failure_window_seconds: float = 60.0
    failure_count_alert: int = 3

    # 前端提交帧的最大字节数（base64），防止超大报文
    max_frame_bytes: int = 2_000_000
    # 允许开门的最小间隔（秒），防止同一人重复触发
    allow_min_interval: float = 3.0
    # deny 命令的发送最小间隔（秒）
    deny_min_interval: float = 5.0

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.faces_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
