# LCD 与跨核计时诊断 v2.3

日期：2026-10-15。适用于已经安装并运行 v2.2 的学校 edu-riscv64 工程。

更新：用户已烧录运行 v2.3。色带显示清晰，预览可辨认人物/物体，边缘噪点带已呈黑色边框。日志到 270 帧无新故障、栈使用约 14%、阶段计时数值合理；仍 NO_FACE，照片中手机遮脸。下一步观察无遮挡正脸，详见 ../../docs/2026-10-15-v2.3实板记录.md。下文保留本版制作时的调查与操作说明；已分发 ZIP 保留不变。

## 当前结论

v2.2 最新日志已连续处理超过 510 帧，查询时 utask01 仍存在，栈使用约 14%，未报告新的 Face fault。但仍是 NO_FACE，CPU 采样 R/G/B 大多很暗，照片中为绿色噪点和白色噪点条带。帧数增加不能证明摄像头图像正确，更不能证明已识别人脸。

随后用户补充“摄像头已经能拍到东西，但是还是有噪声光带”。这是新的画面反馈；目前还没有对应新照片或检测日志，需确认光带是否固定在未绘制的约四分之一区域，不能继续把先前整幅噪点照片当作当前画面。

用户进一步确认：光带固定在屏幕边缘，约占四分之一。这个位置和比例与旧程序遗漏的 80/320 行吻合，未清屏是当前主要原因假设；待 v2.3 全屏清理后用实板确认。

学校驱动的 PrivGetTickTime 最终读取 heartbeat_ticks[GetCpuId()]，是各核分别维护的时间。worker 在核 0、shell 在核 1 时不应直接相减。此外旧诊断并发读取 stage_since/capture_stage，没有获取一致的阶段时间对。这两点可以导致 stage_ms=4294967076 一类结果。不能据此断言此前 83 帧后的退出就是误超时。

学校屏幕为 320×320，旧程序只写 320×240，留下 80 行未初始化。这可能解释照片中的部分噪点条带，不能解释整个预览异常。

## 本版改动与诊断顺序

1. Face 内部计时改读两核共享的 CLINT mtime，按学校 SysctlClockGetFreq(CPU)/CLINT_CLOCK_DIV 换算毫秒。只读计数器，不调用 CLINT 初始化/启动/停止接口；假设演示期间 CPU 频率固定。阶段名称和起始时间由单独互斥锁保护，先读取阶段时间再读取当前时间，避免并发与 printf 参数求值顺序导致负数。
2. 增加 `Face lcd`：必须停止采集并关闭后台会话，由原 face worker 绘制完整 320×320 六条色带。逻辑 x 从 0 至 319 依次为红、绿、蓝、白、黑、灰，屏幕安装方向可能使色带横置或顺序反向。无需摄像头图像或模型输出参与绘制，不应产生新采集帧。
3. `Face start` 首次预览先全屏清黑，再把 320×240 图像放在 y=40 至 279，保留两侧各 40 行黑边。色带与预览使用相同的 LCD 行写入接口。行缓冲是静态 640 字节，不占用大块任务栈。

保留 v2.1 的 NMS 堆工作区、v2.2 阶段与采样日志。摄像头寄存器、DVP 驱动、KPU 模型、检测门槛与超时限制没有改动。诊断期间 Windows 后端关闭。当前尚未在本机完成 XiZi 全量交叉编译或实板验证，不宣称花屏或历史超时已经修复。

## 安装到 Ubuntu

把完整 `smart-access-face-v2.3-lcd-diagnostics.zip` 通过邮件或共享文件夹传至 `/home/xiuos/Downloads`，不要单独发送 .py 文件。安装和编译阶段不需要连接开发板。

```bash
mkdir -p /home/xiuos/smart-access-face-v2.3-lcd-diagnostics
unzip -o /home/xiuos/Downloads/smart-access-face-v2.3-lcd-diagnostics.zip -d /home/xiuos/smart-access-face-v2.3-lcd-diagnostics
cd /home/xiuos/smart-access-face-v2.3-lcd-diagnostics
sha256sum -c SHA256SUMS.txt
python3 install_lcd_diagnostics.py /home/xiuos/test/xiuos
```

只有文件校验全部 OK、安装脚本完成后继续。脚本支持 Python 3.6，校验当前 v2.2 或同版源码、板型、尺寸、帧结束驱动标记和 CLINT API。先备份完整 access_face、配置、摄像头驱动、旧 bin/ELF/map，再替换应用源码；备份路径会打印出来。没有重新生成模型或修改 menuconfig。

若提示 `LCD diagnostics stopped`，保留完整输出，不绕过校验覆盖文件。

## 编译与烧录

```bash
cd /home/xiuos/test/xiuos/Ubiquitous/XiZi_IIoT
make BOARD=edu-riscv64 clean
set -o pipefail
make BOARD=edu-riscv64 -j1 2>&1 | tee /home/xiuos/smart-access-face-v2.3-lcd-build.log
echo "build exit=$?"
ls -lh build/XiZi-edu-riscv64.bin
sha256sum build/XiZi-edu-riscv64.bin
```

`echo` 必须紧接 make 执行。确认没有编译/链接错误且 build exit=0，再传回新 bin，另存为 `D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.3-lcd-diagnostics.bin`。核对 Windows 哈希与 Ubuntu 一致：

```powershell
Get-FileHash D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.3-lcd-diagnostics.bin -Algorithm SHA256
```

关闭 MobaXterm 和后端，用 K-Flash 选择实际 COM、此前成功的 115200 配置和新 bin 烧录。USB 交给 Windows。烧录成功关闭 K-Flash，打开串口终端，按 Reset。启动信息应含 `diagnostics=v2.3; clock=CLINT/`。

## 第一步：只看 LCD 色带

保持后端关闭，在硬件初始化完成后输入：

```text
Face lcd
```

等待 `Face lcd: DONE; bars=RED,GREEN,BLUE,WHITE,BLACK,GRAY; frames=0`，拍照记录色带。命令刚返回时可仍为 LCD_QUEUED，因为绘制由后台任务执行；等待 DONE 再继续。若出现 fault 或迟迟没有 DONE，保存完整日志，暂不 Face start。

- 六条色带边界清楚、没有噪点：本次静态图案通过 LCD 行写入链路可正常显示，接下来集中检查摄像头输入及格式。
- 色带也花屏：优先检查 LCD 数据/驱动/连接，不能先凭 NO_FACE 调整识别算法。
- 红蓝位置与预期互换但边界清晰：记录可能的颜色顺序差异，不等同于摄像头损坏。

## 第二步：摄像头预览

先找到真实镜头。学校板图中，它是网口旁边竖起的黑色圆筒，顶部圆形玻璃面是镜头。若开发板有外壳，实际安装位置以实物为准；屏幕这一面是否露出镜头不能仅从当前照片确定。确认镜头无盖且朝向有光、可辨认的物体，避免对着桌面。

色带拍照后输入：

```text
Face start
```

等待约 10 秒，输入 `Face`，再输入 `ShowTask`。观察最多 60 秒，记录屏幕能否看清物体、帧数增长、Face sample 和阶段信息，再输入 `Face stop`。边缘黑带在当前分辨率下是预期布局。

若有 `Face fault`，先保留故障行，再查询 Face 和 ShowTask，不立即复位。返回色带照片、镜头朝向/侧背面照片、预览照片和完整日志。阶段名称/起始时间为一致快照，其余阶段耗时/行号是运行中进度参考，不视作同一帧的原子快照。

## 恢复

原 `smart-access-face-v2.2-diagnostics.zip` 保留原内容与 SHA256，不被 v2.3 打包脚本覆盖。需要回退时从本次打印的 face-lcd-diag 备份恢复 access_face，重新 clean/build，或烧录备份的旧 bin；不删除学校工程。已故障的旧 DMA 任务需要先复位后再运行。
