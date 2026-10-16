# Windows 板端特征联调步骤

更新：2026-10-15。本机项目为 D:\SmartBuildingSystem\smart-access-demo。v2.3.1 已有实板正立预览、红框及 FACE_STABLE 反馈，板端特征、身份权限及执行已有联调记录；当前问题见 待联调事项.md。当前无需再次编译烧录。

## 1. 保持板端连接

保持开发板 USB 接在 Windows，固定 USB 在上、摄像头在右下的横放姿态。关闭 K-Flash 和 MobaXterm 的串口会话，以便后端使用同一串口。保留开发板供电；无需把 USB 切到 Ubuntu 虚拟机。

本文件中的 D:\SmartBuildingSystem\smart-access-demo 是原开发机路径，请替换为你的检出目录。新检出仓库须先按根 README 安装 Python/npm 依赖并构建 frontend/dist；完成后再执行下方联调步骤。

## 2. 确认端口

在 Windows PowerShell 执行：

```powershell
cd D:\SmartBuildingSystem\smart-access-demo
& .\.venv\Scripts\python.exe -m serial.tools.list_ports -v
```

2026-10-15 本次检查结果为 COM10，描述为 Prolific USB-to-Serial Comm Port (COM10)。照片中的 COM9 是此前使用记录；重新插拔可能改变端口，始终以上述命令当前结果为准。没有端口时先检查 Windows 是否持有 USB、线缆及设备管理器，不启动模拟串口代替真实连接。

## 3. 启动后端与网页

同一 PowerShell 窗口依次执行。COM10 按第 2 步结果修改：

```powershell
cd D:\SmartBuildingSystem\smart-access-demo
$env:SMART_ACCESS_DATA = 'D:\SmartBuildingSystem\smart-access-demo\demo-data'
$env:SMART_ACCESS_INPUT = 'board'
$env:SMART_ACCESS_BRIDGE_MODE = 'serial'
$env:SMART_ACCESS_SERIAL_PORT = 'COM10'
$env:SMART_ACCESS_SERIAL_BAUD = '115200'
$env:SMART_ACCESS_SERIAL_CHAR_INTERVAL = '0.003'
$env:SMART_ACCESS_HOST = '127.0.0.1'
$env:SMART_ACCESS_PORT = '8000'
cd backend
& ..\.venv\Scripts\python.exe -m app.main
```

保持此窗口运行。后端会初始化独立的 demo-data 数据库和 D001 设备，并通过串口握手自动启动板端采图。浏览器打开 http://127.0.0.1:8000，进入实时监控，等待显示真实串口在线和“板端特征模块就绪”。板端模式使用开发板摄像头，现场画面在 LCD 查看；网页暂未提供板端实时视频。

初次没有人员模板或阈值尚未校准时拒绝通行属于预期行为。

## 4. 确认连接结果

另开一个 Windows PowerShell，执行：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/health' | ConvertTo-Json -Depth 4
Invoke-RestMethod 'http://127.0.0.1:8000/api/edge/status' |
    Select-Object input_mode, ready, camera_state, session, transport_error |
    ConvertTo-Json -Depth 6
```

health 应为 status=ok、mode=serial、online=true；edge 应为 input_mode=board、ready=true。ready 说明串口会话及板端特征模块已就绪，还需下一步采集成功才能确认完整特征上传和保存。保留输出及后端报错供排查。端口占用时关闭其他串口会话；8000 已被占用时查看现有服务，不重复启动。

## 5. 人员登记和样本采集

在“人员与权限”页面添加人员编号和姓名，然后打开其“采集人脸”弹窗。新版支持上传照片、Windows 摄像头和板载摄像头三种来源；照片/电脑摄像头录入无需板子在线，详见 多来源人脸录入使用说明.md。以下为板载摄像头操作：

1. 选择“训练模板”，本人面对板载相机，点击“采集板载摄像头特征”。每次等待采集结束且计数增加，再开始下一次。至少采 3 份，适度改变位置和光照。
2. 选择“独立验证”，重新站位或换时段，另采至少 3 份。不要把训练样本复制为验证样本。
3. 同样登记其他人员。还可让未登记同学选择“未登记人员验证（不绑定此用户）”采集负例；选中此项后实际入镜者应是未登记人员。

采集为异步过程，网页提示请求已接受不等于特征已保存。核对计数与监控结果。若正在传输通行特征而提示忙，先让人离开镜头，待处理结束后再发起采集；不要连续点击。

## 6. 校准、权限和现场通行

点击“阈值校准”。本人验证与他人/陌生人验证不足、距离分布重叠时，系统保持未校准并拒绝通行，应补充样本和检查采图质量。每次新增样本或删除人员都会使校准失效，需要重新校准。

为目标人员配置 D001 的有效通行权限，确认人员启用、权限有效期覆盖当前时间。让人员离开镜头再重新进入，观察身份判断、权限结果、板端执行确认，以及 LED 是否点亮约 3 秒后熄灭。还需对照无权限和陌生人情形。不要把红框或 FACE_STABLE 当作身份已匹配或已允许开门。

## 7. 停止与恢复串口调试

在后端 PowerShell 按 Ctrl+C 停止服务，等待退出后，再打开 MobaXterm 连接当前串口。后端运行期间不要同时用串口终端发命令。串口调试完成后关闭会话，再按第 3 步启动后端。

已有成功匹配与执行的记录，也仍有 FACE_CHANGED/STALE_REQUEST；尚未完成完整场景和识别准确率验收。实板记录见 2026-10-15-v2.3实板记录.md。
