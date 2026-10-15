#!/usr/bin/env python3
"""Ubuntu Python 3.6+: backed-up integration into the school's complete XiZi tree."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct


def insert(text, marker, anchor, addition, name):
    if marker in text:
        return text
    if text.count(anchor) != 1:
        raise ValueError("Unexpected {}: expected one integration anchor".format(name))
    return text.replace(anchor, anchor + addition, 1)


def patch_camera(text):
    marker = "SMART_ACCESS_FRAME_FINISH_V1"
    if marker in text:
        return text
    start = text.index("static int on_irq_dvp(")
    end = text.index("struct DvpRegConfigureInfo", start)
    old = text[start:end]
    if (old.count("dvp_start_convert();") != 1 or
            old.count("shoot_flag=STOP_SHOOT;") != 1 or
            "DVP_STS_FRAME_FINISH" not in old):
        raise ValueError("Camera IRQ differs from the inspected school driver")
    replacement = '''/* SMART_ACCESS_FRAME_FINISH_V1: completion means DMA finished. */
static int on_irq_dvp(int irq, void *arg)
{
    if (dvp_get_interrupt(DVP_STS_FRAME_FINISH)) {
        dvp_clear_interrupt(DVP_STS_FRAME_FINISH);
        if (shoot_flag == ONLY_ONE_SHOOT) {
            __sync_synchronize();
            shoot_flag = STOP_SHOOT;
        }
    }
    if (dvp_get_interrupt(DVP_STS_FRAME_START)) {
        if (shoot_flag > STOP_SHOOT) dvp_start_convert();
        dvp_clear_interrupt(DVP_STS_FRAME_START);
    }
    return 0;
}

'''
    text = text[:start] + replacement + text[end:]
    declaration = "static int shoot_flag = 0;"
    if text.count(declaration) != 1:
        raise ValueError("Unexpected shoot_flag declaration")
    text = text.replace(declaration, "static volatile int shoot_flag = 0;", 1)
    # Arm the flag before enabling IRQ, so the first frame cannot race with it.
    pattern = (r"(dvp_config_interrupt\(DVP_CFG_START_INT_ENABLE \| "
               r"DVP_CFG_FINISH_INT_ENABLE, 1\);\s*)shoot_flag=ONLY_ONE_SHOOT;")
    text, count = re.subn(pattern, r"shoot_flag=ONLY_ONE_SHOOT;\n        \1", text)
    if count != 1:
        raise ValueError("Unexpected single-shot IRQ ordering")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="/home/xiuos/test/xiuos")
    args = parser.parse_args()
    root = Path(args.repository).expanduser().resolve()
    package = Path(__file__).resolve().parent
    app = root / "APP_Framework/Applications"
    kernel = root / "Ubiquitous/XiZi_IIoT"
    driver = kernel / "board/edu-riscv64/third_party_driver/dvp/connect_dvp.c"
    try:
        config = (kernel / ".config").read_text(encoding="utf-8")
        if "CONFIG_BOARD_EDU_RISCV64_EVB=y" not in config or "CONFIG_SEPARATE_COMPILE=y" in config:
            raise ValueError("Configure edu-riscv64 with combined compilation first")
        model_path = app / "knowing_app/k210_detect_entry/face.kmodel"
        model = model_path.read_bytes()
        if len(model) != 388776:
            raise ValueError("Expected the school's 388776-byte face.kmodel")
        version, flags, arch, layers, maximum, memory, outputs = struct.unpack_from("<7I", model)
        if version != 3 or arch != 0 or outputs != 1 or not 0 < memory <= 4 * 1024 * 1024:
            raise ValueError("Unsupported model header: version/arch/outputs/memory")
        output_address, output_size = struct.unpack_from("<2I", model, 28)
        if output_size != 20 * 15 * 30 * 4 or output_address + output_size > memory:
            raise ValueError("Model output does not match face.json")
        meta = json.loads(model_path.with_suffix(".json").read_text(encoding="utf-8"))
        if (meta.get("net_input_size") != [240, 320] or meta.get("net_output_shape") != [20, 15, 30]
                or meta.get("labels") != ["face"]):
            raise ValueError("face.json does not match this adapter")

        main_path, make_path, kconfig_path = (app / name for name in ("main.c", "Makefile", "Kconfig"))
        main_text = main_path.read_text(encoding="utf-8")
        main_text = insert(main_text, '#include "access_control/door_app.h"', "#include <transform.h>",
                           '\n#ifdef APP_ACCESS_CONTROL\n#include "access_control/door_app.h"\n#endif', "main.c")
        main_text = insert(main_text, '#include "access_face/face_app.h"', "#include <transform.h>",
                           '\n#ifdef APP_ACCESS_FACE\n#include "access_face/face_app.h"\n#endif', "main.c")
        if "DoorAppInit();" not in main_text:
            main_text = insert(main_text, "DoorAppInit();", "FrameworkInit();",
                               "\n#ifdef APP_ACCESS_CONTROL\n    DoorAppInit();\n#endif", "main.c")
        if "FaceAppInit();" not in main_text:
            pattern = r"(#ifdef APP_ACCESS_CONTROL\s+DoorAppInit\(\);\s+#endif)"
            main_text, count = re.subn(pattern, r"\1\n#ifdef APP_ACCESS_FACE\n    FaceAppInit();\n#endif", main_text)
            if count != 1:
                raise ValueError("Unexpected DoorAppInit integration; preserve and inspect main.c")
        make_text = make_path.read_text(encoding="utf-8")
        for name, symbol in (("access_control", "APP_ACCESS_CONTROL"), ("access_face", "APP_ACCESS_FACE")):
            make_text = insert(make_text, "CONFIG_" + symbol, "SRC_FILES := main.c",
                               "\n    ifeq ($(CONFIG_{}),y)\n        SRC_DIR += {}\n    endif\n".format(symbol, name), "Makefile")
        kconfig = kconfig_path.read_text(encoding="utf-8")
        for name in ("access_control", "access_face"):
            line = 'source "$APP_DIR/Applications/{}/Kconfig"'.format(name)
            kconfig = insert(kconfig, line, 'source "$APP_DIR/Applications/app_test/Kconfig"',
                             "\n    " + line, "Kconfig")
        driver_text = patch_camera(driver.read_text(encoding="utf-8"))
        replacements = {main_path: main_text, make_path: make_text, kconfig_path: kconfig, driver: driver_text}
        source_sets = {}
        for name in ("access_control", "access_face"):
            files = [p for p in (package / name).iterdir() if p.suffix in (".c", ".h") or p.name in ("Kconfig", "Makefile")]
            if not files or not (package / name / "Makefile").is_file():
                raise ValueError("Incomplete package: " + name)
            target = app / name
            if target.exists():
                allowed = {p.name for p in files} | {"face_model_data.c"}
                unexpected = [p.name for p in target.iterdir() if p.is_file() and
                              (p.suffix in (".c", ".h") or p.name in ("Kconfig", "Makefile")) and p.name not in allowed]
                if unexpected:
                    raise ValueError("Additional application sources must be preserved: " + ", ".join(unexpected))
            source_sets[name] = files

        backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-install-%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        for path in list(replacements) + [kernel / ".config", kernel / "board/edu-riscv64/.config",
                                          kernel / "board/edu-riscv64/xsconfig.h", kernel / "build/XiZi-edu-riscv64.bin"]:
            if path.is_file():
                dest = backup / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(path), str(dest))
        for name in source_sets:
            if (app / name).exists():
                shutil.copytree(str(app / name), str(backup / "APP_Framework/Applications" / name))
        print("Backup: {}".format(backup), flush=True)
        for path, text in replacements.items():
            path.write_text(text, encoding="utf-8")
        for name, files in source_sets.items():
            (app / name).mkdir(exist_ok=True)
            for path in files:
                # Keep destination timestamps local: ZIP time may be ahead of the VM.
                shutil.copyfile(str(path), str(app / name / path.name))
        generated = app / "access_face/face_model_data.c"
        with generated.open("w", encoding="utf-8") as output:
            output.write("/* Embedded from the school's face.kmodel; SHA256 {} */\n".format(hashlib.sha256(model).hexdigest()))
            output.write("const unsigned char access_face_model[] __attribute__((aligned(256))) = {\n")
            for offset in range(0, len(model), 24):
                output.write(",".join("0x{:02x}".format(value) for value in model[offset:offset+24]) + ",\n")
            output.write("};\nconst unsigned int access_face_model_size = sizeof(access_face_model);\n")
        print("Model embedded: {} bytes; KPU main memory {} bytes; SHA256 {}".format(len(model), memory, hashlib.sha256(model).hexdigest()))
        print("Installed source only. Next: menuconfig APP_ACCESS_FACE; disable USER_TEST_GPIO and USER_TEST_CAMERA; build.")
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.exit(1, "Installation stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
