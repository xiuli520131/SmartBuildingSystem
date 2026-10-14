"""冒烟测试：对运行中的后端执行全链路 API 验证（mock 模式）。

前置：后端已以 mock 模式启动（SMART_ACCESS_DATA 指向临时目录）。
运行：backend 目录下  .\\.venv\\Scripts\\python.exe tests\\smoke_test.py
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE = "http://127.0.0.1:8000"
ASSETS = Path(__file__).parent / "assets"

PASS = 0
FAIL = 0


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if not raw:
                return resp.status, {}
            if "json" in content_type:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if not raw:
            return exc.code, {}
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw.decode("utf-8", errors="ignore")


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def b64_of(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def variants(img: np.ndarray, n: int = 6) -> list[np.ndarray]:
    """先检测并裁出人脸区域，再做小幅旋转/亮度/缩放扰动，模拟多角度样本。"""
    import cv2 as _cv2

    cascade = _cv2.CascadeClassifier(
        _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
    assert len(faces) == 1, f"素材应恰好一张脸，实际 {len(faces)}"
    x, y, w, h = faces[0]
    pad = int(0.15 * w)
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, img.shape[1]), min(y + h + pad, img.shape[0])
    crop = img[y0:y1, x0:x1]

    out = []
    h, w = crop.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    for i in range(n):
        angle = (i - n / 2) * 1.8
        scale = 1.0 + (i % 3 - 1) * 0.03
        bright = 1.0 + (i % 4 - 1.5) * 0.07
        M = _cv2.getRotationMatrix2D((cx, cy), angle, scale)
        warped = _cv2.warpAffine(crop, M, (w, h))
        warped = np.clip(warped.astype(np.float32) * bright, 0, 255).astype(np.uint8)
        out.append(warped)
    return out


def blank_image() -> np.ndarray:
    return np.full((480, 640, 3), 128, dtype=np.uint8)


def cleanup_users() -> None:
    """清空测试库中的全部人员（本测试只针对专用临时数据目录）。"""
    st, users = http("GET", "/api/users")
    if st == 200:
        for u in users:
            http("DELETE", f"/api/users/{u['id']}")


def main() -> None:
    cleanup_users()

    print("== 0. 健康检查 ==")
    st, health = http("GET", "/api/health")
    check("health ok", st == 200 and health.get("status") == "ok", str(health))
    check("mock 模式在线", health.get("mode") == "mock" and health.get("online") is True, str(health))

    print("== 1. 创建人员 ==")
    st, ua = http("POST", "/api/users", {"user_no": "U001", "name": "测试用户A"})
    check("创建 A 201", st == 201 and ua["id"] > 0, str(ua))
    st, ub = http("POST", "/api/users", {"user_no": "U002", "name": "测试用户B"})
    check("创建 B 201", st == 201, str(ub))
    st, dup = http("POST", "/api/users", {"user_no": "U001", "name": "重复"})
    check("重复编号 409", st == 409, str(dup))

    print("== 2. 注册样本（A 六张扰动图） ==")
    img_a = cv2.imread(str(ASSETS / "person_a.jpg"))
    img_b = cv2.imread(str(ASSETS / "person_b.jpg"))
    check("素材可读", img_a is not None and img_b is not None)
    a_id, b_id = ua["id"], ub["id"]
    for i, v in enumerate(variants(img_a, 6)):
        st, r = http("POST", "/api/faces/sample", {"user_id": a_id, "image": b64_of(v)})
        check(f"样本{i+1} ok", st == 200 and r["ok"], str(r))
    st, r = http("POST", "/api/faces/sample", {"user_id": a_id, "image": b64_of(blank_image())})
    check("无脸图片被拒", st == 200 and not r["ok"] and "未检测到人脸" in r["message"], str(r))
    two = np.hstack([cv2.resize(img_a, (320, 320)), cv2.resize(img_b, (320, 320))])
    st, r = http("POST", "/api/faces/sample", {"user_id": a_id, "image": b64_of(two)})
    check("多脸图片被拒", st == 200 and not r["ok"] and "只允许恰好一张" in r["message"], str(r))

    print("== 3. 训练与校准 ==")
    st, r = http("POST", "/api/faces/train")
    check("训练 ok", st == 200 and r["ok"] and r["samples"] == 6, str(r))
    st, r = http("POST", "/api/faces/calibrate")
    check("校准 ok", st == 200 and r["ok"], str(r))
    a_th = next(d for d in r["details"] if d["user_id"] == a_id)
    check("A 有阈值", a_th.get("threshold") is not None, str(a_th))

    print("== 4. 识别与授权 ==")
    # 4.1 无权限 → deny
    st, r = http("POST", "/api/access/frame", {"image": b64_of(variants(img_a, 1)[0])})
    check("已识别", r["recognition"] == "recognized", str(r))
    check("无权限 deny", r["authorization"] == "deny" and "无有效权限" in r["auth_reason"], str(r))

    # 4.2 配置权限 → allow
    st, perm = http(
        "POST", "/api/permissions",
        {"user_id": a_id, "device_id": 1,
         "valid_from": "2026-01-01 00:00", "valid_to": "2030-12-31 23:59"},
    )
    check("创建权限 201", st == 201, str(perm))
    st, r = http("POST", "/api/access/frame", {"image": b64_of(variants(img_a, 1)[0])})
    check("allow 授权", r["authorization"] == "allow" and r["execution"] == "confirmed", str(r))
    check("门状态 UNLOCKED", r["device_lock_state"] == "UNLOCKED", str(r))
    check("请求ID 合法", r["request_id"] and len(r["request_id"]) == 12, str(r.get("request_id")))

    # 4.3 3.5 秒后自动锁定
    time.sleep(3.6)
    st, r = http("GET", "/api/devices/status")
    check("3.5s 后自动锁定", r["lock_state"] == "LOCKED", str(r))

    # 4.4 未知人员
    st, r = http("POST", "/api/access/frame", {"image": b64_of(img_b)})
    check("B 判定未知", r["recognition"] == "unknown", str(r))
    check("未知拒绝", r["authorization"] == "block", str(r))

    # 4.5 无脸 / 多脸
    st, r = http("POST", "/api/access/frame", {"image": b64_of(blank_image())})
    check("无脸 no_face", r["recognition"] == "no_face", str(r))
    st, r = http("POST", "/api/access/frame", {"image": b64_of(two)})
    check("多脸 multiple", r["recognition"] == "multiple", str(r))

    # 4.6 未注册照片验证接口
    st, r = http("POST", "/api/faces/validate",
                 {"user_id": a_id, "image": b64_of(variants(img_a, 1)[0])})
    check("validate match", st == 200 and r["verdict"] == "match", str(r))

    print("== 5. 记录 / 汇总 / 导出 / 告警 ==")
    st, r = http("GET", "/api/access/events?limit=20")
    check("事件记录存在", st == 200 and len(r) >= 5, f"count={len(r)}")
    st, raw = http("GET", "/api/access/events/export")
    check("CSV 导出", st == 200 and b"CSV" in raw[:1000] or (st == 200 and len(raw) > 100),
          f"status={st} len={len(raw)}")
    st, r = http("GET", "/api/access/summary")
    check("汇总 ok", st == 200 and r["today_allow"] >= 1 and r["today_unknown"] >= 1, str(r))
    st, r = http("GET", "/api/alerts?limit=20")
    check("未知人员告警", st == 200 and any(a["type"] == "unknown_person" for a in r), str(r))
    st, r = http("GET", "/api/faces/status")
    check("模型状态", st == 200 and r["total_samples"] == 6, str(r))

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
