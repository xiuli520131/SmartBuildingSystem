# 智能门禁：RISC-V 板端采集与 Windows 管理后台

本分支 `feature/riscv-face-access-20261015` 基于原仓库 `master` 的 `d5a19e59592e61bfbf85b12f4f32d9cf42fffb67`，整理截至 2026-10-15 的课程项目改动。板端采用已上板的 **v2.3.1**，尚未部署的 v2.3.2 诊断补丁不在本分支。改动范围见 [交接与改动说明](docs/分支改动与交接说明.md)。

RISC-V 开发板通过 OV2640 采图、KPU 检测人脸并提取 `lbph4-v1` 特征，经 USB 串口传到 Windows 后端。后端匹配人员模板、检查启用状态和本门通行权限，再返回决定；开发板核对请求和现场人脸后，以 LED 点亮约 3 秒模拟解锁。电脑和 USB 串口是本版运行条件。

## 当前功能与状态

- 保留实时监控、人员与权限、通行记录、告警四个页面，以及记录导出和原有管理能力。
- 人员样本支持上传自拍照片、Windows 摄像头和板载摄像头；训练、独立验证和陌生人验证样本分开管理，变更样本后需重新校准阈值。
- 板端实现摄像头预览、人脸框、单脸质量检查、特征提取、当前请求校验及 LED 模拟锁；Windows 实现比对、权限、事件与执行结果监控。
- 已有实板 `FACE_STABLE`、身份匹配和 `ALLOW|OK`、随后恢复 `LOCKED` 的记录。串口字符发送间隔设为 3 ms 后，用户完成无人运行 15 分钟、进出镜头 10 次和连续面对镜头 5 分钟，未观察到冻结或离线。
- **仍有匹配与权限通过、板端取消请求并返回 `STALE_REQUEST` 的情况**。`FACE_CHANGED` 的具体分支尚未定位；后台判定允许不等于板端已执行开锁。上述运行记录不代表长期稳定性或识别准确率验收完成。

## 目录

| 目录 / 文件 | 用途 |
|---|---|
| `backend/app/` | FastAPI、数据库、人员权限、特征匹配、串口与诊断日志 |
| `frontend/src/` | Vue 页面、API 类型和三种来源的人脸采集组件 |
| `firmware/edu-riscv64/` | 板端应用与学校 XiZi 源码接入脚本；不含操作系统和模型 |
| `docs/板端特征协议与算法说明.md` | 特征格式、阈值校准、串口协议和请求保护 |
| `docs/多来源人脸录入使用说明.md` | 照片、电脑和板端采集操作 |
| `docs/待联调事项.md` | 当前已确认事项、问题和后续验证 |
| `docs/2026-10-15-*.md` | 各阶段实现、实板反馈与故障排查记录 |
| `backend/tests/` | 原仓库遗留脚本；本次未运行，依赖的历史数据不随分支提供 |

## Windows 首次启动

需要 Python（当前开发环境为 3.13）、Node.js/npm，以及真实串口演示时的 PL2303 驱动。以下命令从本仓库根目录执行。源码分支不包含 `node_modules`、`.venv` 或 `frontend/dist`，先安装依赖并构建前端：

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
```

确认构建成功后，关闭 K-Flash、MobaXterm 对串口的占用。开发板 USB 留在 Windows；先确认当前串口：

```powershell
& .\.venv\Scripts\python.exe -m serial.tools.list_ports -v
```

在同一 PowerShell 中设置参数并启动；`COM10` 替换为实际枚举结果：

```powershell
$env:SMART_ACCESS_DATA = Join-Path (Get-Location).Path 'demo-data'
$env:SMART_ACCESS_INPUT = 'board'
$env:SMART_ACCESS_BRIDGE_MODE = 'serial'
$env:SMART_ACCESS_SERIAL_PORT = 'COM10'
$env:SMART_ACCESS_SERIAL_BAUD = '115200'
$env:SMART_ACCESS_SERIAL_CHAR_INTERVAL = '0.003'
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.main
Pop-Location
```

浏览器打开 `http://127.0.0.1:8000`。后端首次启动创建空数据库及 D001 设备，不含原开发者的人员、照片、特征和阈值。后端握手会自动启动板端采集。也可参考 `backend/.env.example` 创建本机 `.env`。仅查看界面可把桥接模式设为 `mock`；模拟模式不能验证真实板端识别和开锁。

前端开发可在 `frontend` 中执行 `npm run dev`，另行保持后端运行。重建前端或修改后端代码后需重启后端，后台运行进程不会自动加载本次源码更新。

## 板端与登记流程

首次安装板端源码按 [板端说明](firmware/edu-riscv64/README.md) 操作。已经运行 v2.3.1 的板无需为本次整理重新烧录。保持横放：USB 在上、摄像头在右下，LCD 预览和红框旋转 180°，模型输入方向保持实板确认的状态。

1. 确认网页真实串口在线、板端特征模块就绪，LCD 实时更新。
2. 新建人员，至少采 3 份训练模板、3 份另行拍摄的独立验证样本，并采其他人员或陌生人验证样本。照片/电脑摄像头录入不要求板端在线。
3. 执行阈值校准。样本不足或本人/他人距离重叠时保持未校准，补样本后再试。推荐用现场板载摄像头采独立验证样本，评估跨摄像头差异。
4. 设置 D001 的有效通行权限，人员离开镜头再进入。分别核对身份判定、授权结果、板端 ACK 和 LED 实际动作；不能只看“识别通过”。

## 联调限制

本项目面向 20 人以内的课程演示。学校 `face.kmodel` 用于检测人脸，身份特征采用 LBPH；尚未完成准确率、误接受率、跨摄像头一致性和端到端延迟的系统评估。没有活体检测、实体电锁/门磁反馈、脱离电脑运行或网页板端实时视频。LCD 曾出现热复位后黑屏、完全断电后恢复的情况，原因未确认。

原仓库已跟踪的依赖、构建文件、IDE 缓存和人员演示数据已在本分支最新目录中移除并加入忽略规则，原有提交历史保留。学校资料、工具、固件二进制和未部署诊断补丁不随分支上传。
