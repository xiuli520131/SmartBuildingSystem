"""端到端 API 冒烟：通过 HTTP 调运行中的后端，走一遍 注册→训练→校准→授权→识别→开门。"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = r.read().decode()
            return r.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def main() -> int:
    import time as _t
    assets = Path(__file__).parent / "assets"
    img_a = b64(assets / "person_a.jpg")
    img_b = b64(assets / "person_b.jpg")

    fails = 0

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal fails
        mark = "PASS" if cond else "FAIL"
        if not cond:
            fails += 1
        print(f"  [{mark}] {name} {extra}")

    print("== 1. 创建人员 ==")
    no = f"E2E{int(_t.time()) % 100000}"
    s, u = call("POST", "/api/users", {"user_no": no, "name": "端到端甲"})
    check("创建人员", s in (200, 201), f"status={s} body={u}")
    user_id = u["id"] if s in (200, 201) else None

    print("== 2. 注册样本（person_a 同图 3 张） ==")
    for i in range(3):
        s, r = call("POST", "/api/faces/sample", {"user_id": user_id, "image": img_a})
        check(f"样本{i+1}", s == 200 and r.get("ok"), f"msg={r.get('message')}")

    print("== 3. 训练 + 校准 ==")
    s, r = call("POST", "/api/faces/train")
    check("训练", s == 200 and r.get("ok"), f"msg={r.get('message')}")
    s, r = call("POST", "/api/faces/calibrate")
    check("校准", s == 200 and r.get("ok"), f"msg={r.get('message')}")

    print("== 4. 配置权限 ==")
    s, devs = call("GET", "/api/devices")
    dev_id = devs[0]["id"]
    s, p = call(
        "POST",
        "/api/permissions",
        {
            "user_id": user_id,
            "device_id": dev_id,
            "valid_from": "2020-01-01 00:00",
            "valid_to": "2099-12-31 23:59",
        },
    )
    check("创建权限", s in (200, 201), f"status={s} body={p}")

    print("== 5. 识别 person_a（应 recognized + allow + confirmed） ==")
    s, r = call("POST", "/api/access/frame", {"image": img_a, "device_no": "D001"})
    check("recognition=recognized", r.get("recognition") == "recognized", f"got={r.get('recognition')}")
    check("authorization=allow", r.get("authorization") == "allow", f"reason={r.get('auth_reason')}")
    check("execution=confirmed", r.get("execution") == "confirmed", f"exec={r.get('execution')}")
    check("request_id 非空", bool(r.get("request_id")), f"rid={r.get('request_id')}")

    print("== 6. 识别 person_b（应 unknown） ==")
    s, r = call("POST", "/api/access/frame", {"image": img_b, "device_no": "D001"})
    check("recognition=unknown", r.get("recognition") == "unknown", f"got={r.get('recognition')} conf={r.get('confidence')}")
    check("authorization=block/deny", r.get("authorization") in ("block", "deny"), f"auth={r.get('authorization')}")

    print("== 7. 记录 / 汇总 ==")
    s, evs = call("GET", "/api/access/events?limit=5")
    check("事件已落库", s == 200 and len(evs) >= 2, f"count={len(evs) if s==200 else '?'}")
    s, sm = call("GET", "/api/access/summary")
    check("汇总返回", s == 200 and sm.get("total_users", 0) >= 1, f"users={sm.get('total_users')}")

    print(f"\n结果: {'全部通过' if fails == 0 else f'{fails} 项失败'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
