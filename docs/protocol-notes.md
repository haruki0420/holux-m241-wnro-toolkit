# Protocol implementation notes

Facts here were verified against the real protocol descriptions in
`docs/04-firmware-profiles.md` and the frozen contract in
`docs/ARCHITECTURE.md`. This file records the few places where the
implementation had to interpret or extend the contract, so an integrator can
tell a deliberate deviation from a bug.

## 1. Firmware size header byte order (`brom.dump_firmware`)

The contract says the size at `base + 0xF4` is a **big-endian** u32. The
verified hardware fact in `docs/04-firmware-profiles.md` says it is a
**little-endian** u32 (`406308 = 0x63324` stored as `24 33 06 00`).

`dump_firmware` therefore decodes both orders and keeps the value that is
plausible as a firmware size (`0x10 .. 8 MiB`); when both are plausible the
smaller one wins. For every real image only the little-endian reading is in
range, so behaviour matches the hardware facts without breaking the contract's
wording. An explicit `length=` argument bypasses this entirely, and the error
message names both decoded values.

## 2. `MtkBootRom.__init__` accepts two extra keyword arguments

`timeout` (per-read timeout, default `1.0` s) and `sync_timeout` (total budget
for the `a0 0a 50 05` sync dance, default `5.0` s) were added so tests can be
fast and deterministic. They are keyword-only with defaults, so the frozen
signature stays call-compatible.

The serial port is opened **lazily** on the first I/O operation (normally
`start()`), via `serial_factory(port, baud)` or
`m241.protocol.ports.open_serial`. Constructing a `MtkBootRom` therefore never
touches hardware, which keeps signature-only tests and non-hardware commands
safe; `start()` also pulses the control lines and runs the sync dance. A second
`start()` re-runs the handshake; `FakeDevice` accepts the `0xA0` probe at a
command boundary so the simulator allows this, while real-hardware behaviour of
a repeated `start()` without a reset has not been exercised here. After
`close()` the instance refuses further I/O with a `BootRomError` rather than
silently reopening the port.

## 3. `write16` wire chunking

The frame (`0xA1` + big-endian address + big-endian count) is sent once, then
the payload is streamed in **10-byte chunks** (the chunk size used by the
reference tooling); every byte is echoed by the chip and verified. With
`byte_swap=True` the payload is swapped in 16-bit pairs before transmission,
i.e. callers pass the bytes as they must appear in memory (the chip stores
little-endian). The trailing verification checksum (`0xA4`) compares the
region checksum reported by the chip with `checksum_xor16` of the stored bytes;
`skip_cksum=True` omits that round trip, which is what RAM (DA load) writes
need.

### Length units on the wire (open point for the integrator)

`docs/01-protocol.md` §3 states `word-count = byte_length / 2` for `0xA1`,
`0xA2`, `0xA4` and `dword-count = byte_length / 4` for `0xAF`, while the frozen
contract's API only names a byte `length` parameter and `dump_firmware`
documents the header value as a byte count. This implementation keeps the
**byte length** in the on-wire count field, so host and simulated device are
self-consistent and the API stays exactly as contracted. If the vendor
tooling's word/dword counts turn out to be the literal wire values on real
hardware, only `MtkBootRom._frame()` call sites plus `FakeDevice._dispatch_frame`
need a unit conversion; the public API would not change. Flagged here rather
than silently picking one reading.

### Read responses carry a trailing XOR16 checksum

`docs/01-protocol.md` says the `0xA2`/`0xAF` responses are "data + XOR16
checksum". Although the frozen contract returns plain `bytes` from
`read32`/`read16`, `MtkBootRom._read()` consumes the trailing 2-byte big-endian
checksum after the data and verifies it with `checksum_xor16()`, raising
`BootRomError` on mismatch. `FakeDevice` emits the same checksum, so both sides
stay in lock-step.

The checksum is always computed over the **memory-order** bytes (the wire order
is the 16-bit-pair-swapped stream, and the chip unswaps before checksumming);
`byte_swap=True` returns those memory-order bytes, `byte_swap=False` returns the
raw wire order unchanged.

## 4. Download-agent report and commands

* 20-byte report, `struct ">BHBLHHHHL"`, all fields big-endian, starting at the
  first `0xC0` sync byte. Bytes before the sync byte are skipped (and counted
  in the error message if no sync byte ever arrives).
* `flash_size` is a big-endian u32 in the DA report and is already in
  **bytes** (the reference protocol reference in `docs/01-protocol.md` documents
  it that way); `info["flash_size"]` and `flash_size_bytes` both expose bytes.
* `0xD3` is `>BBLL` = (opcode, 1, start, end) with `end = start + len - 1` and
  is acknowledged with `0x5A`; `0xD5` is `>BL` = (opcode, 0x100) and is
  answered with two `0x5A` bytes; each 0x100-byte packet is followed by a
  big-endian u16 sum-of-bytes checksum and answered with `0x69` (CONT); two
  final `0x5A` bytes close the transfer. Any `0xA5` is surfaced as
  `DownloadAgentError` including the packet offset.
* `0xD2` (set baud) and `0xD9` (restart) are implemented with the same
  one-byte `0x5A` acknowledgement convention; the vendor documentation for
  these two is less specific than for the write path.

## 5. Simulator scope

`m241.protocol.simulator.FakeDevice` models only what the real host code needs:

* BootROM: sync sequence, per-byte command echo, `0xA2`/`0xAF` reads (returned
  pair-swapped), `0xA4` checksum, `0xA1` writes (stored little-endian), `0xA8`
  jump; all 9-byte frames big-endian.
* DA: info report, `0xD3`/`0xD5`/`0xD2`/`0xD9`, per-packet sum-of-bytes
  checksum, `0x69` continuation, two final `0x5A` acknowledgements.
* Address map: flash at `0x20000000` (BootROM window) *and* at `0x0` (the DA's
  linear view used by `flash --start-addr 0x0`), RAM at
  `0x00100000-0x001FFFFF` with the DA at `0x00100800`; total backing store is
  8 MiB.
* Fault injection for the error-path tests: `silent`, `corrupt_echo`,
  `fail_packet`, `nak_set_baud`.

It is a test/`doctor --simulate` aid, not a cycle-accurate emulator: it does
not model flash erase timings, real DA code execution, or NMEA output.
