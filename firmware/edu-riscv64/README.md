# RISC-V 板端源码安装与编译

本目录是 **v2.3.1** 应用源码，包含已上板的预览旋转、NMS 堆工作区等修正，不含暂缓部署的 v2.3.2 补丁。已经运行 v2.3.1 的板无需因本次整理重新烧录。Windows 身份与权限联调已有成功和被取消的两类记录，当前问题见 ../../docs/待联调事项.md。

## 依赖

需要学校提供的完整 XiZi/XiUOS 工程，原使用位置 `/home/xiuos/test/xiuos`，分支 `prepare_for_master`、提交 `3fc12f67b9f2c47c1f665552c03fd1e19cc38929`。编译器为学校 GNU MCU Eclipse RISC-V GCC 8.2.0，安装脚本支持 Python 3.6+。

学校工程须已有 `APP_Framework/Applications/knowing_app/k210_detect_entry/face.kmodel`（388776 字节）及对应 `face.json`。本仓库不分发模型、操作系统或工具链；安装器检查模型结构并在本机生成 face_model_data.c。摄像头使用 OV2640，显示与采集维持学校板型配置。

## 首次接入

把本目录完整复制到 Ubuntu，例如 `/home/xiuos/smart-access-firmware`，确保该目录直接包含 access_control、access_face 和 install_access_face.py。若用邮件传输，将整个目录压成 ZIP，避免单独发送 .py 附件。

以下安装和编译操作不需要连接开发板：

```bash
python3 /home/xiuos/smart-access-firmware/install_access_face.py /home/xiuos/test/xiuos
cd /home/xiuos/test/xiuos/Ubiquitous/XiZi_IIoT
make BOARD=edu-riscv64 menuconfig
```

安装器先校验预期结构，再备份应用入口、配置、已有应用/驱动及固件，打印备份位置，接入应用并修正 DVP 单帧完成标记。若提示停止，保存输出并检查差异，不绕过结构校验。

menuconfig 启用 `APP_ACCESS_FACE`（Smart access board-camera feature demo）和 `APP_ACCESS_CONTROL`，保留 edu-riscv64 合并编译；启用 GPIO、LCD、DMA、摄像头、KPU、DVP 中断，图像 320×240；禁用 `USER_TEST_GPIO`、`USER_TEST_CAMERA` 和会竞争摄像头/KPU 的旧检测示例。

```bash
grep -E '^(CONFIG_(BOARD_EDU_RISCV64_EVB|APP_ACCESS_CONTROL|APP_ACCESS_FACE|BSP_USING_GPIO|BSP_USING_CAMERA|BSP_USING_LCD|BSP_USING_DMA|BSP_USING_KPU|DVP_INTERRUPT_ENABLE|IMAGE_WIDTH|IMAGE_HEIGHT|SEPARATE_COMPILE)=|# CONFIG_(USER_TEST_GPIO|USER_TEST_CAMERA|SEPARATE_COMPILE))' .config
make BOARD=edu-riscv64 clean
set -o pipefail
make BOARD=edu-riscv64 -j2 2>&1 | tee /home/xiuos/smart-access-build.log
ls -lh build/XiZi-edu-riscv64.bin
sha256sum build/XiZi-edu-riscv64.bin
```

确认完整编译和链接成功，不使用失败构建残留的旧 bin。将本次 bin 传到 Windows，再用 `Get-FileHash -Algorithm SHA256` 与 Ubuntu 结果核对。只需该 bin 烧录，模型已内嵌。

## 烧录与运行

开发板 USB 接 Windows，关闭后端及 MobaXterm 串口会话，使用已成功的 K-Flash 板型和实际 COM 端口、115200 波特率烧录。完成后关闭 K-Flash，用 MobaXterm 115200、8N1 检查启动。若热复位后黑屏，已有完全断电约 10 秒再上电恢复的观察，尚未确认根因。

固定横放：USB 在上、摄像头在右下。启动日志应含 `diagnostics=v2.3.1` 和 `preview=ROT180`。Shell 大小写敏感：

```text
Help
Face
Face lcd
Face start
Door ping
```

`Face lcd` 完成后应显示色条；`Face start` 在后端尚未握手时提供预览和人脸框。`FACE_STABLE` 表示单脸质量状态，不表示身份确认。APP_ACCESS_FACE 启用后普通 Door allow/deny 返回 FACE_REQUIRED，现场授权经当前 FaceDecision 请求执行。

确认预览后关闭 MobaXterm，再按根 README 启动 Windows 后端，串口由后端独占。后端握手自动启动识别；模板数据库位于 Windows，无需 TF 卡或板上网口。授权成功应核对同一请求的 ACK ALLOW|OK、LED 约 3 秒及 STATE LOCKED；仍可能出现请求取消，详见当前待联调事项。

## 历史文件

apply_build_fix.py、apply_runtime_fix.py、install_capture_diagnostics.py、install_lcd_diagnostics.py、install_preview_fix.py 及对应文档用于追溯早期增量升级，部分依赖历史版本哈希。首次从本分支安装直接使用上面的完整安装器，不需要按版本逐个应用。
