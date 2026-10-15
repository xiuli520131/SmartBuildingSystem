"""Versioned board features; enrollment/calibration never shares legacy thresholds."""
from __future__ import annotations

import threading
from datetime import datetime

import cv2
import numpy as np

from . import database as db

ALGORITHM = "lbph4-v1"
PREPROCESS = "gray160-nn-eq-v1"
FEATURE_COUNT = 4 * 4 * 256
FEATURE_BYTES = FEATURE_COUNT * 4
MIN_TRAIN = 3
MIN_VALIDATION = 3
MIN_GAP = 5.0


def decode_feature(raw: bytes) -> np.ndarray:
    if len(raw) != FEATURE_BYTES:
        raise ValueError("特征长度必须为 16384 字节")
    feature = np.frombuffer(raw, dtype="<f4").copy()
    if not np.isfinite(feature).all() or (feature < 0).any() or (feature > 1).any():
        raise ValueError("特征含非有限值或超出归一化范围")
    if not np.allclose(feature.reshape(16, 256).sum(axis=1), 1, atol=1e-4):
        raise ValueError("LBPH 每个网格的直方图必须归一化")
    return feature


def preprocess_gray(gray: np.ndarray) -> np.ndarray:
    """Contract shared with C: nearest resize, then integer-rounded equalization."""
    if gray.ndim != 2 or gray.dtype != np.uint8 or gray.size == 0:
        raise ValueError("需要非空 uint8 灰度 ROI")
    height, width = gray.shape
    face = gray[(np.arange(160) * height // 160)[:, None],
                (np.arange(160) * width // 160)[None, :]]
    hist = np.bincount(face.ravel(), minlength=256)
    first = int(np.flatnonzero(hist)[0])
    denominator = face.size - int(hist[first])
    if denominator == 0:
        return face.copy()
    cumulative = np.cumsum(hist) - hist[first]
    lut = np.clip((cumulative * 255 + denominator // 2) // denominator, 0, 255).astype(np.uint8)
    return lut[face]


def extract_gray(gray: np.ndarray) -> bytes:
    face = preprocess_gray(gray)
    model = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=4, grid_y=4)
    model.train([face], np.array([1], dtype=np.int32))
    return model.getHistograms()[0].astype("<f4").tobytes()


def distance(left: np.ndarray, right: np.ndarray) -> float:
    return float(cv2.compareHist(left, right, cv2.HISTCMP_CHISQR_ALT))


class FeatureService:
    def __init__(self) -> None:
        self.lock = threading.RLock()

    def add_sample(self, user_id: int | None, role: str, raw: bytes,
                   source: str = "board", image_sha256: str | None = None) -> dict:
        if role not in ("train", "validation", "unknown"):
            raise ValueError("样本类型必须为 train/validation/unknown")
        if role != "unknown" and not db.fetch_one("SELECT id FROM users WHERE id=?", (user_id,)):
            raise ValueError("人员不存在")
        if role == "unknown":
            user_id = None
        if source not in ("board", "upload", "webcam"):
            raise ValueError("无效的样本来源")
        decode_feature(raw)
        with self.lock, db.get_conn() as conn:
            if role != "unknown" and not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("人员不存在")
            # A duplicate may not appear in training and validation under any label.
            if conn.execute("SELECT id FROM edge_samples WHERE data=? LIMIT 1", (raw,)).fetchone():
                raise ValueError("重复特征，不能把同一样本用于训练和验证")
            if image_sha256 and conn.execute("SELECT id FROM edge_samples WHERE image_sha256=? LIMIT 1",
                                              (image_sha256,)).fetchone():
                raise ValueError("这张照片已录入，请使用另一张独立拍摄的照片")
            cursor = conn.execute("""INSERT INTO edge_samples
                (user_id, role, algorithm, data, source, image_sha256) VALUES (?,?,?,?,?,?)""",
                (user_id, role, ALGORITHM, raw, source, image_sha256))
            count = conn.execute("SELECT COUNT(*) FROM edge_samples WHERE user_id IS ? AND role=?",
                                 (user_id, role)).fetchone()[0]
            # Any data change invalidates every calibrated threshold: impostor distances change.
            conn.execute("DELETE FROM edge_thresholds")
            conn.commit()
        label = {"train": "训练模板", "validation": "独立验证", "unknown": "陌生人验证"}[role]
        return {"ok": True, "message": f"{label}已保存（现有 {count} 份），请完成独立验证并重新校准",
                "sample_id": cursor.lastrowid, "sample_count": count, "source": source, "role": role}

    def _samples(self) -> list[dict]:
        return db.fetch_all("SELECT * FROM edge_samples WHERE algorithm=? ORDER BY id", (ALGORITHM,))

    def status(self) -> dict:
        return {"algorithm": ALGORITHM, "preprocess": PREPROCESS, "feature_bytes": FEATURE_BYTES,
                "users": db.fetch_all("""SELECT u.id AS user_id, u.name, u.user_no,
                    (SELECT COUNT(*) FROM edge_samples s WHERE s.user_id=u.id AND role='train') AS train_count,
                    (SELECT COUNT(*) FROM edge_samples s WHERE s.user_id=u.id AND role='validation') AS validation_count,
                    t.threshold, t.enabled AS calibrated
                    FROM users u LEFT JOIN edge_thresholds t ON t.user_id=u.id ORDER BY u.id"""),
                "source_counts": db.fetch_all("""SELECT user_id, source, role, COUNT(*) AS count
                    FROM edge_samples GROUP BY user_id, source, role"""),
                "unknown_count": db.fetch_one("SELECT COUNT(*) AS c FROM edge_samples WHERE role='unknown'")["c"]}

    def calibrate(self) -> dict:
        with self.lock:
            rows = self._samples()
            vectors = [(r, decode_feature(r["data"])) for r in rows]
            details = []
            with db.get_conn() as conn:
                conn.execute("DELETE FROM edge_thresholds")
                for user in db.fetch_all("SELECT id, user_no, name FROM users ORDER BY id"):
                    uid = user["id"]
                    templates = [v for r, v in vectors if r["user_id"] == uid and r["role"] == "train"]
                    positives = [v for r, v in vectors if r["user_id"] == uid and r["role"] == "validation"]
                    negatives = [v for r, v in vectors if r["role"] in ("validation", "unknown") and r["user_id"] != uid]
                    entry = {"user_id": uid, "name": user["name"], "user_no": user["user_no"],
                             "train_count": len(templates), "validation_count": len(positives),
                             "negative_count": len(negatives), "enabled": False, "threshold": None}
                    if len(templates) < MIN_TRAIN or len(positives) < MIN_VALIDATION or not negatives:
                        entry["message"] = "至少 3 个训练、3 个独立验证特征，并需要其他身份/陌生人验证特征"
                    else:
                        own_max = max(min(distance(v, t) for t in templates) for v in positives)
                        cross_min = min(min(distance(v, t) for t in templates) for v in negatives)
                        entry.update(own_max=own_max, cross_min=cross_min)
                        if cross_min - own_max <= MIN_GAP:
                            entry["message"] = "本人/他人距离缺少安全间隔，保持未校准，不启用刷脸"
                        else:
                            threshold = own_max + (cross_min - own_max) / 2
                            entry.update(enabled=True, threshold=threshold, message="校准完成；仍需未参与调参的最终测试")
                            conn.execute("INSERT INTO edge_thresholds(user_id, algorithm, threshold, enabled, calibrated_at) VALUES (?,?,?,?,?)",
                                         (uid, ALGORITHM, threshold, 1, datetime.now().isoformat()))
                    details.append(entry)
                conn.commit()
            return {"ok": True, "message": "特征阈值校准完成", "details": details}

    def recognize(self, raw: bytes) -> dict:
        query = decode_feature(raw)
        with self.lock:
            rows = db.fetch_all("""SELECT s.user_id, s.data, u.id, u.user_no, u.name, u.enabled,
                t.threshold, t.enabled AS calibrated FROM edge_samples s
                JOIN users u ON u.id=s.user_id LEFT JOIN edge_thresholds t ON t.user_id=u.id
                WHERE s.role='train' AND s.algorithm=?""", (ALGORITHM,))
            nearest: dict[int, tuple[float, dict]] = {}
            for row in rows:
                score = distance(query, decode_feature(row["data"]))
                if row["user_id"] not in nearest or score < nearest[row["user_id"]][0]:
                    nearest[row["user_id"]] = (score, row)
            ranking = sorted(nearest.values(), key=lambda x: x[0])
            result = {"recognition": "unknown", "user": None, "faces": 1,
                      "confidence": None, "distance": None, "message": "没有板端训练模板"}
            if not ranking:
                return result
            best, user = ranking[0]
            result.update(confidence=best, distance=best, message="特征不满足身份判定条件")
            if not user["calibrated"] or user["threshold"] is None or best > user["threshold"]:
                return result
            if len(ranking) > 1 and ranking[1][0] - best < MIN_GAP:
                result["message"] = "两个身份距离接近，拒绝确认身份"
                return result
            result.update(recognition="recognized", user=user, threshold=user["threshold"],
                          message=f"识别为 {user['name']}（距离 {best:.1f}）")
            return result
