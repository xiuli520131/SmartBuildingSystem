#!/usr/bin/env python3
"""Fix XiZi pthread compatibility without reinstalling or changing .config."""
import argparse
import datetime
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="/home/xiuos/test/xiuos")
    args = parser.parse_args()
    app = Path(args.repository).expanduser().resolve() / "APP_Framework/Applications"
    path = app / "access_face/face_app.c"
    try:
        original = path.read_text(encoding="utf-8")
        replacements = (
            ("    pthread_attr_t attributes;", "    pthread_attr_t attributes = {0};\n    struct sched_param priority = {0};"),
            ("    pthread_attr_init(&attributes);", "    pthread_attr_init(&attributes);\n    /* XiZi attr_init does not initialize the priority. */\n    priority.sched_priority = 11;\n    pthread_attr_setschedparam(&attributes,&priority);"),
            ("    pthread_attr_destroy(&attributes);", "    /* XiZi has no attr_destroy; attributes own no resources. */"),
        )
        if "pthread_attr_destroy(&attributes);" in original:
            fixed = original
            for before, after in replacements:
                if fixed.count(before) != 1:
                    raise ValueError("Unexpected source anchor: " + before.strip())
                fixed = fixed.replace(before, after, 1)
            backup = Path.home() / "xiuos-backups" / datetime.datetime.now().strftime("face-build-fix-%Y%m%d-%H%M%S-%f")
            backup.mkdir(parents=True)
            shutil.copy2(str(path), str(backup / "face_app.c"))
            path.write_text(fixed, encoding="utf-8")
            print("Backup: {}".format(backup))
        elif ("pthread_attr_t attributes = {0};" in original and
              "pthread_attr_setschedparam(&attributes,&priority);" in original):
            print("pthread compatibility fix already present")
        else:
            raise ValueError("Source differs; preserve it and inspect")
        # ZIP timestamps may be ahead of the VM. Contents stay unchanged.
        for directory in (app / "access_face", app / "access_control"):
            for item in directory.iterdir():
                if item.is_file() and (item.suffix in (".c", ".h") or item.name in ("Makefile", "Kconfig")):
                    item.touch()
        print("Fix applied. Configuration unchanged. Clean and rebuild in XiZi_IIoT.")
    except (OSError, ValueError) as error:
        parser.exit(1, "Build fix stopped: {}\n".format(error))


if __name__ == "__main__":
    main()
