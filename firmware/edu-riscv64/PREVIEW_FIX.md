# v2.3.1：固定有效安装方向，转正 LCD 预览

> 以下保留该阶段的历史记录。当前完成范围和未解决问题以根 README、docs/待联调事项.md 为准。

日期：2026-10-15。增量包适用于已经安装 v2.3 的学校 edu-riscv64 工程。

## 实板证据与原因

用户先在原竖放方向看到完整正脸，但持续 NO_FACE。将整块板顺时针转 90°后，摄像头位于右下方、USB 线从上方接入，出现红色框；三次查询均 FACE_STABLE，帧号 4698、4718、4739，feature=143ms，error=NONE。这证明该安装方向下，当前模型能检测到单张人脸，应用的单脸质量门槛通过，特征提取代码已运行。尚未验证真实身份、权限或 LED 自动开锁。

模型直接读取 RGB 缓冲，LCD 驱动另外设置了显示坐标方向。屏幕看起来正立不等于模型输入正立。实际旋转对照支持方向是这次检测差异的关键因素，但没有取得原始输入快照，不能仅凭照片精确断言原缓冲应旋转多少度。只做过原方向与一次 90°方向对照，不能声称其他所有方向均不可用。

当前有效姿态的照片里，LCD 中人像倒置。v2.3.1 只在 DisplayFrame 中反向读取 RGB565 行、反向排列行内像素，让预览旋转 180°；红框先画入同一缓冲，所以随画面一起旋转。复用已有静态 640 字节行缓冲。模型输入 RGB、检测框原坐标、特征算法、阈值、串口授权、摄像头驱动和配置均保持当前有效版本。

这次修正用于让已有效的安装姿态具有正立预览，不提供任意安装方向下的自动检测。请固定开发板，保持 USB 在上、摄像头在右下；若以后采用竖装，应另外验证输入旋转/缩放及框坐标映射，再重新评估模板。

## Ubuntu 安装

将完整 `smart-access-face-v2.3.1-preview-fix.zip` 放入 `/home/xiuos/Downloads`，脚本包含在 ZIP 内。

```bash
mkdir -p /home/xiuos/smart-access-face-v2.3.1-preview-fix
unzip -o /home/xiuos/Downloads/smart-access-face-v2.3.1-preview-fix.zip -d /home/xiuos/smart-access-face-v2.3.1-preview-fix
cd /home/xiuos/smart-access-face-v2.3.1-preview-fix
sha256sum -c SHA256SUMS.txt
python3 install_preview_fix.py /home/xiuos/test/xiuos
```

校验全部 OK 且安装成功后再继续。不需要 menuconfig 或重传模型。脚本支持 Python 3.6，校验旧 v2.3 face_app.c 或已安装同版源码、NMS 修正和图像尺寸。先备份完整 access_face、配置、旧 bin/ELF/map，再替换一个 face_app.c；备份位置会打印。若提示 Preview fix stopped，请保留输出，不绕过校验。

## 编译、传回与烧录

```bash
cd /home/xiuos/test/xiuos/Ubiquitous/XiZi_IIoT
make BOARD=edu-riscv64 clean
set -o pipefail
make BOARD=edu-riscv64 -j1 2>&1 | tee /home/xiuos/smart-access-face-v2.3.1-preview-build.log
echo "build exit=$?"
ls -lh build/XiZi-edu-riscv64.bin
sha256sum build/XiZi-edu-riscv64.bin
```

echo 紧接 make 执行。确认编译链接成功、退出码 0，再将新 bin 传回 Windows，另存为：

```text
D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.3.1-preview-fix.bin
```

Windows 核对新 bin 的 SHA256 与 Ubuntu 一致：

```powershell
Get-FileHash 'D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.3.1-preview-fix.bin' -Algorithm SHA256
```

关闭后端与 MobaXterm，沿用实际 COM、成功的 K-Flash 115200 设置烧录新 bin。之后关闭 K-Flash，再打开 MobaXterm，按 Reset。

## 本次上板验收

1. 开发板保持已出现红框的横放姿态：USB 在上，摄像头在右下。
2. 启动信息应含 `diagnostics=v2.3.1; preview=ROT180`。
3. 后端保持关闭，输入 `Face start`，观察画面是否正立、人脸红框是否仍正确跟随。
4. 完整正脸入镜后，间隔约 5 秒查询两次 `Face`，返回输出与屏幕照片。期望仍有 FACE_STABLE、feature 耗时，error=NONE。若转正显示后检测结果变化，保留日志并检查是否移动了物理安装方向。

2026-10-15 用户已完成 v2.3.1 的 Ubuntu 编译、烧录及实板反馈：照片显示人像正立、红框位于脸部。两次 Face 为 FACE_STABLE，frames=137/247，feature=142/141ms，preview=ROT180，error=NONE；另有 frame=300 的采样输出。预览修正与检测/特征流程在本次反馈中可运行，尚未验证串口完整特征传输、真实身份、权限或自动 LED 解锁。已分发增量 ZIP 保持原内容；此段是工作目录中的后续记录。

## 下一阶段

固定安装方向后，关闭串口终端，将串口交给 Windows 后端；按主 README 启动板端模式，确认在线与 FREADY，然后依次完成板端训练模板、独立验证、陌生人负例、阈值校准和 D001 通行权限。三次 FACE_STABLE 不代表连续五帧请求门槛、模板匹配或权限验证已经通过。

恢复时可使用本次备份的旧 bin，或恢复 access_face 后重新 clean/build。已分发 v2.3 ZIP 保持原内容和校验值。
