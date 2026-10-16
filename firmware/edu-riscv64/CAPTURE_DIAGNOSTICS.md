# 采集故障诊断版 v2.2（2026-10-15）

## 当前证据

用户安装 NMS 栈数组修正并重编译，新 bin 的 Ubuntu SHA256 为 7865206712d0571868ebdc5086892a0d6516c43b3892c5ddbf7ebe35ffd57947。烧录后曾报告 frames=45、capture=90ms、NO_FACE；后续查询为 ready=0 running=0 state=CAPTURE_TIMEOUT_REBOOT frames=83，人脸任务 utask01 已不在任务列表，屏幕变黑。初次 Load address misaligned 未在这份日志重现，但无法从已退出的任务读取采集期间最大栈使用。

旧状态 CAPTURE_TIMEOUT_REBOOT 是统一标签，CameraControl 失败、摄像头等待超时、KPU 启动/等待失败、模型输出尺寸异常及非有限数值都会走这个路径。因此现在不能断定是摄像头故障，也不能因为 capture=90ms 就认为最后一次失败只用了 90ms：这个字段保存的是最近一次成功调用的耗时，且原来没有包含 LCD 写入。

照片中噪点条带与屏幕发黑的原因尚未确定。学校 LCD 默认 320×320，应用只绘制 320×240，未绘制部分可能保留旧像素；这一点只能解释部分显示现象，不能解释所有黑屏或处理退出。摄像头是否提供正确的人像尚未确认。

## 诊断版新增证据

保留 v2.1 的堆工作区修正和 16 KiB 任务栈，在 face_app.c 增加：

- 按阶段记录 CAMERA_START、WAIT_CAMERA、KPU_START、WAIT_KPU、KPU_OUTPUT、POSTPROCESS、LCD_WRITE、FEATURE、IDLE、FAULT。
- 错误标签区分 CAMERA_START_FAILED、CAMERA_FLAG_FAILED、CAMERA_WAIT_TIMEOUT、KPU_START_FAILED、KPU_WAIT_TIMEOUT、KPU_OUTPUT_FAILED、KPU_OUTPUT_INVALID、KPU_OUTPUT_NONFINITE、LCD_WRITE_FAILED。
- 首次故障自动打印 `Face fault reason=...`，包含当前阶段、成功帧数、返回码/异常索引、总耗时、阶段耗时、DVP 状态/配置寄存器和 KPU 层进度。随后设置具体故障状态并退出任务，仍要求复位后才能再次采集。
- `Face` 额外打印 `Face diag=v2.2 ...`。无后台会话且未取得合格单脸特征时，在第 1 帧及每 30 帧打印缓冲采样：R/G/B 各 256 点的最小值/最大值/均值/采样哈希，以及 RGB565 缓冲的范围/采样哈希。数值只能辅助识别全黑、固定缓冲等现象，不能证明图片正确。
- LCD 写入失败以前只跳出绘制循环，诊断版会记录并进入故障保护。帧数现在在本次显示写入成功或未连接显示设备后递增。

本版没有调整摄像头寄存器、DVP 中断、KPU DMA、检测阈值或超时限制，没有自动恢复或继续授权。采样日志在正常捕获完成后输出；有限日志仍可能改变任务时序，所以暂时不宣称修复。尚待 Ubuntu 交叉编译与实板运行。

## Ubuntu 安装

关闭 Windows 后端。将 smart-access-face-v2.2-diagnostics.zip 作为完整 ZIP 通过邮件传到 /home/xiuos/Downloads。以下安装编译不需要连接开发板。

```bash
mkdir -p /home/xiuos/smart-access-face-v2.2-diagnostics
unzip -o /home/xiuos/Downloads/smart-access-face-v2.2-diagnostics.zip -d /home/xiuos/smart-access-face-v2.2-diagnostics
python3 /home/xiuos/smart-access-face-v2.2-diagnostics/install_capture_diagnostics.py /home/xiuos/test/xiuos
```

脚本检查当前 v2.1 源码（忽略 Windows/Ubuntu 换行差别）或已安装的同版诊断源码。若与两者都不同，停止并保留现场。先备份 access_face 完整目录、摄像头驱动、配置、旧 ELF/bin/map，再替换 face_app.c、region_layer.c/h，打印当前相关配置和旧 bin SHA256。备份位于 /home/xiuos/xiuos-backups/face-capture-diag-时间/。模型不需要再次传输；原工程配置保留，不需要 menuconfig。

提示 Capture diagnostics stopped 时发送完整输出，先不继续编译烧录。

## 编译与传回

```bash
cd /home/xiuos/test/xiuos/Ubiquitous/XiZi_IIoT
make BOARD=edu-riscv64 clean
set -o pipefail
make BOARD=edu-riscv64 -j1 2>&1 | tee /home/xiuos/smart-access-face-v2.2-diagnostics-build.log
echo "build exit=$?"
ls -lh build/XiZi-edu-riscv64.bin
sha256sum build/XiZi-edu-riscv64.bin
```

仅在编译/链接成功、build exit=0 后，将新 bin 传到 Windows，另存为 D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.2-diagnostics.bin。用 PowerShell 核对两端 SHA256：

```powershell
Get-FileHash D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.2-diagnostics.bin -Algorithm SHA256
```

关闭后端和串口终端，用之前成功的 K-Flash 设置烧录新文件（实际 COM、115200），USB 由 Windows 使用。完成后关闭 K-Flash，使用 MobaXterm 打开串口、复位开发板。

## 板端记录

后台保持关闭。启动日志应有 `diagnostics=v2.2`，否则先核对烧录文件。输入：

```text
Face
Face start
```

在有照明的环境中，让摄像头对准可辨认的物体/人，等待约 10 秒，输入 Face；随后每隔约 10 秒再次查询，观察帧数是否增加。无需反复重新输入 Face start。记录连续串口输出和屏幕画面，最多观察 60 秒。

出现 `Face fault reason=...` 后，不按 Reset，输入 Face 和 ShowTask 保存最终状态。如没有故障且帧数继续增加，输入 ShowTask 记录 utask01 栈用量，再输入 Face stop。若发生异常或命令不响应，保存第一段完整输出后停止运行。

需要返回：安装脚本输出、编译结尾和 SHA256、从复位开始的串口日志（尤其是 Face sample / Face fault / Face diag 行）及屏幕是否始终黑屏、有无可辨认图像。根据明确的失败阶段再修正驱动或采集链路，不把加长超时当作根因修复。
