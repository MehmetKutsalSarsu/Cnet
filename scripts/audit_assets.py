
from __future__ import annotations

import os
import sys

# Make project imports work when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _PROJECT_ROOT)

from src.agents.coder.part_library import PART_LIBRARY_MAP  # noqa: E402


def audit() -> int:
    kicad_sym_dir = os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_sym")
    kicad_lib_dir = os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_lib")

    if not os.path.isdir(kicad_sym_dir):
        print(f"FAIL: {kicad_sym_dir} does not exist")
        return 1

    required = {(lib, sym) for _, (lib, sym) in PART_LIBRARY_MAP.items()}
    missing: list[str] = []
    found: list[str] = []

    for lib, sym in sorted(required):
        custom_file = os.path.join(kicad_lib_dir, f"{sym}.kicad_sym")
        if os.path.exists(custom_file):
            found.append(f"{lib}:{sym} (custom: kicad_lib/{sym}.kicad_sym)")
            continue

        lib_file = os.path.join(kicad_sym_dir, f"{lib}.kicad_sym")
        if not os.path.exists(lib_file):
            missing.append(f"{lib}:{sym} — no kicad_lib file and no {lib}.kicad_sym")
            continue
        with open(lib_file, encoding="utf-8", errors="replace") as f:
            content = f.read()
        if f'"{sym}"' not in content:
            missing.append(f"{lib}:{sym} — symbol not in {lib}.kicad_sym")
        else:
            found.append(f"{lib}:{sym}")

    print(f"Audited {len(required)} required (library, symbol) pairs")
    print(f"  Found:   {len(found)}")
    print(f"  Missing: {len(missing)}")

    if missing:
        print("\nMISSING:")
        for m in missing:
            print(f"  - {m}")
        return 1

    print("\nOK: assets/librarys/ satisfies PART_LIBRARY_MAP")
    return 0


if __name__ == "__main__":
    sys.exit(audit())
