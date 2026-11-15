# Architecture & module contract

This document is the **frozen interface contract** between the parallel work
streams of this project. Every owner builds against these signatures; the test
suite (`pytest`) verifies the behaviour.

Package root: `src/m241/`.
Python: **3.10+**, stdlib only + `pyserial`. No Qt, no compilation, no
platform-specific binaries.

Hard platform rules (all streams):

- Never hard-code `/dev/tty…` or `COM…`; always go through
  `m241.protocol.ports.resolve_port()` / `list_serial_ports()`.
- Never `import serial` at module top level outside `m241.protocol.*`
  (so `m241 patch`, `m241 logs`, `m241 nmea repair` work on machines without
  the port open). `pyserial` is a hard dependency of `m241.protocol` only.
- File paths are `pathlib.Path`; every CLI output path is created with
  parents.
- All user-visible text is English; the Japanese README is `README.md` and the
  English one is `README.en.md`. Log messages go to `stderr`, machine output to
  `stdout`.
- No proprietary binary may be committed. Tests must synthesise their own
  fixtures.

---

## `m241/errors.py`

```python
class M241Error(Exception): ...                 # base
class ProtocolError(M241Error): ...             # framing / timeout / checksum
class BootRomError(ProtocolError): ...
class DownloadAgentError(ProtocolError): ...
class FirmwareError(M241Error): ...             # bad profile, bad offset, bad size
class FirmwareProfileError(FirmwareError): ...
class LogParseError(M241Error): ...
class NmeaError(M241Error): ...
```

Every exception must carry a human-readable message **and** (where a byte
offset is involved) an attribute `offset: int | None` defaulting to `None`.

---

## `m241/protocol/` — owner: stream A

### `ports.py`

```python
@dataclass(frozen=True)
class PortInfo:
    device: str            # "COM7", "/dev/ttyUSB0", "/dev/tty.usbserial-0001"
    description: str
    hwid: str
    vid: int | None
    pid: int | None
    is_candidate: bool     # CP210x / USB-serial bridge heuristics

def list_serial_ports() -> list[PortInfo]: ...
def candidate_ports() -> list[PortInfo]: ...
def resolve_port(spec: str | None) -> str:
    """None/'auto' -> the single candidate, or raise M241Error listing
    candidates. A raw device path/COM name is returned unchanged."""

def open_serial(port: str, baud: int = 115200, timeout: float = 1.0) -> Any:
    """Thin wrapper over serial.Serial; raises M241Error with an
    OS-appropriate hint (Windows: COM in use / driver; Linux: dialout group;
    macOS: /dev/tty.usbserial-*)."""

def platform_hint() -> str: ...
```

`SI_LABS_VID = 0x10C4`, `CP210X_PID = 0xEA60`, `PL2303 = (0x067B, 0x2303)`,
`FTDI = (0x0403, None)`, `CH340 = (0x1A86, 0x7523)`.

### `brom.py`

```python
BASE_ADDR_FIRMWARE = 0x00200000   # measured: 0x20000000 reads back an unmapped bus
BASE_ADDR_FIRMWARE_M3318 = 0x00000000   # the DA's linear view of the same flash
BASE_ADDR_FIRMWARE_MT333X = 0x20000000  # MT333x-style probe address only
BASE_ADDR_DA = 0x00100800         # from HoluxLoggerUtility.exe (push 0x100800)
BOOT_SYNC = b"\xa0\x0a\x50\x05"
BOOT_REPLY = (0x5F, 0xF5, 0xAF, 0xFA)

def checksum_xor16(data: bytes) -> int: ...

class MtkBootRom:
    def __init__(self, port: str, *, baud: int = 115200,
                 nmea_baud: int | None = None,
                 serial_factory: Callable[[str, int], Any] | None = None): ...
    ser: Any                                     # .write/.read/.baudrate/.timeout
    def start(self, *, use_rts_dtr: bool = True,
              pmtk180: bool = False) -> None: ...
    def read32(self, address: int, length: int, byte_swap: bool = False) -> bytes: ...
    def read16(self, address: int, length: int, byte_swap: bool = False) -> bytes: ...
    def read(self, address: int, length: int, byte_swap: bool = False) -> bytes: ...
    def checksum(self, address: int, length: int) -> int: ...
    def jump(self, address: int) -> None: ...
    def write16(self, address: int, data: bytes, *, byte_swap: bool = False,
                verify_echo: bool = True, skip_cksum: bool = False,
                progress_cb: Callable[[int, int], None] | None = None) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "MtkBootRom": ...
    def __exit__(self, *exc) -> None: ...

def dump_firmware(brom: MtkBootRom, *, base_addr: int = BASE_ADDR_FIRMWARE,
                  length: int | None = None, byte_swap: bool = True,
                  progress_cb=None) -> bytes:
    """length=None -> read size from header at base+0xF4 (little-endian u32;
    both byte orders are decoded and the plausible one is kept).
    Raises FirmwareError when the size is implausible."""
```

`serial_factory(port, baud)` allows tests to inject a fake. It must default
to `ports.open_serial`.

### `da.py`

```python
class DownloadAgent:
    OPCODE_SET_BAUD = 0xD2; OPCODE_MEM_CMD = 0xD3; OPCODE_WRITE = 0xD5
    ACK = 0x5A; NAK = 0xA5; CONT = 0x69; SYNC = 0xC0
    WRITE_PACKET_LEN = 0x100
    EXPECTED_DA_VERSION = 0x0308
    def __init__(self, brom: MtkBootRom): ...
    @property
    def info(self) -> dict[str, int]: ...
    def set_progress(self, cb: Callable[[int, int], None] | None) -> None: ...
    def start(self, da_path: str | Path) -> dict[str, int]: ...
    def write_firmware(self, fw: bytes, *, start: int = 0) -> None: ...
    def restart(self) -> None: ...
    def set_baud_rate(self, baudrate: int) -> None: ...

DA_INFO_FIELDS = ("sync_char", "da_version", "flash_device_id", "flash_size",
                  "flash_manufacturer_code", "flash_device_code",
                  "flash_ext_device_code1", "flash_ext_device_code2",
                  "ext_sram_size")
```

`info["flash_size"]` is **bytes** (decode from the DA report the same way the
reference tooling does; expose `flash_size_bytes` in a convenience property).

### `simulator.py`

A pure-Python MT3318 stand-in used by tests and `m241 doctor --simulate`:

```python
class FakeSerial:            # duck-typed serial.Serial
    def __init__(self, device: FakeDevice): ...
    # write/read/flush/reset_input_buffer/close/in_waiting/baudrate/timeout/rts/dtr

class FakeDevice:
    """Implements the BootROM command framing, an 8 MiB address space
    pre-filled from a supplied image, and the DA handshake (SYNC + 20-byte
    info report + 0xD3/0xD5 write path)."""
    def __init__(self, firmware: bytes = b"", *, flash_size: int = 0x200000): ...
    @property
    def memory(self) -> bytearray: ...
    def factory(self, port: str, baud: int) -> FakeSerial: ...

def roundtrip(self_test_image: bytes) -> bytes:
    """Load via fake BootROM, dump it back through the real MtkBootRom code
    path, and return the dumped bytes (must equal the input)."""
```

---

## `m241/firmware/` — owner: stream B

### `image.py`

```python
@dataclass(frozen=True)
class FirmwareHeader:
    family: str            # "HOLUX_M-241"
    device_type: str       # "01017-00E" (V1.12) / "01017-00F" (V1.13)
    release_name: str      # "B-core"
    release_major: int
    release_minor: int
    build_number: int
    fw_size: int
    build_date: str        # best-effort ASCII date string or ""
    raw: bytes = b""

class FirmwareImage:
    data: bytearray
    @classmethod
    def from_bytes(cls, data: bytes) -> "FirmwareImage": ...
    @classmethod
    def from_path(cls, path: str | Path) -> "FirmwareImage": ...
    def sha256(self) -> str: ...
    def header(self) -> FirmwareHeader: ...
    def version_string(self) -> str | None:      # "V1.13"
    def version_offset(self) -> int | None: ...
    def epoch_offsets(self) -> list[int]:        # all LE32 == 315964800
    def __len__(self) -> int: ...
    def to_bytes(self) -> bytes: ...
    def save(self, path: str | Path) -> None: ...
```

### `profiles.py`

```python
@dataclass(frozen=True)
class FirmwareProfile:
    name: str                    # "v1.12", "v1.13"
    display_name: str            # "Holux M-241 firmware V1.12"
    sha256: tuple[str, ...]      # known official image hashes
    sizes: tuple[int, ...]       # accepted byte lengths
    version: str                 # "V1.12"
    version_offset: int
    epoch_offset: int            # the *effective* one (second occurrence)
    epoch_constant: int = 315964800
    log_record_bytes: int = 16   # profile default; the block-header bitmask decides
    notes: str = ""

PROFILES: dict[str, FirmwareProfile]        # from data/firmware_profiles.json
OFFICIAL_SHA256: dict[str, str]             # sha256 -> profile name
def detect_profile(image: FirmwareImage) -> FirmwareProfile     # raises FirmwareProfileError
def get_profile(name: str) -> FirmwareProfile
def list_profiles() -> list[FirmwareProfile]
```

Rule: `detect_profile` matches on sha256 first, then (size, version string,
epoch offset) and only then raises. `--profile` on the CLI overrides.

### `patcher.py`

```python
@dataclass(frozen=True)
class Patch:
    offset: int
    old: bytes
    new: bytes
    description: str
    @property
    def changed_bytes(self) -> list[int]: ...

@dataclass
class PatchPlan:
    profile: FirmwareProfile
    patches: list[Patch]
    target_version: str
    def changed_offsets(self) -> list[int]: ...
    def summary(self) -> str: ...

WNRO_SHIFT_SECONDS = 1024 * 604800          # 619315200 == +7168 days

def build_patch_plan(image: FirmwareImage, *,
                     profile: FirmwareProfile | None = None,
                     target_version: str = "V1.14",
                     include_version_marker: bool = True,
                     wnro_shift_seconds: int = WNRO_SHIFT_SECONDS) -> PatchPlan: ...

def apply_patch_plan(image: FirmwareImage, plan: PatchPlan) -> FirmwareImage: ...
def verify_patched(patched: FirmwareImage, plan: PatchPlan) -> None: ...
def patch_file(src: str | Path, dst: str | Path, **kwargs) -> PatchPlan: ...
```

`patch_file` must write atomically (temp file + `os.replace`) and print/return
the before/after SHA256. Applying a plan to an already-patched image is a
no-op **error** (`FirmwareError`, "already patched").

### `data/firmware_profiles.json`

Shipped data file, consumed by `profiles.py` via `importlib.resources`.
Must contain at least `v1.12` and `v1.13` with the real offsets and hashes
from `docs/reference/03-firmware-offsets.md` (integrator supplies them; stream B
may start from the documented values in that file).

---

## `m241/logfile/` — owner: stream C

### `record.py`

```python
@dataclass
class LogRecord:
    index: int
    utc: datetime | None       # tz-aware, UTC
    valid: int | None          # raw fix-quality word
    lat: float | None
    lon: float | None
    height_m: float | None
    speed_kmh: float | None
    heading_deg: float | None
    pdop: float | None
    hdop: float | None
    vdop: float | None
    sats_used: int | None
    sats_view: int | None
    crc_ok: bool = True
    offset: int = 0

LOG_BIT_UTC = 0
... (bit index constants for every field, matching the MTK bitmask)
M241_FIELD_SIZES: dict[int, int]     # bit -> byte size on M-241
def record_length(bitmask: int, *, device: str = "m241") -> int: ...
def block_header(block: bytes) -> BlockHeader: ...   # first 0x200 bytes
```

### `reader.py`

```python
@dataclass
class BlockHeader:
    offset: int
    count: int | None          # 0xFFFF -> block not full
    bitmask: int
    policy: int
    period_ds: int
    distance_dm: int
    speed_dkh: int

@dataclass
class LogDump:
    data: bytes
    blocks: list[BlockHeader]
    records: list[LogRecord]
    @property
    def record_length(self) -> int
    def used_bytes(self) -> int
    def fill_ratio(self) -> float

def read_log_dump(data: bytes, *, device: str = "m241",
                  strict: bool = False) -> LogDump: ...
def read_log_file(path: str | Path, **kw) -> LogDump: ...
```

Must handle: 64 KiB block boundaries with the 0x200-byte header, 0xFF padding,
truncated final record, bad CRC (`crc_ok=False`, never raise unless `strict`),
and M-241's 3-byte height + 1-byte CRC. No `'*'` separator on M-241.

### `capacity.py`

```python
FLASH_SIZES = {"512K": 0x80000, "2M": 0x200000, "4M": 0x400000}
BLOCK_SIZE = 0x10000
BLOCK_HEADER = 0x200
@dataclass(frozen=True)
class Capacity:
    flash_bytes: int
    blocks: int
    usable_bytes: int
    record_bytes: int
    points: int
    seconds_at: Callable[[float], float] | None      # or method duration(interval_s)
def capacity(flash: str | int, record_bytes: int) -> Capacity: ...
def capacity_table() -> list[Capacity]:              # 512K/2M/4M x 16B/20B
def record_bytes_for_version(version: str) -> int:   # "V1.12" -> 16, "V1.13" -> 20
def flash_name_from_da(flash_device_id: int) -> str | None   # 0x49 -> "512K"
def estimate_points(flash: str | int, fw_version: str) -> int
```

### `export.py`

```python
def to_gpx(records, *, name="M-241 log") -> str: ...
def to_csv(records) -> str: ...
def to_nmea(records) -> str: ...
def to_kml(records, *, name="M-241 log") -> str: ...
def to_json(dump: LogDump) -> dict: ...
EXPORTERS: dict[str, Callable[..., str]]     # "gpx", "csv", "nmea", "kml", "json"
def export(dump: LogDump, fmt: str) -> str: ...
```

GPX must be schema-valid (`<gpx version="1.1">`, `<trk><trkseg><trkpt lat lon>`),
CSV columns `index,utc,lat,lon,altitude_m,speed_kmh,heading_deg,hdop,sats,crc_ok`.

---

## `m241/nmea/` — owner: stream C

### `sentence.py`

```python
def checksum(body: str) -> int: ...               # XOR of chars, no '$'/'*'
def with_checksum(body: str) -> str: ...          # "$<body>*XX"
def split(sentence: str) -> tuple[str, list[str]]: ...
@dataclass
class Rmc:
    utc: time, status: str, lat, lon, speed_kn, course, date: date | None, ...
def parse_rmc(sentence: str) -> Rmc: ...
def parse_gga(sentence: str) -> dict: ...
def is_valid(sentence: str) -> bool: ...
```

### `repair.py`

```python
WNRO_DAYS = 7168        # = 1024 weeks
WNRO_SECONDS = 619315200
# Date windows used for auto-detection: a device that is "19.6 years behind"
# reports dates between 1999-08-22 and 2019-04-06 while wall-clock is later.
def needs_wnro_fix(date_value: date, *, wall_clock: date | None = None) -> bool: ...
def shift_days(date_value: date, days: int = WNRO_DAYS) -> date: ...
def repair_sentence(line: str, *, offset_days: int = 0, auto: bool = True,
                    wall_clock: date | None = None) -> tuple[str, bool]:
    """Returns (rewritten_line, changed). Only RMC (the `ddmmyy` date field)
    and ZDA (day/month/year) carry dates and are rewritten; every other sentence
    is returned verbatim (including GGA/GSA/GSV/VTG and the device's debug
    lines). Checksums are recomputed when a field changed."""
def repair_stream(lines: Iterable[str], **kw) -> Iterator[str]: ...
```

### `proxy.py`

```python
class NmeaProxy:
    def __init__(self, port: str, *, offset_days: int = 0, auto: bool = True,
                 baud: int = 38400): ...
    def serve_tcp(self, host: str = "127.0.0.1", port: int = 10110,
                  stop_event: threading.Event | None = None) -> None: ...
    def serve_stdout(self, stop_event=None) -> None: ...
    def stop(self) -> None: ...
```

Must be testable with `io.BytesIO`-style fakes (`make_proxy(fake_in, fake_out)`).

---

## `m241/cli.py` — owner: stream D

Single entry point, `main(argv: list[str] | None = None) -> int`, no
third-party argparse helpers. Subcommands:

```
m241 ports                       [--json]
m241 doctor                      [--json] [--simulate] [--from-drop PATH]
                                 [--extract-to DIR] [--include-firmware] [--port PORT]
m241 info <port>                 probe PMTK605/705 -> firmware/device id [--aiding] [--json]
m241 dump <port> -o FILE         [--base-addr 0x00200000|0x0] [--length N]
                                 [--verify/--no-verify] [--force] [--json]
m241 patch SRC -o DST            [--in-place] [--profile auto|v1.12|v1.13]
                                 [--target-version V1.14] [--no-version-marker]
                                 [--dry-run] [--json]
m241 flash <port> --da DA --fw IMG --backup BACKUP
                                 [--start-addr 0x0] [--yes] [--high-baud] [--json]
m241 logs info FILE              [--json]      (blocks, records, capacity used)
m241 logs read FILE              [--format csv|gpx|nmea|kml|json] [-o FILE]
                                 [--flash 2M|4M] [--firmware V1.12|V1.13] [--strict]
m241 logs download <port> -o FILE [--chunk BYTES] [--length BYTES] [--json]
m241 logs erase <port>           [--yes] [--require-backup FILE] [--json]
m241 logs geotag TRACK PHOTO...  [--format exiftool|csv|json] [-o FILE]
m241 logs capacity               [--flash 512K|2M|4M|all] [--firmware V1.12|V1.13]
                                 [--interval SECONDS] [--json]
m241 nmea repair                 [--in FILE|-] [--out FILE|-] [--offset-days 7168|auto]
m241 nmea proxy <port>           [--offset-days 7168|auto] [--tcp 127.0.0.1:10110]
m241 selftest                    end-to-end fake-device run (no hardware) [--json]
```

Rules: `-h` exits 0; unknown command exits 2 with usage on stderr; hardware
commands never prompt unless `--yes` is absent **and** the action is
destructive (`flash`, `logs erase`); all commands are importable without opening
a port.

### `scripts/` (removed)

Earlier versions shipped four thin shims under `scripts/` (dump, patch, flash
and a wrapper) so the three-step workflow from the original blog post still read
naturally. They only forwarded to the CLI, so they were removed: `m241 dump`,
`m241 patch` and `m241 flash` are the interface.

---

## CI (owner: stream D)

`.github/workflows/ci.yml` runs three jobs:

* `lint` on Linux + Python 3.12 — ruff, the docs link check and a hardware-free
  CLI smoke test (`m241 --help`, `selftest`, `logs capacity`, `ports --json`,
  `doctor --simulate --json`).
* `test` — Linux on Python 3.10 / 3.11 / 3.13, Windows on 3.12 and macOS on
  3.12. It is **not** every OS × every Python: the package is pure Python, so
  the extra combinations add minutes without adding coverage.
* `build` (needs `lint` and `test`) — `python -m build`, assert the wheel and
  sdist ship `m241/data/*.json`, install the wheel and smoke-test it.

`.github/workflows/release.yml` runs `verify` → `build` → `publish` on `v*`
tags. `verify` runs ruff, the docs link check, the tests, a tag-is-on-main
check, a version check (tag = `pyproject.toml` = `__version__`) and a CHANGELOG
check; `publish` creates the GitHub release.

Branch protection on `main` is not configured in the repository itself.
