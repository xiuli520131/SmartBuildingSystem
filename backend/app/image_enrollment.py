"""Photo/webcam enrollment and local recognition using the firmware feature contract.

不依赖串口：解码浏览器/照片帧 → Haar 单脸检测 → 与板端一致的整数灰度与质量门
→ 提取 lbph4-v1 特征。同一份预处理既用于录入（add_sample），也用于电脑摄像头
实时识别（features.recognize）。
"""
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

    # ---------- 公共预处理：解码 → 单脸检测 → 灰度 → 质量门 ----------
    def _prepare_face(self, image: str) -> dict:
        """把一张 base64 图片处理成 160×160 整数灰度 ROI。

        返回 dict：ok/status/message，成功时附 normalized/bgr/width/height/face_box。
        status: ok | no_face | multiple | rejected | error
        """
        if len(image) > settings.max_frame_bytes:
            return {"ok": False, "status": "error", "message": "图片过大，请缩小后重试"}
        text = image.strip()
        if text.startswith("data:") and ";base64," in text:
            text = text.split(";base64,", 1)[1]
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            return {"ok": False, "status": "error", "message": "图片编码无效，请重新选择 JPEG、PNG 或 WebP 照片"}
        if not data:
            return {"ok": False, "status": "error", "message": "图片为空"}
        try:
            bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            return {"ok": False, "status": "error", "message": "图片无法解码，请重新导出为 JPEG 或 PNG"}
        if bgr is None:
            return {"ok": False, "status": "error", "message": "图片无法解码，请重新选择有效照片"}
        height, width = bgr.shape[:2]
        if width * height > 12_000_000 or min(height, width) < 60:
            return {"ok": False, "status": "rejected", "message": "图片尺寸不合适：至少 60×60，且不超过 1200 万像素"}

        # Haar detects a box; extraction uses the board's integer grayscale formula.
        scale = min(1.0, 1280 / max(height, width))
        small = cv2.resize(bgr, (round(width * scale), round(height * scale))) if scale < 1 else bgr
        detection_gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        with self._lock:
            faces = self._cascade.detectMultiScale(detection_gray, scaleFactor=1.1,
                                                   minNeighbors=5, minSize=(50, 50))
        if len(faces) == 0:
            return {"ok": False, "status": "no_face", "message": "未检测到正脸，请选择无遮挡、正立且光线充足的照片"}
        if len(faces) != 1:
            return {"ok": False, "status": "multiple", "message": f"检测到 {len(faces)} 张人脸，请使用只有一人的照片"}
        sx, sy, sw, sh = (int(v) for v in faces[0])
        x, y = round(sx * width / small.shape[1]), round(sy * height / small.shape[0])
        x2 = min(width, round((sx + sw) * width / small.shape[1]))
        y2 = min(height, round((sy + sh) * height / small.shape[0]))
        w, h = x2 - x, y2 - y
        if min(w, h) < 60:
            return {"ok": False, "status": "rejected", "message": "人脸太小，请靠近摄像头或选择脸部更清楚的照片"}
        roi = bgr[y:y2, x:x2][(np.arange(160) * h // 160)[:, None],
                              (np.arange(160) * w // 160)[None, :]].astype(np.uint32)
        normalized = ((77 * roi[:, :, 2] + 150 * roi[:, :, 1] + 29 * roi[:, :, 0]) >> 8).astype(np.uint8)
        total = int(normalized.sum())
        clipped = int(((normalized < 10) | (normalized > 245)).sum())
        gradient = int(np.abs(np.diff(normalized.astype(np.int16), axis=1)).sum())
        if total < 35 * 160 * 160 or total > 220 * 160 * 160 or clipped > 160 * 160 // 3:
            return {"ok": False, "status": "rejected", "message": "脸部过暗、过亮或曝光不均，请调整光线后重新采集"}
        if gradient < 2 * 160 * 159:
            return {"ok": False, "status": "rejected", "message": "脸部细节不足，请选择清晰原图或靠近摄像头"}

        return {"ok": True, "status": "ok", "message": "ok", "normalized": normalized,
                "bgr": bgr, "width": width, "height": height,
                "face_box": {"x": x, "y": y, "w": w, "h": h}}

    def enroll(self, user_id: int | None, role: str, image: str, source: str) -> dict:
        prep = self._prepare_face(image)
        if not prep["ok"]:
            raise ValueError(prep["message"])
        digest = hashlib.sha256(
            f"{prep['width']}x{prep['height']}:".encode("ascii") + prep["bgr"].tobytes()
        ).hexdigest()
        result = self.features.add_sample(
            user_id, role, extract_gray(prep["normalized"]), source=source, image_sha256=digest
        )
        return {**result, "face_box": prep["face_box"],
                "image_width": prep["width"], "image_height": prep["height"]}

    def recognize(self, image: str) -> dict:
        """电脑摄像头/照片帧 → lbph4-v1 特征 → 板端特征模板比对。

        返回与 FeatureService.recognize 对齐的结构：
        recognition / user / confidence / distance / message / faces。
        """
        prep = self._prepare_face(image)
        if not prep["ok"]:
            recognition = {"no_face": "no_face", "multiple": "multiple"}.get(prep["status"], "error")
            return {"recognition": recognition, "user": None, "faces": 1,
                    "confidence": None, "distance": None, "message": prep["message"]}
        raw = extract_gray(prep["normalized"])
        rec = self.features.recognize(raw)
        rec["faces"] = 1
        rec["face_box"] = prep["face_box"]
        return rec
