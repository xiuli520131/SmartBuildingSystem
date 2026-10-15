"""Bounded, ordered, CRC-checked ASCII transfer on the existing serial reader."""
from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass

from .feature_service import ALGORITHM, FEATURE_BYTES, decode_feature
from .protocol import Frame, is_valid_action_id


@dataclass(frozen=True)
class FeaturePacket:
    request_id: str
    session: str
    mode: str
    raw: bytes
    started_at: float


class FeatureAssembler:
    def __init__(self) -> None:
        self.session: str | None = None
        self.current: dict | None = None
        self.last_error = ""

    def reset(self, session: str | None = None) -> None:
        self.session = session
        self.current = None

    def feed(self, frame: Frame) -> FeaturePacket | None:
        now = time.monotonic()
        try:
            if self.current and now - self.current["started_at"] > 8:
                self.current = None
                raise ValueError("特征传输超时")
            if frame.kind == "FCANCEL":
                self.current = None
                return None
            if frame.kind == "FBEGIN":
                session, algorithm, size, crc, mode = frame.payload.split("|")
                if (not self.session or session != self.session or algorithm != ALGORITHM
                        or int(size) != FEATURE_BYTES or mode not in ("INFER", "ENROLL")
                        or not is_valid_action_id(frame.lower_id)):
                    raise ValueError("特征会话、算法或长度无效")
                checksum = int(crc, 16)
                if len(crc) != 8 or not 0 <= checksum <= 0xffffffff:
                    raise ValueError("无效 CRC")
                if self.current is not None:
                    raise ValueError("已有未完成的特征请求")
                self.current = {"request_id": frame.lower_id, "session": session, "mode": mode,
                                "started_at": now, "crc": checksum, "data": bytearray()}
                return None
            transfer = self.current
            if transfer is None or transfer["request_id"] != frame.lower_id:
                raise ValueError("没有匹配的特征传输")
            if frame.kind == "FCHUNK":
                offset, encoded = frame.payload.split("|", 1)
                if int(offset) != len(transfer["data"]) or len(encoded) > 684:
                    raise ValueError("特征块错序或过大")
                chunk = base64.b64decode(encoded, validate=True)
                if not chunk or len(chunk) > 512 or len(transfer["data"]) + len(chunk) > FEATURE_BYTES:
                    raise ValueError("特征块长度无效")
                transfer["data"].extend(chunk)
                return None
            if frame.kind == "FEND":
                raw = bytes(transfer["data"])
                if frame.payload != transfer["session"] or len(raw) != FEATURE_BYTES:
                    raise ValueError("特征包不完整")
                if binascii.crc32(raw) & 0xffffffff != transfer["crc"]:
                    raise ValueError("特征 CRC 校验失败")
                decode_feature(raw)
                self.current = None
                self.last_error = ""
                return FeaturePacket(transfer["request_id"], transfer["session"], transfer["mode"], raw, transfer["started_at"])
            raise ValueError("未知特征帧")
        except (ValueError, binascii.Error) as exc:
            self.current = None
            self.last_error = str(exc)
            return None
