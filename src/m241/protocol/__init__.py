"""MTK BootROM / Download Agent protocol support for the Holux M-241.

This package owns the only hard dependency on pyserial: importing
``m241.protocol`` itself is cheap, and the serial module is imported lazily
when a port is actually opened.

Public surface::

    from m241.protocol import (
        BASE_ADDR_DA, BASE_ADDR_FIRMWARE, BOOT_SYNC, MtkBootRom, checksum_xor16,
        dump_firmware, DownloadAgent, DA_INFO_FIELDS,
        PortInfo, list_serial_ports, candidate_ports, resolve_port, open_serial,
        FakeDevice, FakeSerial, roundtrip,
    )
"""

from __future__ import annotations

from .brom import (
    BASE_ADDR_DA,
    BASE_ADDR_FIRMWARE,
    BASE_ADDR_FIRMWARE_M3318,
    BOOT_REPLY,
    BOOT_SYNC,
    MtkBootRom,
    checksum_xor16,
    dump_firmware,
    swap_pairs,
)
from .da import (
    DA_INFO_FIELDS,
    DA_INFO_SIZE,
    EXPECTED_DA_VERSION,
    DownloadAgent,
)
from .ports import (
    CH340,
    CP210X_PID,
    FTDI,
    PL2303,
    SI_LABS_VID,
    PortInfo,
    candidate_ports,
    list_serial_ports,
    open_serial,
    platform_hint,
    resolve_port,
)
from .simulator import FakeDevice, FakeSerial, roundtrip

__all__ = [
    "BASE_ADDR_DA",
    "BASE_ADDR_FIRMWARE",
    "BASE_ADDR_FIRMWARE_M3318",
    "BOOT_REPLY",
    "BOOT_SYNC",
    "CH340",
    "CP210X_PID",
    "DA_INFO_FIELDS",
    "DA_INFO_SIZE",
    "EXPECTED_DA_VERSION",
    "FTDI",
    "PL2303",
    "SI_LABS_VID",
    "DownloadAgent",
    "FakeDevice",
    "FakeSerial",
    "MtkBootRom",
    "PortInfo",
    "candidate_ports",
    "checksum_xor16",
    "dump_firmware",
    "list_serial_ports",
    "open_serial",
    "platform_hint",
    "resolve_port",
    "roundtrip",
    "swap_pairs",
]
