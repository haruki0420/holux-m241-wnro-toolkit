"""Smoke-test the built wheel in an isolated directory.

Run from the repository root after `python -m build`:

    python tools/check_wheel.py

It installs `dist/*.whl` into a temporary target with `--no-deps --target`,
puts that target first on sys.path, and exercises the CLI plus the package data
files. This mirrors the CI "build" job locally.
"""

from __future__ import annotations

import glob
import pathlib
import subprocess
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
VENV_PY = pathlib.Path(sys.executable)


def main() -> int:
    wheels = glob.glob(str(REPO / "dist" / "*.whl"))
    if not wheels:
        print("no wheel in dist/ - run `python -m build` first")
        return 1
    wheel = wheels[0]
    names = set(zipfile.ZipFile(wheel).namelist())
    required = {"m241/data/firmware_profiles.json", "m241/data/vendor_drop.json"}
    missing = sorted(required - names)
    print(f"wheel: {pathlib.Path(wheel).name}")
    print(f"data files: {'OK' if not missing else 'MISSING ' + str(missing)}")
    if missing:
        return 1

    with tempfile.TemporaryDirectory(prefix="m241-wheel-") as target:
        install = subprocess.run(
            [
                str(VENV_PY),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                "--target",
                target,
                wheel,
            ],
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            print(install.stdout)
            print(install.stderr)
            return 1
        code = (
            "import sys;"
            f"sys.path.insert(0, {target!r});"
            "import m241;"
            "from m241.cli import main;"
            "print('version', m241.__version__);"
            "print('profiles', [p.name for p in __import__('m241.firmware', fromlist=['x']).list_profiles()]);"
            "rc = main(['selftest']);"
            "print('selftest rc', rc);"
            "sys.exit(rc)"
        )
        run = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True)
        print(run.stdout, end="")
        if run.returncode != 0:
            print(run.stderr, end="")
        return run.returncode


if __name__ == "__main__":
    sys.exit(main())
