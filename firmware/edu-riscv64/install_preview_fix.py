#!/usr/bin/env python3
"""Install display-only v2.3.1 on known v2.3 school sources; Python 3.6+."""
import argparse
import datetime
import hashlib
from pathlib import Path
import shutil


PREVIOUS_FACE = "a177830d3faf4fe6e789589cd1a36c1855b7797275887c03578bfe0616638c54"
REGION_HASHES = {
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
        target = app / "face_app.c"
        old = target.read_text(encoding="utf-8")
        new = (package / "access_face/face_app.c").read_text(encoding="utf-8")
        if digest(old) not in {PREVIOUS_FACE, digest(new)}:
            raise ValueError("face_app.c differs from v2.3 and this package; preserve it for inspection")
        if "SMART_ACCESS_PREVIEW_ROTATE_180_V1" not in new:
            raise ValueError("Missing display-only rotation payload")
        for name, expected in REGION_HASHES.items():
            if digest((app / name).read_text(encoding="utf-8")) != expected:
                raise ValueError("Unexpected {}: preserve the NMS stack fix".format(name))
        config_lines = set((kernel / ".config").read_text(encoding="utf-8").splitlines())
        for required in ("CONFIG_BOARD_EDU_RISCV64_EVB=y", "CONFIG_APP_ACCESS_FACE=y",
                         "CONFIG_BSP_LCD_X_MAX=320", "CONFIG_BSP_LCD_Y_MAX=320",
                         "CONFIG_IMAGE_WIDTH=320", "CONFIG_IMAGE_HEIGHT=240"):
            if required not in config_lines:
                raise ValueError("Expected " + required)
        for conflict in ("CONFIG_SEPARATE_COMPILE=y", "CONFIG_USER_TEST_CAMERA=y"):
            if conflict in config_lines:
                raise ValueError("Disable " + conflict)
        backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-preview-fix-%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        print("Backup: {}".format(backup), flush=True)
        shutil.copytree(str(app), str(backup / "access_face"))
        shutil.copy2(str(kernel / ".config"), str(backup / "config.before"))
        for path in (kernel / "build").glob("XiZi-edu-riscv64.*"):
            if path.is_file() and path.suffix in (".elf", ".map", ".bin"):
                shutil.copy2(str(path), str(backup / path.name))
                if path.suffix == ".bin":
                    print("Previous bin SHA256: {}".format(hashlib.sha256(path.read_bytes()).hexdigest()))
        target.write_text(new, encoding="utf-8")
        for directory in (app, app.parent / "access_control"):
            for path in directory.iterdir():
                if path.is_file() and path.suffix in (".c", ".h"):
                    path.touch()
        print("Preview fix v2.3.1 installed: RGB565 preview/boxes rotate 180 degrees.")
        print("Keep the confirmed landscape mounting: USB above, camera at lower right.")
        print("Clean and rebuild; camera input, model, features and permissions unchanged.")
    except (OSError, ValueError) as error:
        parser.exit(1, "Preview fix stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
