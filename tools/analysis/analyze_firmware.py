#!/usr/bin/env python3
"""Offline firmware image analysis — how the numbers in this repo were derived.

This is the script that produced `docs/reference/03-firmware-offsets.md`. It needs no
hardware, no vendor DLLs and it never writes to a device; it just reads `.bin`
files you point it at.

Usage
-----
    # profile one image (or several): constants, version marker, header
    python tools/analysis/analyze_firmware.py vendor/m241_v113.bin

    # compare two images and show every differing byte cluster
    python tools/analysis/analyze_firmware.py --diff backup/v112.bin backup/v113.bin

    # explain exactly what the WNRO patch would change, without writing anything
    python tools/analysis/analyze_firmware.py --plan backup/stock.bin

Notes
-----
* Firmware images are proprietary: do not commit them, and do not paste large
  hex dumps of them into issues. Hashes and offsets are fine.
* Everything here is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
from pathlib import Path

# The GPS epoch (1980-01-06T00:00:00Z) as a Unix timestamp; the constant the
# M-241 firmware hard-codes and the one this project patches.
GPS_EPOCH_UNIX = 315964800
WNRO_SHIFT_SECONDS = 1024 * 604800  # +619315200 s == +7168 days

HEADER_FIELDS = (
    # (offset, length, name)
    (0x090, 32, "firmware family"),
    (0x0F4, 4, "firmware size (LE32)"),
    (0x108, 2, "build number (LE16)"),
    (0x148, 8, "release name"),
    (0x15A, 1, "release major"),
    (0x15B, 1, "release minor"),
    (0x190, 16, "serial port string"),
    (0x1B0, 16, "device type"),
)

VERSION_RE = re.compile(rb"V1\.[0-9]{2}")
DATE_RE = re.compile(rb"[A-Z][a-z]{2} [ 0-9][0-9] 20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_all(haystack: bytes, needle: bytes) -> list[int]:
    """Every offset of ``needle`` in ``haystack`` (overlapping-safe)."""
    out: list[int] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return out
        out.append(index)
        start = index + 1


def printable(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def describe(path: Path) -> int:
    data = path.read_bytes()
    print("=" * 78)
    print(f"{path.name}")
    print(f"  size      : {len(data)} bytes (0x{len(data):X})")
    print(f"  sha256    : {sha256(data)}")

    print("  header:")
    for offset, length, name in HEADER_FIELDS:
        if offset + length > len(data):
            continue
        raw = data[offset : offset + length]
        if length == 4 and name.endswith("(LE32)"):
            value = f"{struct.unpack('<I', raw)[0]} (0x{struct.unpack('<I', raw)[0]:X})"
        elif length == 2 and name.endswith("(LE16)"):
            value = f"{struct.unpack('<H', raw)[0]}"
        elif length == 1:
            value = f"{raw[0]}"
        else:
            value = repr(printable(raw))
        print(f"    0x{offset:03X}  {name:<22} = {value}")

    build_date = DATE_RE.search(data)
    print(f"  __DATE__  : {build_date.group().decode() if build_date else '(not found)'}")

    epoch = struct.pack("<I", GPS_EPOCH_UNIX)
    offsets = find_all(data, epoch)
    print(f"  epoch const 315964800 (LE32) at: {[hex(o) for o in offsets] or 'NONE'}")
    if len(offsets) > 1:
        print(f"    ^ the last one (0x{offsets[-1]:X}) is the effective GPS->Unix conversion site")
    patched = struct.pack("<I", GPS_EPOCH_UNIX + WNRO_SHIFT_SECONDS)
    already = find_all(data, patched)
    if already:
        print(f"  already patched epoch value at: {[hex(o) for o in already]}")

    versions = [(m.start(), m.group().decode()) for m in VERSION_RE.finditer(data)]
    print(f"  version marker(s): {[(hex(o), v) for o, v in versions] or 'NONE'}")

    for value in (619315200, 935280000):
        hits = find_all(data, struct.pack("<I", value))
        if hits:
            print(f"  constant {value} (LE32) at: {[hex(o) for o in hits]}")

    # movs rN,#1 ; lsls rN,rN,#10  -> the "+1024" arithmetic that does NOT work
    pattern = bytes([0x01, 0x21, 0x89, 0x04])
    hits = find_all(data, pattern)
    print(f"  '+1024' thumb pattern (movs r1,#1; lsls r1,r1,#10): {len(hits)} site(s)")

    # Flash geometry hint from the image size.
    print(
        f"  fits 2 MiB flash: {'yes' if len(data) <= 0x200000 else 'no'};"
        f" 4 MiB flash: {'yes' if len(data) <= 0x400000 else 'no'}"
    )
    return 0


def diff(left_path: Path, right_path: Path, *, context: int = 4, max_clusters: int = 40) -> int:
    left = left_path.read_bytes()
    right = right_path.read_bytes()
    print("=" * 78)
    print(f"diff {left_path.name}  vs  {right_path.name}")
    print(f"  sizes: {len(left)} vs {len(right)}")
    common = min(len(left), len(right))
    clusters: list[list[int]] = []
    for index in range(common):
        if left[index] == right[index]:
            continue
        if clusters and index - clusters[-1][-1] <= 32:
            clusters[-1].append(index)
        else:
            clusters.append([index])
    print(f"  {len(clusters)} differing cluster(s) in the common range")
    for cluster in clusters[:max_clusters]:
        start, end = cluster[0], cluster[-1]
        span = end - start + 1
        print(f"    0x{start:06X}-0x{end:06X}  ({len(cluster)} of {span} bytes differ)")
        if span > 4 * context:
            lo, hi = start, start + context
            print(f"      left  @0x{lo:06X}: {left[lo:hi].hex(' ')} ...")
            print(f"      right @0x{lo:06X}: {right[lo:hi].hex(' ')} ...")
            lo, hi = end - context + 1, end + 1
            print(f"      left  @0x{lo:06X}: ... {left[lo:hi].hex(' ')}")
            print(f"      right @0x{lo:06X}: ... {right[lo:hi].hex(' ')}")
        else:
            lo, hi = max(0, start - context), min(common, end + context + 1)
            print(f"      left  @0x{lo:06X}: {left[lo:hi].hex(' ')}")
            print(f"      right @0x{lo:06X}: {right[lo:hi].hex(' ')}")
    if len(clusters) > max_clusters:
        print(f"  ... {len(clusters) - max_clusters} more cluster(s) not shown")
    if len(right) > common:
        print(f"  right has {len(right) - common} extra trailing byte(s)")
    if len(left) > common:
        print(f"  left has {len(left) - common} extra trailing byte(s)")
    return 0


def plan(path: Path) -> int:
    """Show the WNRO patch without applying it (mirrors `m241 patch --dry-run`)."""
    data = path.read_bytes()
    print("=" * 78)
    print(f"WNRO patch plan for {path.name}")
    offsets = find_all(data, struct.pack("<I", GPS_EPOCH_UNIX))
    if not offsets:
        print("  no unpatched epoch constant found — wrong image, or already patched")
        return 1
    print(f"  epoch constant at {[hex(o) for o in offsets]}")
    print(f"  patching the LAST occurrence only: 0x{offsets[-1]:X}")
    print("    (patching both double-shifts the clock; verified on hardware)")
    target = offsets[-1]
    before = data[target : target + 4]
    after = struct.pack("<I", GPS_EPOCH_UNIX + WNRO_SHIFT_SECONDS)
    print(f"    {before.hex(' ')}  ->  {after.hex(' ')}")
    print(
        f"    {GPS_EPOCH_UNIX} -> {GPS_EPOCH_UNIX + WNRO_SHIFT_SECONDS}"
        f"  (+{WNRO_SHIFT_SECONDS} s = +{WNRO_SHIFT_SECONDS // 86400} days)"
    )

    versions = [(m.start(), m.group().decode()) for m in VERSION_RE.finditer(data)]
    if versions:
        offset, value = versions[0]
        print(f"  version marker at 0x{offset:X}: {value} -> V1.14 (cosmetic, optional)")

    patched = bytearray(data)
    patched[target : target + 4] = after
    if versions:
        offset, _ = versions[0]
        patched[offset : offset + 5] = b"V1.14"
    changed = [i for i in range(len(data)) if data[i] != patched[i]]
    print(f"  resulting file would change {len(changed)} byte(s) at {[hex(i) for i in changed]}")
    print(f"  resulting sha256: {sha256(bytes(patched))}")
    print("  (nothing was written)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("images", nargs="+", type=Path, help="firmware .bin file(s)")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="treat the first two images as left/right and show differing clusters",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="show what the WNRO patch would change for the first image",
    )
    args = parser.parse_args(argv)

    for image in args.images:
        if not image.exists():
            print(f"[!] not found: {image}", file=sys.stderr)
            return 2

    if args.diff:
        if len(args.images) != 2:
            print("[!] --diff needs exactly two images", file=sys.stderr)
            return 2
        return diff(args.images[0], args.images[1])
    if args.plan:
        return plan(args.images[0])

    for image in args.images:
        describe(image)
    return 0


if __name__ == "__main__":
    sys.exit(main())
