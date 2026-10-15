#!/usr/bin/env python3
"""Back up the crashed build and move the RV64 NMS workspace off the task stack."""
import argparse
import datetime
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="/home/xiuos/test/xiuos")
    args = parser.parse_args()
    root = Path(args.repository).expanduser().resolve()
    app = root / "APP_Framework/Applications/access_face"
    kernel = root / "Ubiquitous/XiZi_IIoT"
    payload = Path(__file__).resolve().parent / "access_face"
    try:
        sources = {}
        for name in ("region_layer.c", "region_layer.h"):
            target = app / name
            old = target.read_text(encoding="utf-8")
            new = (payload / name).read_text(encoding="utf-8")
            if name.endswith(".c"):
                if ("sortable_box_t s[boxes_number];" not in old and
                        "SMART_ACCESS_NMS_HEAP_V1" not in old):
                    raise ValueError("Unexpected region_layer.c; preserve it for inspection")
                if "SMART_ACCESS_NMS_HEAP_V1" not in new or "sortable_box_t s[boxes_number];" in new:
                    raise ValueError("Invalid patch payload")
            elif "} region_layer_t;" not in old or "void *nms_workspace;" not in new:
                raise ValueError("Unexpected region_layer.h")
            # Validate that this is the installed access-face adapter, not the school framework.
            if "#define region_layer_init AccessRegionInit" not in (payload / "region_layer.h").read_text(encoding="utf-8"):
                raise ValueError("Invalid access-face adapter")
            sources[name] = new

        backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-runtime-fix-%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        print("Backup: {}".format(backup), flush=True)
        for name in ("region_layer.c", "region_layer.h", "face_app.c", "face_feature.c", "face_feature.h"):
            path = app / name
            if path.is_file():
                shutil.copy2(str(path), str(backup / name))
        for name in ("XiZi-edu-riscv64.elf", "XiZi-edu-riscv64.bin", "XiZi-edu-riscv64.map"):
            path = kernel / "build" / name
            if path.is_file():
                shutil.copy2(str(path), str(backup / name))
        if (kernel / ".config").is_file():
            shutil.copy2(str(kernel / ".config"), str(backup / "config.before"))

        elf = backup / "XiZi-edu-riscv64.elf"
        prefix = Path("/opt/gnu-mcu-eclipse/riscv-none-gcc/8.2.0-2.1-20190425-1021/bin/riscv-none-embed-")
        report = ["Original crash PC: 0x8004246c\n"]
        if elf.is_file():
            commands = (
                [str(prefix) + "addr2line", "-a", "-f", "-C", "-i", "-e", str(elf), "0x8004246c"],
                [str(prefix) + "objdump", "-d", "--start-address=0x80042440", "--stop-address=0x80042490", str(elf)],
            )
            for command in commands:
                try:
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            universal_newlines=True, timeout=30)
                    report.extend(["\n{}\n".format(Path(command[0]).name), result.stdout,
                                   "exit={}\n".format(result.returncode)])
                except (OSError, subprocess.TimeoutExpired) as error:
                    report.append("Symbol collection unavailable: {}\n".format(error))
        else:
            report.append("No original ELF at build/XiZi-edu-riscv64.elf; exact PC mapping unavailable.\n")
        diagnostic = "".join(report)
        (backup / "crash-address.txt").write_text(diagnostic, encoding="utf-8")
        print(diagnostic, flush=True)

        for name, contents in sources.items():
            (app / name).write_text(contents, encoding="utf-8")
        # Avoid future ZIP/VM timestamps preventing recompilation.
        for directory in (app, app.parent / "access_control"):
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file() and path.suffix in (".c", ".h"):
                        path.touch()
        print("Runtime source fix installed. Clean and rebuild; board behavior is not yet verified.")
    except (OSError, ValueError) as error:
        parser.exit(1, "Runtime fix stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
