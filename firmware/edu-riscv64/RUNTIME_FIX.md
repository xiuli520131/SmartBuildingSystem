# Face start 栈容量修正（2026-10-15）

用户烧录了第一版摄像头固件，执行 Face start 后出现 Load address misaligned，异常 PC 为 0x8004246c；utask01 的 16384 字节栈显示 100%，任务列表出现异常数值。LCD 照片显示彩色噪点，没有可辨认的人像。屏幕现象本身不能证明摄像头或 LCD 损坏。

## 已发现的问题与修正

region_layer.c 的 do_nms_sort 原先声明 `sortable_box_t s[boxes_number]`。模型产生 20×15×5=1500 个候选框；RV64 上两个 int 加一个指针的结构体占 16 字节，因此数组需要 24000 字节，已经超过 FaceWorker 的整个 16384 字节栈，尚未计入其他调用。原学校示例使用 128 KiB 任务栈，之前适配时遗漏了排序数组的栈需求。

修正版将排序工作区在 region_layer_init 中分配一次，保存到 region_layer_t 的 nms_workspace；排序复用该区域，初始化失败返回错误，释放时回收。任务栈仍为 16 KiB。这样消除了已发现的 24 KB 栈数组。其他 SDK 调用的实际最大栈深度仍需上板观察。

这是一项源码修正，尚未在用户的交叉工具链或真实开发板验证。旧 ELF 地址映射用来核对这次异常位置；新固件可能还有独立的采集/显示问题，不能预先宣称屏幕恢复。

## Ubuntu 安装与重编译

先断开开发板 USB，停止 Windows 后端与串口终端。以下操作只处理 Ubuntu 中的源码，不需要连接开发板。

1. 将 smart-access-face-v2-runtime-fix.zip 作为邮件附件或共享文件传到 /home/xiuos/Downloads。
2. 解压到新目录：

```bash
mkdir -p /home/xiuos/smart-access-face-v2-runtime-fix
unzip -o /home/xiuos/Downloads/smart-access-face-v2-runtime-fix.zip -d /home/xiuos/smart-access-face-v2-runtime-fix
```

3. 保留此前编译输出，直接运行脚本：

```bash
python3 /home/xiuos/smart-access-face-v2-runtime-fix/apply_runtime_fix.py /home/xiuos/test/xiuos
```

脚本先检查修正文件和已安装源码，然后备份旧 region_layer.c/h、face_app.c、特征源码、配置、存在的旧 bin/ELF/map 至 /home/xiuos/xiuos-backups/face-runtime-fix-时间/。在写入修正前，脚本用旧 ELF 的 addr2line/objdump 自动收集 0x8004246c 的映射，打印并保存 crash-address.txt。请保留输出；旧 ELF 对应旧固件才有定位意义，后续重新编译产生的新 ELF 无法替代它。未找到 ELF 或缺少工具时会如实记录，源码修正仍可安装。

脚本只替换 access_face/region_layer.c/h，更新应用源码时间戳。配置和模型已在当前工程内，无需重新安装完整源码或重新 menuconfig。如果提示 Runtime fix stopped，先发送完整输出，不继续烧录。

4. 完整重编译，因为 region_layer_t 的结构定义改变，必须清理旧目标文件：

```bash
cd /home/xiuos/test/xiuos/Ubiquitous/XiZi_IIoT
make BOARD=edu-riscv64 clean
set -o pipefail
make BOARD=edu-riscv64 -j1 2>&1 | tee /home/xiuos/smart-access-face-v2-runtime-fix-build.log
echo "build exit=$?"
ls -lh build/XiZi-edu-riscv64.bin
sha256sum build/XiZi-edu-riscv64.bin
```

只有编译/链接完成、build exit=0 且新 bin 存在时才继续。任何错误都保留完整输出，不使用旧 bin。

5. 将新 bin 传到 Windows，保存为 D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.1.bin，与旧 v2.bin 区分。Windows 执行：

```powershell
Get-FileHash D:\SmartBuildingSystem\artifacts\riscv\smart-access-face-v2.1.bin -Algorithm SHA256
```

与 Ubuntu 的 SHA256 一致后，以原来成功的 K-Flash 设置烧录新文件：实际 COM、115200，USB 由 Windows 使用。先关闭占用 COM 的后端和 MobaXterm；烧录完关闭 K-Flash，再打开 MobaXterm 串口并复位。

6. Windows 后端暂不启动。在 MobaXterm 内按顺序执行：

```text
Face
ShowTask
Face start
```

若开始显示连续相机画面且未出现异常，再执行：

```text
Face
ShowTask
Face stop
```

发送脚本打印的旧地址映射、编译结尾、启动日志以及 Face/ShowTask 输出，并说明 LCD 是否连续显示画面。若仍崩溃，保存第一段完整异常后断开 USB；使用这次新固件对应的 ELF 定位新 PC，避免混用地址。
