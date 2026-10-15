"""Photo/webcam enrollment using the firmware feature contract, without serial I/O."""
from __future__ import annotations

import base64
import binascii
import hashlib
import threading

import cv2
import numpy as np

from .config import settings
from .feature_service import FeatureService, extract_gray


class ImageEnrollmentService:
    def __init__(self, features: FeatureService) -> None:
        self.features = features
        self._cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self._cascade.empty():
            raise RuntimeError("照片录入的人脸检测器加载失败")
        self._lock = threading.Lock()

    def enroll(self, user_id: int | None, role: str, image: str, source: str) -> dict:
        if len(image) > settings.max_frame_bytes:
            raise ValueError("图片过大，请缩小后重试")
        text = image.strip()
        if text.startswith("data:") and ";base64," in text:
            text = text.split(";base64,", 1)[1]
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("图片编码无效，请重新选择 JPEG、PNG 或 WebP 照片") from exc
        if not data:
            raise ValueError("图片为空")
        try:
            bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError("图片无法解码，请重新导出为 JPEG 或 PNG") from exc
        if bgr is None:
            raise ValueError("图片无法解码，请重新选择有效照片")
        height, width = bgr.shape[:2]
        if width * height > 12_000_000 or min(height, width) < 60:
            raise ValueError("图片尺寸不合适：至少 60×60，且不超过 1200 万像素")

        # Haar detects a box; extraction uses the board's integer grayscale formula.
        scale = min(1.0, 1280 / max(height, width))
        small = cv2.resize(bgr, (round(width * scale), round(height * scale))) if scale < 1 else bgr
        detection_gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        with self._lock:
            faces = self._cascade.detectMultiScale(detection_gray, scaleFactor=1.1,
                                                   minNeighbors=5, minSize=(50, 50))
        if len(faces) == 0:
            raise ValueError("未检测到正脸，请选择无遮挡、正立且光线充足的照片")
        if len(faces) != 1:
            raise ValueError(f"检测到 {len(faces)} 张人脸，请使用只有一人的照片")
        sx, sy, sw, sh = (int(v) for v in faces[0])
        x, y = round(sx * width / small.shape[1]), round(sy * height / small.shape[0])
        x2 = min(width, round((sx + sw) * width / small.shape[1]))
        y2 = min(height, round((sy + sh) * height / small.shape[0]))
        w, h = x2 - x, y2 - y
        if min(w, h) < 60:
            raise ValueError("人脸太小，请靠近摄像头或选择脸部更清楚的照片")
        roi = bgr[y:y2, x:x2][(np.arange(160) * h // 160)[:, None],
                              (np.arange(160) * w // 160)[None, :]].astype(np.uint32)
        normalized = ((77 * roi[:, :, 2] + 150 * roi[:, :, 1] + 29 * roi[:, :, 0]) >> 8).astype(np.uint8)
        total = int(normalized.sum())
        clipped = int(((normalized < 10) | (normalized > 245)).sum())
        gradient = int(np.abs(np.diff(normalized.astype(np.int16), axis=1)).sum())
        if total < 35 * 160 * 160 or total > 220 * 160 * 160 or clipped > 160 * 160 // 3:
            raise ValueError("脸部过暗、过亮或曝光不均，请调整光线后重新采集")
        if gradient < 2 * 160 * 159:
            raise ValueError("脸部细节不足，请选择清晰原图或靠近摄像头")

        digest = hashlib.sha256(f"{width}x{height}:".encode("ascii") + bgr.tobytes()).hexdigest()
        result = self.features.add_sample(user_id, role, extract_gray(normalized), source=source, image_sha256=digest)
        return {**result, "face_box": {"x": x, "y": y, "w": w, "h": h},
                "image_width": width, "image_height": height}
