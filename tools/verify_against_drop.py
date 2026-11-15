#!/usr/bin/env python3
"""Verify this toolkit against an official Holux vendor drop.

Point this at the folder of official Holux packages (the one containing
`DA_v3.8.4.bin`, `brom.dll`, the two firmware `.bin` files, ...) and it will:

1. identify every file using the shipped manifest
   (`src/m241/data/vendor_drop.json`) and check its SHA256,
2. detect and re-patch both stock firmware images, comparing the result against
   the hashes recorded in `src/m241/data/firmware_profiles.json`,
3. dump both images through the built-in MT3318 simulator and compare the
   roundtrip byte for byte,
4. round-trip the DA binary through the simulated BootROM write path,
5. print a summary table.

Nothing is written outside a temporary directory, and no proprietary bytes are
copied into the repository.

Usage:
    python tools/verify_against_drop.py ../holux
    python tools/verify_against_drop.py ../holux --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from m241.firmware import (  # noqa: E402
    FirmwareImage,
    apply_patch_plan,
    build_patch_plan,
    detect_profile,
    verify_patched,
)
from m241.firmware.profiles import OFFICIAL_SHA256  # noqa: E402
from m241.protocol import BASE_ADDR_DA, DownloadAgent, MtkBootRom, dump_firmware  # noqa: E402
from m241.protocol.simulator import RAM_BASE, FakeDevice  # noqa: E402

MANIFEST = REPO_ROOT / "src" / "m241" / "data" / "vendor_drop.json"
MAX_HASH_BYTES = 8 * 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def walk(drop: Path) -> list[Path]:
    return sorted(p for p in drop.rglob("*") if p.is_file())


def index_drop(drop: Path) -> dict[str, list[Path]]:
    """basename(lower) -> [paths] so we tolerate the vendor's ugly folder names."""
    out: dict[str, list[Path]] = {}
    for path in walk(drop):
        out.setdefault(path.name.lower(), []).append(path)
    return out


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        if ok:
            print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
        else:
            print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))

    @property
    def ok(self) -> bool:
        return all(check["ok"] for check in self.checks)


def check_manifest(drop: Path, report: Report) -> list[Path]:
    manifest = load_manifest()
    index = index_drop(drop)
    found_firmware: list[Path] = []
    print(f"\n== vendor drop: {drop} ==")
    for entry in manifest["files"]:
        role = entry["role"]
        basename = entry["basename"]
        candidates = index.get(basename.lower(), [])
        if not candidates:
            # Not every drop carries the drivers; only warn for optional roles.
            required = bool(entry.get("required_for"))
            report.add(
                f"{role}: {basename}", not required, "not present" if not required else "MISSING"
            )
            continue
        matched = None
        for candidate in candidates:
            if candidate.stat().st_size != entry["size"]:
                continue
            if sha256_of(candidate) == entry["sha256"]:
                matched = candidate
                break
        if matched is None:
            report.add(
                f"{role}: {basename}",
                False,
                f"{len(candidates)} candidate(s) but no size+sha256 match",
            )
            continue
        report.add(f"{role}: {basename}", True, matched.relative_to(drop).as_posix())
        if role == "firmware":
            found_firmware.append(matched)
    return found_firmware


def check_patching(images: list[Path], report: Report, tmp: Path) -> None:
    print("\n== firmware patch verification ==")
    if not images:
        report.add("patching", False, "no stock firmware images in the drop")
        return
    for path in images:
        digest = sha256_of(path)
        profile_name = OFFICIAL_SHA256.get(digest)
        label = profile_name or path.name[:40]
        try:
            image = FirmwareImage.from_path(path)
            profile = detect_profile(image)
            plan = build_patch_plan(image)
            patched = apply_patch_plan(image, plan)
            verify_patched(patched, plan)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            report.add(f"patch {label}", False, f"{type(exc).__name__}: {exc}")
            continue
        got = patched.sha256()
        want = profile.patched_sha256
        report.add(
            f"patch {label}",
            bool(want) and got == want,
            f"profile={profile.name} out={got[:16]}... expected={want[:16]}..."
            if want
            else "no expected hash",
        )
        target = tmp / f"{label}_patched.bin"
        patched.save(target)
        report.add(
            f"patch {label} writes atomically",
            target.exists() and sha256_of(target) == got,
            str(target.name),
        )


def check_simulator(images: list[Path], report: Report) -> None:
    print("\n== simulator roundtrip ==")
    if not images:
        report.add("simulator roundtrip", False, "no stock firmware images in the drop")
        return
    for path in images:
        payload = path.read_bytes()
        device = FakeDevice(bytes(payload.ljust(0x200000, b"\xff")))
        brom = MtkBootRom("SIM", serial_factory=device.factory, timeout=0.5, sync_timeout=2.0)
        try:
            brom.start()
            dumped = dump_firmware(brom, length=len(payload))
        finally:
            brom.close()
        report.add(
            f"dump roundtrip {path.name[:40]}",
            dumped == payload,
            f"{len(dumped)} bytes",
        )


def check_da_upload(drop: Path, report: Report) -> None:
    print("\n== simulated DA upload ==")
    index = index_drop(drop)
    candidates = index.get("da_v3.8.4.bin", [])
    if not candidates:
        report.add("DA upload", False, "DA_v3.8.4.bin not found in drop")
        return
    da_bytes = candidates[0].read_bytes()
    device = FakeDevice(b"\xff" * 0x40000)
    brom = MtkBootRom("SIM", serial_factory=device.factory, timeout=0.5, sync_timeout=2.0)
    da = DownloadAgent(brom)
    try:
        brom.start()
        info = da.start(str(candidates[0]))
    finally:
        brom.close()
    stored = bytes(device.ram[BASE_ADDR_DA - RAM_BASE : BASE_ADDR_DA - RAM_BASE + len(da_bytes)])
    trimmed = da_bytes.rstrip(b"\x00")
    padded = trimmed + b"\x00" * (-len(trimmed) % 4)
    report.add(
        "DA loaded at 0x00100800",
        stored[: len(padded)] == padded,
        f"da_version=0x{info['da_version']:04x} flash_size={info['flash_size']}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("drop", type=Path, help="official Holux package folder")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--skip-simulator",
        action="store_true",
        help="skip the (slower) simulator roundtrips",
    )
    args = parser.parse_args(argv)

    if not args.drop.is_dir():
        print(f"[!] not a directory: {args.drop}", file=sys.stderr)
        return 2

    report = Report()
    with tempfile.TemporaryDirectory(prefix="m241-verify-") as tmp_name:
        tmp = Path(tmp_name)
        images = check_manifest(args.drop, report)
        check_patching(images, report, tmp)
        if not args.skip_simulator:
            check_simulator(images, report)
        check_da_upload(args.drop, report)

    passed = sum(1 for check in report.checks if check["ok"])
    total = len(report.checks)
    print(f"\n{'=' * 60}\n{passed}/{total} checks passed")

    if args.json:
        print(
            json.dumps(
                {"ok": report.ok, "passed": passed, "total": total, "checks": report.checks},
                indent=2,
            )
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
