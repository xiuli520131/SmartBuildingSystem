# smart-access：Windows 前端 + Python 后端（对接 XiUOS 板端）

本目录是智能门禁系统的 Windows 侧实现，与 `firmware/edu-riscv64/` 下已验证的 RISC-V 板端固件配套。

```text
浏览器 Vue3 前端  ──HTTP 帧──▶  FastAPI 后端  ──串口 AC| 协议──▶  RISC-V XiUOS 板
（getUserMedia 摄像头）        （人脸/权限/串口桥）             （Door allow/LED 模拟锁）
```

板端固件 `smart-access-v1.bin` 已烧录验证（见根目录 `README.md`），本目录不再包含板端代码。

## 1. 目录结构

```text
smart-access/
├── backend/                    Python FastAPI 后端
│   ├── app/
│   │   ├── main.py             入口（lifespan 装配、CORS、托管前端 dist）
│   │   ├── config.py           全部可调参数（.env 覆盖）
│   │   ├── database.py         SQLite 连接与建表
│   │   ├── state.py            进程内单例（face/bridge/access）
│   │   ├── schemas.py          Pydantic 请求/响应模型
│   │   ├── face_service.py     Haar 检测 + LBPH 训练/识别/留一法校准
│   │   ├── access_service.py   决策链：识别→启用→权限时段→在线→allow/deny
│   │   ├── serial_bridge.py   串口桥：mock/serial 双模式、心跳、重连
│   │   ├── protocol.py         AC| 帧解析与构造（12-hex 请求 ID）
│   │   └── api/                users / permissions / devices / faces / access / alerts
│   ├── tests/
│   │   ├── smoke_test.py       隔离数据库冒烟（34 项）
│   │   └── e2e_api_check.py    对运行中后端的 HTTP 端到端验证
│   ├── requirements.txt
│   └── .env.example
├── frontend/                   Vue3 + TS + Vite
│   ├── src/
│   │   ├── api.ts              fetch 封装 + 简单响应式 store
│   │   ├── App.vue            顶部导航
│   │   └── views/             Monitor / People / Records / Alerts
│   └── vite.config.ts          /api 代理到 127.0.0.1:8000
└── data/                       SQLite、人脸样本、LBPH 模型（不入库）
    ├── smart_access.db
    ├── faces/<user_id>/*.jpg
    └── model/face_model.yml
```

## 2. 快速启动（mock 模式，无需开发板）

默认 `SMART_ACCESS_BRIDGE_MODE=mock`，后端在本机模拟板端状态机，`Door allow` 会在 3 秒后自动回 `LOCKED`，便于先看网页。

### 2.1 启动后端

```powershell
cd C:\SmartBuildingSystem\smart-access\backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动成功日志会打印 `mode=mock port=8000`。健康检查：

```powershell
curl http://127.0.0.1:8000/api/health
# {"status":"ok","mode":"mock","online":true,"lock_state":"LOCKED",...}
```

### 2.2 打开网页（二选一）

**方式 A：直接用后端托管的前端产物（推荐演示用）**

`frontend/dist` 已构建好，后端启动后直接浏览器访问 <http://127.0.0.1:8000/> 即可。

**方式 B：前端开发模式（改代码时用）**

```powershell
cd C:\SmartBuildingSystem\smart-access\frontend
npm run dev
```

访问 <http://127.0.0.1:5173/>，Vite 会把 `/api` 代理到 8000。前端改动热更新，无需重启后端。

> 摄像头 `getUserMedia` 要求 `http://localhost` 或 HTTPS，用 `127.0.0.1` 也可以；不要用其他机器 IP 直接打开，否则浏览器会拒绝摄像头权限。

## 3. 切换到真实开发板（COM9）

前提：已按根目录 README 把 `smart-access-v1.bin` 烧进板子，并用 MobaXterm 关掉（释放 COM9）。

**方式 1：网页右上角设备面板切换**

在「实时监控」页设备状态区，通过界面操作（或调用下面的 API）把模式从 `mock` 切到 `serial`，端口填 COM9。

**方式 2：API 切换**

```powershell
curl -X POST http://127.0.0.1:8000/api/devices/mode `
  -H "Content-Type: application/json" `
  -d '{"mode":"serial","com_port":"COM9","baud":115200}'
```

**方式 3：环境变量**

复制 `backend/.env.example` 为 `backend/.env`，改：

```ini
SMART_ACCESS_BRIDGE_MODE=serial
SMART_ACCESS_SERIAL_PORT=COM9
SMART_ACCESS_SERIAL_BAUD=115200
```

重启后端。串口桥会：
1. 打开 COM9（115200, 8-N-1）；
2. 自动探测换行符（`\r\n` / `\r` / `\n`）直到收到 `AC|PONG|...`；
3. 之后每 5 秒发 `Door ping <id>` 心跳，15 秒无响应判离线；
4. 断线自动重连，但**绝不重发状态不明的 `allow`**。

> K-Flash 和 MobaXterm 不能同时占用 COM9。切到 serial 模式前请先关闭 MobaXterm。

## 4. 人脸注册与阈值校准（首次使用必做）

1. 打开网页「人员与权限」页，添加人员（编号、姓名）。
2. 选中该人员，点「采集样本」，正对着摄像头连续拍 5–10 张，要求有轻微角度/光照变化。
   - 后端会用 Haar 级联检测，**必须恰好一张脸**才保存；无脸/多脸自动拒绝。
   - 样本 ≥ 2 张后自动增量训练 LBPH 模型。
3. 采集完点「训练」，再点「校准」。
   - 校准采用**留一法**：对每张样本，用其余样本训练后回头预测它，得到自距离；阈值 = 自距离最大值 + 15（`calibrate_margin`），下限 40。
   - 同时计算与他人样本的最近距离，若小于阈值会提示「存在误识风险，建议补样本」。
4. 用一张**未参与注册**的照片点「验证」，确认本人能通过、他人被拒。
5. 在「实时监控」页开摄像头，正对镜头，每 1.5 秒自动提交一帧；识别结果叠加在画面上。

识别判定（LBPH 距离越小越相似）：

| 状态 | 含义 | 是否开门 |
| --- | --- | --- |
| `no_face` | 未检测到人脸 | 否 |
| `multiple` | 检测到多张人脸 | 否 |
| `unknown` | 距离 > 阈值 | 否（生成未知人员告警） |
| `recognized` | 距离 ≤ 阈值，命中某用户 | 继续看权限 |
| 人员停用 / 无权限 / 时段外 | 识别成功但不放行 | 发 `Door deny` |
| 设备离线 | 识别成功但板子没响应 | 记录授权，执行标「未确认」 |

## 5. 端到端联调清单

对照 `docs/人脸识别智能门禁系统实施计划.md` 阶段 5，逐项验证：

- [x] 已注册且有权限人员 → `allow` + `confirmed`，mock 下 3 秒后自动 `LOCKED`
- [x] 已注册但无权限 → `deny`，不触发解锁
- [x] 未知人员 → `unknown` + `block`，写告警
- [x] 无脸 / 多脸 → 不发任何命令
- [x] mock 模式下拔「线」（切回 mock）→ 授权结果保留但执行未确认
- [x] 重复帧节流：门未锁定时不重复发 `allow`；同一人 3 秒内不重复开门
- [x] 后端重启后人员/权限/记录保留（SQLite 落盘）

已通过的自动化验证：

- `backend/tests/smoke_test.py`：隔离库 34 项断言全过（见 `tests/smoke_output.txt`）。
- `backend/tests/e2e_api_check.py`：对运行中后端 HTTP 全流程验证，注册→训练→校准→授权→识别→误识拦截，全部通过。

## 6. 主要 HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（mode/online/lock_state/model_version） |
| GET/POST | `/api/users` | 人员列表 / 新建 |
| PUT/DELETE | `/api/users/{id}` | 改人员 / 删除（连带样本与权限） |
| GET/POST | `/api/permissions` | 权限列表 / 新建（valid_from~valid_to） |
| GET/POST | `/api/devices`、`/api/devices/status`、`/api/devices/mode` | 设备列表 / 状态 / 切 mock↔serial |
| POST | `/api/faces/sample` | 上传一张人脸样本（恰好一张脸才接受） |
| POST | `/api/faces/train` | 全量重训 LBPH |
| POST | `/api/faces/calibrate` | 留一法校准每用户阈值 |
| POST | `/api/faces/validate` | 用未注册照片验证某人 |
| GET | `/api/faces/status` | 模型版本、样本数、每用户阈值 |
| POST | `/api/access/frame` | 提交一帧 base64，走完整决策链 |
| GET | `/api/access/events?limit=&authorization=&recognition=` | 通行记录 |
| GET | `/api/access/summary` | 今日统计 + 设备状态 |
| GET/PUT | `/api/alerts`、`/api/alerts/{id}` | 告警列表 / 标记已处理 |

交互式文档启动后端后访问 <http://127.0.0.1:8000/docs>。

## 7. 常见问题

- **摄像头打不开 / `NotAllowedError`**：浏览器地址栏左侧权限里允许摄像头；必须用 `localhost`/`127.0.0.1` 访问，不能用局域网 IP。
- **切 serial 后一直「未收到 PONG」**：确认板子已上电、`smart-access-v1.bin` 已烧录、MobaXterm 已关闭释放 COM9；换 USB 口后用设备管理器确认新 COM 号。
- **同一个人时好时坏**：回「人员与权限」补 2–3 张不同角度/光线样本，重新训练并校准；阈值不要手工压得太低（默认 80，校准后每人独立）。
- **陌生人被认成某人**：校准结果里若出现 `cross_min < threshold`，说明两类人脸太像，补样本或在「人员与权限」页把该用户阈值调小（更严）。
- **端口 8000 被占**：改 `.env` 里 `SMART_ACCESS_PORT`，同时改 `frontend/vite.config.ts` 的 proxy target。

## 8. 数据与隐私

- 所有 SQLite、人脸样本、模型文件都在 `smart-access/data/`，**不要**提交 Git、不要邮件外发。
- 删除人员时会连带删除其样本图片与权限，并重训模型。
- LBPH 是本地模型，不上传任何照片到外部服务。
