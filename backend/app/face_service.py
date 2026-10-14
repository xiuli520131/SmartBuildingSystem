"""人脸检测、注册、LBPH 训练/识别与阈值校准。

检测：OpenCV Haar 级联（opencv 自带 haarcascade_frontalface_default.xml）。
识别：LBPHFaceRecognizer，距离越小越相似；阈值越小判定越严。
注册：必须恰好检测到一张人脸，裁剪→灰度→统一尺寸（160x160）→落盘。
校准：对每个人员做留一法自距离评估，阈值 = 自距离最大值 + 余量，
     同时给出与他人最近距离作为交叉检验；单样本人员沿用全局默认阈值。
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from . import database as db
from .config import settings

_FACE_W, _FACE_H = settings.face_size
_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


class FaceService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cascade = cv2.CascadeClassifier(_CASCADE_PATH)
        if self._cascade.empty():
            raise RuntimeError(f"Haar 级联加载失败: {_CASCADE_PATH}")
        self.model: Optional[cv2.face.LBPHFaceRecognizer] = None
        self.model_version: Optional[str] = None
        self._load_model()

    # ---------- 模型持久化 ----------
    def _load_model(self) -> None:
        if settings.model_path.exists():
            self.model = cv2.face.LBPHFaceRecognizer_create()
            self.model.read(str(settings.model_path))
        if settings.model_meta_path.exists():
            try:
                meta = json.loads(settings.model_meta_path.read_text(encoding="utf-8"))
                self.model_version = meta.get("version")
            except (json.JSONDecodeError, OSError):
                pass

    def _save_model(self) -> None:
        assert self.model is not None
        self.model.write(str(settings.model_path))
        self.model_version = datetime.now().strftime("%Y%m%d%H%M%S")
        meta = {
            "version": self.model_version,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "face_size": list(settings.face_size),
        }
        settings.model_meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 图像工具 ----------
    @staticmethod
    def decode_image(image_b64: str) -> np.ndarray:
        """base64 → BGR 图。容忍 data:image/...;base64, 前缀。"""
        text = image_b64.strip()
        if text.startswith("data:") and ";base64," in text:
            text = text.split(";base64,", 1)[1]
        raw = base64.b64decode(text)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("图片解码失败，请提交有效的 JPEG/PNG base64")
        return img

    def detect_faces(self, img: np.ndarray) -> list[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50)
        )
        return [tuple(int(v) for v in box) for box in faces]

    @staticmethod
    def preprocess_face(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
        x, y, w, h = box
        face = img[y : y + h, x : x + w]
        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        return cv2.resize(gray, (_FACE_W, _FACE_H))

    # ---------- 注册 ----------
    def save_sample(self, user_id: int, image_b64: str) -> dict[str, Any]:
        img = self.decode_image(image_b64)
        faces = self.detect_faces(img)
        if len(faces) == 0:
            return {"ok": False, "message": "未检测到人脸，请正对摄像头并保持光线充足"}
        if len(faces) > 1:
            return {"ok": False, "message": f"检测到 {len(faces)} 张人脸，注册时只允许恰好一张"}
        box = faces[0]
        face = self.preprocess_face(img, box)
        user_dir = settings.faces_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        path = user_dir / f"{stamp}.jpg"
        cv2.imwrite(str(path), face)
        db.execute(
            "INSERT INTO face_samples(user_id, path) VALUES (?,?)", (user_id, str(path))
        )
        count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM face_samples WHERE user_id=?", (user_id,)
        )["c"]
        return {
            "ok": True,
            "message": f"样本已保存（该人员现有 {count} 张）",
            "user_id": user_id,
            "sample_count": count,
            "sample_path": str(path),
            "face_box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
        }

    def list_samples(self, user_id: int) -> list[dict[str, Any]]:
        return db.fetch_all(
            "SELECT id, path, created_at FROM face_samples WHERE user_id=? ORDER BY id",
            (user_id,),
        )

    def delete_user_samples(self, user_id: int) -> int:
        rows = self.list_samples(user_id)
        for r in rows:
            try:
                Path(r["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        db.execute("DELETE FROM face_samples WHERE user_id=?", (user_id,))
        return len(rows)

    # ---------- 训练 ----------
    def retrain(self) -> dict[str, Any]:
        with self._lock:
            rows = db.fetch_all(
                """SELECT fs.id, fs.user_id, fs.path, u.user_no, u.name
                   FROM face_samples fs JOIN users u ON u.id = fs.user_id
                   ORDER BY fs.user_id, fs.id"""
            )
            if not rows:
                self.model = None
                self.model_version = None
                settings.model_path.unlink(missing_ok=True)
                return {"ok": True, "message": "没有样本，模型已清空", "users": 0, "samples": 0}

            images: list[np.ndarray] = []
            labels: list[int] = []
            skipped = 0
            for r in rows:
                img = cv2.imread(r["path"], cv2.IMREAD_GRAYSCALE)
                if img is None:
                    skipped += 1
                    continue
                img = cv2.resize(img, (_FACE_W, _FACE_H))
                images.append(img)
                labels.append(int(r["user_id"]))
            if not images:
                return {"ok": False, "message": "所有样本文件缺失或损坏，无法训练"}

            model = cv2.face.LBPHFaceRecognizer_create()
            model.train(images, np.array(labels, dtype=np.int32))
            self.model = model
            self._save_model()
            users = db.fetch_all("SELECT COUNT(*) AS c FROM users WHERE id IN (SELECT DISTINCT user_id FROM face_samples)")[0]["c"]
            return {
                "ok": True,
                "message": f"训练完成（{len(images)} 张样本，跳过 {skipped} 张损坏）",
                "users": users,
                "samples": len(images),
                "model_version": self.model_version,
            }

    # ---------- 识别 ----------
    def recognize(self, image_b64: str) -> dict[str, Any]:
        """返回 {recognition, confidence, user(或 None), faces, message}。"""
        try:
            img = self.decode_image(image_b64)
        except ValueError as exc:
            return {"recognition": "error", "message": str(exc), "faces": 0}
        faces = self.detect_faces(img)
        if len(faces) == 0:
            return {"recognition": "no_face", "message": "未检测到人脸", "faces": 0}
        if len(faces) > 1:
            return {
                "recognition": "multiple",
                "message": f"检测到 {len(faces)} 张人脸，不执行开门",
                "faces": len(faces),
            }
        if self.model is None:
            return {
                "recognition": "unknown",
                "message": "模型尚未训练，请先注册并训练",
                "faces": 1,
                "confidence": None,
            }
        box = max(faces, key=lambda b: b[2] * b[3])
        face = self.preprocess_face(img, box)
        label, conf = self.model.predict(face)
        user = db.fetch_one(
            "SELECT id, user_no, name, enabled, recognition_threshold FROM users WHERE id=?",
            (int(label),),
        )
        threshold = (user or {}).get("recognition_threshold") or settings.default_threshold
        if user is not None and float(conf) <= threshold:
            return {
                "recognition": "recognized",
                "confidence": float(conf),
                "user": user,
                "faces": 1,
                "message": f"识别为 {user['name']}（距离 {conf:.1f} ≤ 阈值 {threshold:.1f}）",
                "threshold": threshold,
            }
        return {
            "recognition": "unknown",
            "confidence": float(conf),
            "user": None,
            "faces": 1,
            "message": f"未知人员（距离 {conf:.1f} > 阈值 {threshold:.1f}）",
            "threshold": threshold,
        }

    # ---------- 阈值校准（留一法） ----------
    def calibrate(self) -> dict[str, Any]:
        with self._lock:
            users = db.fetch_all(
                """SELECT u.id, u.user_no, u.name, u.recognition_threshold,
                          (SELECT COUNT(*) FROM face_samples fs WHERE fs.user_id=u.id) AS sample_count
                   FROM users u ORDER BY u.id"""
            )
            details: list[dict[str, Any]] = []
            rows = db.fetch_all(
                "SELECT id, user_id, path FROM face_samples ORDER BY user_id, id"
            )
            if not rows:
                return {"ok": False, "message": "没有样本，无法校准", "details": [], "summary": None}

            by_user: dict[int, list[dict[str, Any]]] = {}
            for r in rows:
                by_user.setdefault(r["user_id"], []).append(r)

            warn_cross = 0
            for u in users:
                samples = by_user.get(u["id"], [])
                entry: dict[str, Any] = {
                    "user_id": u["id"],
                    "user_no": u["user_no"],
                    "name": u["name"],
                    "sample_count": len(samples),
                }
                if len(samples) < 2:
                    threshold = settings.default_threshold
                    entry.update(
                        {
                            "threshold": threshold,
                            "note": "样本不足 2 张，沿用全局默认阈值",
                            "own_max": None,
                            "cross_min": None,
                        }
                    )
                else:
                    # 逐张留一：用其余全部样本训练，预测被留出的样本
                    own_dist: list[float] = []
                    cross_dist: list[float] = []
                    others = [s for uid, ss in by_user.items() for s in ss]
                    for held in samples:
                        train = [s for s in others if s["id"] != held["id"]]
                        imgs = [cv2.imread(s["path"], cv2.IMREAD_GRAYSCALE) for s in train]
                        imgs = [cv2.resize(i, (_FACE_W, _FACE_H)) for i in imgs if i is not None]
                        if not imgs:
                            continue
                        lab = [int(s["user_id"]) for s in train[: len(imgs)]]
                        m = cv2.face.LBPHFaceRecognizer_create()
                        m.train(imgs, np.array(lab, dtype=np.int32))
                        test = cv2.imread(held["path"], cv2.IMREAD_GRAYSCALE)
                        if test is None:
                            continue
                        test = cv2.resize(test, (_FACE_W, _FACE_H))
                        pred_label, dist = m.predict(test)
                        if int(pred_label) == u["id"]:
                            own_dist.append(float(dist))
                        else:
                            cross_dist.append(float(dist))
                    if not own_dist:
                        threshold = settings.default_threshold
                        entry.update(
                            {
                                "threshold": threshold,
                                "note": "留一法评估失败（样本缺失），沿用默认阈值",
                            }
                        )
                    else:
                        own_max = max(own_dist)
                        cross_min = min(cross_dist) if cross_dist else None
                        threshold = max(own_max + settings.calibrate_margin, 40.0)
                        if cross_min is not None and cross_min < threshold:
                            warn_cross += 1
                            entry["warning"] = (
                                f"与他人最近距离 {cross_min:.1f} < 阈值 {threshold:.1f}，"
                                "存在误识风险，建议补充不同角度的样本后重新校准"
                            )
                        entry.update(
                            {
                                "threshold": round(threshold, 2),
                                "own_max": round(own_max, 2),
                                "own_mean": round(sum(own_dist) / len(own_dist), 2),
                                "cross_min": round(cross_min, 2) if cross_min else None,
                                "note": "留一法校准",
                            }
                        )
                db.execute(
                    "UPDATE users SET recognition_threshold=? WHERE id=?",
                    (entry["threshold"], u["id"]),
                )
                details.append(entry)

            trained = self.retrain()
            summary = {
                "users_calibrated": len(details),
                "cross_risk_users": warn_cross,
                "model_version": trained.get("model_version"),
            }
            return {
                "ok": True,
                "message": f"校准完成：{len(details)} 个人员，{warn_cross} 个存在交叉风险",
                "details": details,
                "summary": summary,
            }

    # ---------- 未注册照片验证（识别验证） ----------
    def validate_photo(self, user_id: int, image_b64: str) -> dict[str, Any]:
        """用一张未参与注册的照片验证阈值是否有效。"""
        result = self.recognize(image_b64)
        if result["recognition"] == "recognized":
            matched = result["user"]["id"] == user_id
            verdict = "match" if matched else "wrong_person"
        else:
            verdict = "reject"
        return {
            "verdict": verdict,
            "recognition": result["recognition"],
            "confidence": result.get("confidence"),
            "expected_user_id": user_id,
            "message": result["message"],
        }
