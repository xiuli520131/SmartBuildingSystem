#!/usr/bin/env python3
"""Upgrade known v2.2 sources to v2.3 LCD/clock diagnostics; Python 3.6+."""
import argparse
import datetime
import hashlib
from pathlib import Path
import shutil


PREVIOUS = {
    "face_app.c": "f87fa05da956a22cd8003a65b70a509dfbcf5b96af2855bb30d92ebc7e30a490",
    "region_layer.c": "90f34c7618f0767fbae8a6be11bf7840ce81dc83b007ab3f9ab0605ee185f2f0",
    "region_layer.h": "6315a9440458793e32edf3e0abda5e8b19c84bfca38f256716b1daac30a81c7e",
}


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
        if "SMART_ACCESS_PREVIEW_ROTATE_180_V1" in (package / "access_face/face_app.c").read_text(encoding="utf-8"):
            raise ValueError("This tree contains v2.3.1; use install_preview_fix.py for installed v2.3, or the original v2.3 ZIP for v2.2")
        payload = {}
        for name, previous in PREVIOUS.items():
            old = (app / name).read_text(encoding="utf-8")
            new = (package / "access_face" / name).read_text(encoding="utf-8")
            if digest(old) not in {previous, digest(new)}:
                raise ValueError("{} differs from v2.2 and this package; preserve it for inspection".format(name))
            payload[name] = new
        for marker in ("SMART_ACCESS_SHARED_CLOCK_V1", "SMART_ACCESS_LCD_PATTERN_V1"):
            if marker not in payload["face_app.c"]:
                raise ValueError("Missing " + marker)
        if "SMART_ACCESS_NMS_HEAP_V1" not in payload["region_layer.c"]:
            raise ValueError("Missing NMS stack fix")
        config = (kernel / ".config").read_text(encoding="utf-8")
        config_lines = set(config.splitlines())
        for required in ("CONFIG_BOARD_EDU_RISCV64_EVB=y", "CONFIG_APP_ACCESS_FACE=y",
                         "CONFIG_BSP_LCD_X_MAX=320", "CONFIG_BSP_LCD_Y_MAX=320",
                         "CONFIG_IMAGE_WIDTH=320", "CONFIG_IMAGE_HEIGHT=240"):
            if required not in config_lines:
                raise ValueError("Expected " + required)
        for conflict in ("CONFIG_SEPARATE_COMPILE=y", "CONFIG_USER_TEST_CAMERA=y"):
            if conflict in config_lines:
                raise ValueError("Disable " + conflict)
        driver = kernel / "board/edu-riscv64/third_party_driver/dvp/connect_dvp.c"
        if "SMART_ACCESS_FRAME_FINISH_V1" not in driver.read_text(encoding="utf-8"):
            raise ValueError("Expected the installed frame-finish camera driver")
        clint_header = kernel / "board/edu-riscv64/third_party_driver/include/clint.h"
        header = clint_header.read_text(encoding="utf-8")
        if "clint_get_time" not in header or "CLINT_CLOCK_DIV" not in header:
            raise ValueError("Expected the school's shared CLINT clock API")

        backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-lcd-diag-%Y%m%d-%H%M%S-%f")
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
        for name, contents in payload.items():
            (app / name).write_text(contents, encoding="utf-8")
        for directory in (app, app.parent / "access_control"):
            for path in directory.iterdir():
                if path.is_file() and path.suffix in (".c", ".h"):
                    path.touch()
        print("LCD/clock diagnostics v2.3 installed; camera driver/configuration/model unchanged.")
        print("Clean and rebuild. After flashing: Face lcd, wait for DONE, photograph bars, then Face start.")
    except (OSError, ValueError) as error:
        parser.exit(1, "LCD diagnostics stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
