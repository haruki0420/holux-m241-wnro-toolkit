"""Portability guards.

These tests do not check behaviour; they check that the codebase *stays*
cross-platform. They are cheap and they catch the regressions that would
silently break Windows, Linux or macOS support:

1. a hard-coded device path (``/dev/ttyUSB0``, ``COM3``, ``C:\\...``) instead of
   going through :mod:`m241.protocol.ports`;
2. an eager ``import serial`` outside :mod:`m241.protocol`, which would make the
   offline commands (``patch``, ``logs``, ``nmea repair``) unusable on a machine
   without pyserial.

There is also a check that every platform the project claims to support is
actually documented, so a new OS assumption cannot sneak in unnoticed.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

#: Source files that are allowed to mention a concrete device path.
PATH_ALLOWLIST = {
    # The discovery module exists precisely to know what these look like.
    "m241/protocol/ports.py",
}

#: Modules allowed to import pyserial at module scope.
SERIAL_ALLOWLIST = {
    "m241/protocol/ports.py",
    "m241/protocol/brom.py",
}

#: POSIX device paths that must never be hard-coded in logic.
DEVICE_PATH_RE = re.compile(r"/dev/(ttyUSB|ttyACM|tty\.usbserial|cu\.usbserial)")
#: A quoted Windows COM port name, e.g. ``"COM3"`` or ``'COM12'``.
COM_NAME_RE = re.compile(r"""["']COM\d+["']""")


def python_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_hardcoded_serial_device_paths() -> None:
    """No module may bake in a concrete port name."""
    offenders: list[str] = []
    for path in python_files():
        name = relative(path)
        if name in PATH_ALLOWLIST:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if DEVICE_PATH_RE.search(line) or COM_NAME_RE.search(line):
                offenders.append(f"{name}:{line_no}: {stripped}")
    assert not offenders, "hard-coded device paths found:\n" + "\n".join(offenders)


def test_pyserial_is_only_imported_lazily() -> None:
    """Only the protocol package may import pyserial, and it does so lazily."""
    offenders: list[str] = []
    for path in python_files():
        name = relative(path)
        if name in SERIAL_ALLOWLIST:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped in {"import serial", "import serial as pyserial"}:
                offenders.append(f"{name}:{line_no}: top-level pyserial import")
            if stripped.startswith("from serial"):
                offenders.append(f"{name}:{line_no}: top-level pyserial import")
    assert not offenders, "eager pyserial imports found:\n" + "\n".join(offenders)


def test_offline_commands_work_without_pyserial() -> None:
    """``m241 patch``/``logs``/``nmea`` must run on a machine with no pyserial.

    A subprocess installs an import hook that makes ``serial`` unimportable, then
    runs every offline command plus ``selftest``. If any of them touches pyserial
    the run fails, which is exactly the regression we want to catch.
    """
    script = textwrap.dedent(
        """
        import sys

        class BlockSerial:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "serial" or fullname.startswith("serial."):
                    raise ModuleNotFoundError("pyserial is not installed (blocked)")
                return None

        sys.meta_path.insert(0, BlockSerial())
        for name in list(sys.modules):
            if name == "serial" or name.startswith("serial."):
                del sys.modules[name]

        sys.path.insert(0, SRC_PATH)
        from m241.cli import main

        for argv in (["patch", "--help"], ["logs", "--help"], ["nmea", "--help"],
                     ["ports", "--help"], ["logs", "capacity"],
                     ["selftest", "--json"]):
            code = main(argv)
            if code != 0:
                raise SystemExit(f"command {' '.join(argv)} exited {code}")
        if "serial" in sys.modules:
            raise SystemExit("pyserial was imported despite being blocked")
        print("offline commands ok without pyserial")
        """
    ).replace("SRC_PATH", repr(str(SRC)))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pyserial was imported" not in result.stdout


def test_platform_modules_import_on_any_os() -> None:
    """The whole package (except hardware access) imports regardless of OS."""
    modules = [
        "m241",
        "m241.errors",
        "m241.cli",
        "m241.firmware",
        "m241.firmware.image",
        "m241.firmware.profiles",
        "m241.firmware.patcher",
        "m241.logfile",
        "m241.logfile.capacity",
        "m241.logfile.export",
        "m241.logfile.reader",
        "m241.logfile.record",
        "m241.nmea",
        "m241.nmea.proxy",
        "m241.nmea.repair",
        "m241.nmea.sentence",
        "m241.protocol",
        "m241.protocol.da",
        "m241.protocol.ports",
        "m241.protocol.simulator",
    ]
    for name in modules:
        importlib.import_module(name)


def test_no_windows_drive_letter_paths() -> None:
    """No logic may hard-code a ``C:\\...`` path (tests may use one)."""
    offenders: list[str] = []
    for path in python_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"""["']C:\\\\""", line):
                offenders.append(f"{relative(path)}:{line_no}: {stripped}")
    assert not offenders, "hard-coded drive-letter paths found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("label", "needle"),
    [
        ("windows", "Windows"),
        ("linux", "Linux"),
        ("macos", "macOS"),
    ],
)
def test_documented_platforms(label: str, needle: str) -> None:
    """Every platform the project claims to support is documented."""
    doc = (REPO_ROOT / "docs" / "03-platforms.md").read_text(encoding="utf-8")
    assert needle.lower() in doc.lower(), f"{label} is not documented"


def test_readme_claims_match_reality() -> None:
    """The README's platform table and the docs must agree on what we support."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("Windows", "Linux", "macOS", "3.10"):
        assert needle in readme, f"README does not mention {needle}"
    # The capacity numbers advertised must be the ones the code computes.
    from m241.logfile.capacity import capacity

    assert capacity("2M", 16).points == 130_048
    assert capacity("2M", 20).points == 104_038
    for text in ("130,048", "104,038"):
        assert text in readme, f"README does not advertise {text}"


def test_gitignore_does_not_hide_the_source_tree() -> None:
    """`.gitignore` must not swallow tracked source.

    Regression guard: a bare ``firmware/`` pattern (intended for vendor
    firmware *images*) also matches ``src/m241/firmware/`` and silently drops a
    whole package from the repository. This is how the bug was found; the test
    keeps it from coming back.
    """
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        pytest.skip("not a git checkout")

    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--error-unmatch", "src/m241/firmware/patcher.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        pytest.skip("source tree is not tracked in this checkout")

    package_files = [path for path in (SRC / "m241").rglob("*.py")]
    assert package_files, "no package sources found"

    # Ask git itself which sources are ignored. `check-ignore --stdin` needs one
    # path per line and exits 0 when at least one path is ignored.
    paths = "\n".join(str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in package_files)
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=str(REPO_ROOT),
        input=paths,
        capture_output=True,
        text=True,
    )
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert not ignored, "git ignores source files (fix .gitignore):\n" + "\n".join(ignored)


def test_all_package_data_is_shipped() -> None:
    """Every JSON profile data file exists on disk and parses."""
    import json

    data_dir = SRC / "m241" / "data"
    files = sorted(data_dir.glob("*.json"))
    assert files, "no package data files found"
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{path.name} is not a JSON object"
