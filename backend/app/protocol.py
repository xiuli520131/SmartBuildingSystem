"""板端串口协议：AC| 帧的解析与构造。

帧格式（板端 door_app.c / README 确认）：
    AC|PONG|<12hex-id>|1
    AC|STATUS|<12hex-id>|LOCKED|UNLOCKED|FAULT
    AC|ACK|<12hex-id>|ALLOW|DENY|OK
    AC|STATE|<12hex-id>|LOCKED
    AC|ERR|<12hex-id>|NOT_READY|BAD_ARGS|BAD_ID|FAULT|BUSY

说明：
- 请求 ID 必须是 12 位十六进制，且不得以 ffff 开头（板端保留给手动命令）。
- 串口里混有 Shell 回显、提示符、启动日志和 ANSI 字符，解析器只认 AC| 帧。
- ACK OK 只表示 GPIO 写成功，不代表真实开门。
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Optional

# 帧首：AC|<TYPE>|<12hex>|...
_FRAME_RE = re.compile(r"^AC\|([A-Z]+)\|([0-9a-fA-F]{12})\|(.+)$")

VALID_ACTIONS = ("ping", "status", "allow", "deny")
# 板端保留前缀
RESERVED_PREFIX = "ffff"


@dataclass
class Frame:
    kind: str          # PONG / STATUS / ACK / STATE / ERR
    req_id: str        # 12-hex，小写
    payload: str

    @property
    def lower_id(self) -> str:
        return self.req_id.lower()

    def describe(self) -> str:
        return f"AC|{self.kind}|{self.req_id}|{self.payload}"


def new_request_id() -> str:
    """生成 12 位十六进制请求 ID，保证不以 ffff 开头。"""
    while True:
        rid = secrets.token_hex(6)  # 12 字符
        if not rid.startswith(RESERVED_PREFIX):
            return rid


def parse_line(line: str) -> Optional[Frame]:
    """从一行文本中提取 AC| 帧；非 AC 行（回显/日志/提示符）返回 None。"""
    # 行内可能夹带前后缀（如 \r、ANSI），先找 AC| 起点
    idx = line.find("AC|")
    if idx < 0:
        return None
    m = _FRAME_RE.match(line[idx:].strip())
    if not m:
        return None
    return Frame(kind=m.group(1), req_id=m.group(2).lower(), payload=m.group(3))


def build_command(action: str, req_id: str) -> str:
    """构造发送给 Shell 的命令行（不含换行符）。"""
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    return f"Door {action} {req_id}"


def is_valid_action_id(req_id: str) -> bool:
    return (
        len(req_id) == 12
        and re.fullmatch(r"[0-9a-fA-F]{12}", req_id) is not None
        and not req_id.lower().startswith(RESERVED_PREFIX)
    )
