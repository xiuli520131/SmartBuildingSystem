#!/usr/bin/env python3
"""Install capture diagnostics on the known v2.1 school source; Python 3.6+."""
import argparse
import datetime
import hashlib
from pathlib import Path
import shutil


PREVIOUS = {
    "face_app.c": "9458abb20dc28e34e766d50a768968a49181ce540c2f9f18ada4d1e3577840ed",
    "region_layer.c": "90f34c7618f0767fbae8a6be11bf7840ce81dc83b007ab3f9ab0605ee185f2f0",
    "region_layer.h": "6315a9440458793e32edf3e0abda5e8b19c84bfca38f256716b1daac30a81c7e",
}
BUILD_FIX_FACE = "b1a12d3cf5c511a308a123e2b747d288c53440622c3d423fd5322c7b4b392bea"


def digest(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="/home/xiuos/test/xiuos")
    args = parser.parse_args()
    root = Path(args.repository).expanduser().resolve()
    app = root / "APP_Framework/Applications/access_face"
    kernel = root / "Ubiquitous/XiZi_IIoT"
    package = Path(__file__).resolve().parent
    try:
        payload = {}
        if "SMART_ACCESS_SHARED_CLOCK_V1" in (package / "access_face/face_app.c").read_text(encoding="utf-8"):
            raise ValueError("This source tree contains v2.3; use install_lcd_diagnostics.py for an installed v2.2 board")
        for name, previous in PREVIOUS.items():
            old = (app / name).read_text(encoding="utf-8")
            new = (package / "access_face" / name).read_text(encoding="utf-8")
            allowed = {previous, digest(new)}
            if name == "face_app.c":
                allowed.add(BUILD_FIX_FACE)
            if digest(old) not in allowed:
                raise ValueError("{} differs from v2.1 and this package; preserve it for inspection".format(name))
            payload[name] = new
        if "SMART_ACCESS_CAPTURE_DIAG_V1" not in payload["face_app.c"]:
            raise ValueError("Missing diagnostics payload")
        if "SMART_ACCESS_NMS_HEAP_V1" not in payload["region_layer.c"]:
            raise ValueError("Missing NMS stack fix")
        config = (kernel / ".config").read_text(encoding="utf-8")
        if "CONFIG_BOARD_EDU_RISCV64_EVB=y" not in config or "CONFIG_APP_ACCESS_FACE=y" not in config:
            raise ValueError("Expected the current edu-riscv64 face configuration")
        driver = kernel / "board/edu-riscv64/third_party_driver/dvp/connect_dvp.c"
        driver_text = driver.read_text(encoding="utf-8")
        if "SMART_ACCESS_FRAME_FINISH_V1" not in driver_text:
            raise ValueError("Expected the installed frame-finish camera driver")

        backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-capture-diag-%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        print("Backup: {}".format(backup), flush=True)
        shutil.copytree(str(app), str(backup / "access_face"))
        shutil.copy2(str(driver), str(backup / "connect_dvp.c"))
        shutil.copy2(str(kernel / ".config"), str(backup / "config.before"))
        for path in (kernel / "build").glob("XiZi-edu-riscv64.*"):
            if path.is_file() and path.suffix in (".elf", ".map", ".bin"):
                shutil.copy2(str(path), str(backup / path.name))
                if path.suffix == ".bin":
                    print("Previous bin SHA256: {}".format(hashlib.sha256(path.read_bytes()).hexdigest()))
        print("Current configuration:")
        prefixes = ("CONFIG_BSP_USING_CAMERA=", "CONFIG_BSP_USING_KPU=", "CONFIG_DVP_",
                    "CONFIG_IMAGE_", "CONFIG_SVGA_", "CONFIG_UXGA_", "CONFIG_BSP_LCD_",
                    "CONFIG_KPU_DMA", "CONFIG_SEPARATE_COMPILE=", "CONFIG_USER_TEST_CAMERA=")
        for line in config.splitlines():
            if line.startswith(prefixes):
                print(line)
        for name, contents in payload.items():
            (app / name).write_text(contents, encoding="utf-8")
        for directory in (app, app.parent / "access_control"):
            for path in directory.iterdir():
                if path.is_file() and path.suffix in (".c", ".h"):
                    path.touch()
        print("Capture diagnostics v2.2 installed; camera driver/configuration/model unchanged.")
        print("Clean and rebuild. This package adds evidence; it does not claim to fix the timeout.")
    except (OSError, ValueError) as error:
        parser.exit(1, "Capture diagnostics stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
