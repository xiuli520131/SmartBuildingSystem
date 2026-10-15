"""Bounded serial evidence for unattended failures; never log face features."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .protocol import Frame


class SerialDiagnostics:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "logs" / "serial-diagnostics.jsonl"
        self.error = ""
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._rx_bytes = 0
        self._rx_lines = 0
        self._feature_chunks = 0
        self._last_rx: float | None = None
        self._logger = logging.Logger("smart-access-serial-diagnostics")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self.path, maxBytes=2_000_000, backupCount=3,
                encoding="utf-8", delay=True,
            )
            # Logging errors must not stop the serial reader or flood stderr.
            handler.handleError = self._handle_error
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        except OSError as exc:
            self.error = str(exc)
            self._logger.addHandler(logging.NullHandler())

    def _handle_error(self, record: logging.LogRecord) -> None:
        self.error = "通信诊断日志写入失败，请检查日志目录权限和磁盘空间"

    def event(self, event: str, **fields) -> None:
        now = time.monotonic()
        with self._lock:
            record = {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "elapsed_s": round(now - self._started, 3),
                "event": event,
                "rx_bytes": self._rx_bytes,
                "rx_lines": self._rx_lines,
                "feature_chunks": self._feature_chunks,
                "rx_idle_s": round(now - self._last_rx, 3) if self._last_rx is not None else None,
                **fields,
            }
        self._logger.info(json.dumps(record, ensure_ascii=True))

    def received(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._rx_bytes += count
            self._last_rx = time.monotonic()

    def line(self, text: str, frame: Frame | None, phase: str) -> None:
        with self._lock:
            self._rx_lines += 1
            if frame is not None and frame.kind == "FCHUNK":
                self._feature_chunks += 1
                return
        if frame is not None:
            fields = {"kind": frame.kind, "request_id": frame.lower_id, "phase": phase}
            # Unknown future frame types might contain biometric data.
            if frame.kind in {
                "PONG", "STATUS", "ACK", "STATE", "ERR", "FREADY", "FPROBE",
                "FENROLL", "FSAVED", "FCANCEL", "FBEGIN", "FEND",
            }:
                fields["payload"] = self._safe_text(frame.payload)
            self.event("rx_frame", **fields)
            return
        # Keep all non-feature text, including unfamiliar/garbled fatal messages.
        # The previous allow-list discarded unrecognized lines at the freeze boundary.
        if "FCHUNK" in text or re.fullmatch(r"[A-Za-z0-9+/=]{40,}", text.strip()):
            self.event("rx_feature_text_omitted", length=len(text), phase=phase)
        elif text.strip():
            self.event("rx_shell", text=self._safe_text(text), length=len(text), phase=phase)

    @staticmethod
    def _safe_text(text: str) -> str:
        # Also redact a damaged feature line interleaved with a command echo.
        text = re.sub(r"[A-Za-z0-9+/=]{40,}", "[encoded-data omitted]", text)
        return text[:1024]
