"""Compare a real dump against the official stock image and explain the deltas.

Hardware-confirmed result: a dump taken from the device differs from the official
V1.13 package in exactly 14 bytes -- the programming magic at 0x5C, a second
field at 0xF8, and a 4-byte tail at 0x63458. Those are written by the vendor
flashing tool and stripped from the released `.bin`, which is exactly what
`docs/04-firmware-profiles.md` predicted.

This tool prints the deltas and, with --normalize, writes a copy with those
runtime fields zeroed so it can be compared/patched as a stock image.

Usage:
    python tools/compare_dump.py backup/m241_stock.bin [--normalize out.bin]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

DROP = Path(r"vendor-drop")
OFFICIAL = {
    "v1.12": DROP
    / "M-241_Firmware_V1.12_update_package"
    / "M-241_Firmware_V1 12 update package"
    / "M-241_Firmware_V1_12 update package"
    / "01017-01B MBGPS_2.02B1.20,38400, GGA,GSA,GSV,VTG,RMC_V1.12_GR-241_HLX.bin",
    "v1.13": DROP
    / "M-241_Firmware_V1.13_update_package"
    / "M-241 Firmware V1 13 update package"
    / "M-241 Firmware V1_13 update package"
    / "01017-01C MBGPS_2.02B1.20,38400, GGA,GSA,GSV,VTG,RMC_V1.13_GR-241_HLX.bin",
}

# Fields the vendor tool writes at flash time; released .bin files have them
# blanked. Zeroing them makes a dump comparable to the stock image.
RUNTIME_FIELDS = (
    (0x05C, 4, "programming magic"),
    (0x0F8, 6, "build/flash counters"),
    (0x63458, 4, "image trailer"),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("dump", type=Path)
    parser.add_argument("--normalize", type=Path, default=None)
    args = parser.parse_args()

    ours = args.dump.read_bytes()
    print(f"dump    : {args.dump} ({len(ours)} bytes)")
    print(f"sha256  : {hashlib.sha256(ours).hexdigest()}\n")

    best = None
    for name, path in OFFICIAL.items():
        if not path.exists():
            print(f"official {name}: not present")
            continue
        official = path.read_bytes()
        if len(official) != len(ours):
            print(f"official {name}: size differs ({len(official)}), skipping")
            continue
        diffs = [i for i in range(len(ours)) if ours[i] != official[i]]
        print(f"official {name}: {len(diffs)} differing byte(s)")
        if not best or len(diffs) < best[1]:
            best = (name, len(diffs), official, diffs)

    if best is None:
        print("\nno official image of the same size available")
        return 1

    name, count, official, diffs = best
    print(f"\nclosest: {name} ({count} bytes differ)")
    in_runtime = 0
    for offset in diffs:
        label = ""
        for start, size, what in RUNTIME_FIELDS:
            if start <= offset < start + size:
                label = f"  <- {what}"
                in_runtime += 1
                break
        print(
            f"  0x{offset:05X}: dump=0x{ours[offset]:02X} official=0x{official[offset]:02X}{label}"
        )

    print(f"\n{in_runtime}/{count} differing bytes are inside documented runtime fields")
    if in_runtime != count:
        print("[!] there are differences outside the known runtime fields")

    if args.normalize:
        normalized = bytearray(ours)
        for start, size, _what in RUNTIME_FIELDS:
            if start + size <= len(normalized):
                normalized[start : start + size] = b"\x00" * size
        out = bytes(normalized)
        digest = hashlib.sha256(out).hexdigest()
        args.normalize.write_bytes(out)
        print(f"\nnormalized copy: {args.normalize} (sha256 {digest})")
        print(f"matches official: {digest == hashlib.sha256(official).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
