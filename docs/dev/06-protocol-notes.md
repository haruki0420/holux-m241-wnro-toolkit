# Protocol implementation notes

Facts here were verified against the real protocol descriptions in
`docs/reference/03-firmware-offsets.md` and the frozen contract in
`docs/dev/01-architecture.md`. This file records the few places where the
implementation had to interpret or extend the contract, so an integrator can
tell a deliberate deviation from a bug.

## 1. Firmware size header byte order (`brom.dump_firmware`)

The image stores the size at `base + 0xF4` as a **little-endian** u32
(`406308 = 0x63324` stored as `24 33 06 00`). An earlier contract draft said
big-endian, and `0xAF` READ32 returns byte-reversed 32-bit words, so
`dump_firmware` decodes both orders and keeps the value that is plausible as a
firmware size (`0x10 .. 8 MiB`); when both are plausible the smaller one wins.
For every real image only the little-endian reading is in range. An explicit
`length=` argument bypasses this entirely, and the error message names both
decoded values.

## 2. `MtkBootRom.__init__` accepts extra keyword arguments

`timeout` (per-read timeout, default `1.0` s), `sync_timeout` (total budget for
the `a0 0a 50 05` sync dance, default `5.0` s) and `nmea_probe_seconds` (how
long the NMEA-first safety probe listens, default `0.6` s) were added so tests
can be fast and deterministic. They are keyword-only with defaults, so the
frozen signature stays call-compatible.

The serial port is opened **lazily** on the first I/O operation (normally
`start()`), via `serial_factory(port, baud)` or
`m241.protocol.ports.open_serial`. Constructing a `MtkBootRom` therefore never
touches hardware, which keeps signature-only tests and non-hardware commands
safe; `start()` also asserts the control lines (after the NMEA-first probe) and
runs the sync dance. A second `start()` re-runs the handshake; `FakeDevice`
accepts the `0xA0` probe at a command boundary so the simulator allows this,
while real-hardware behaviour of a repeated `start()` without a reset has not
been exercised here. After `close()` the instance refuses further I/O with a
`BootRomError` rather than silently reopening the port.

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

### Length units on the wire

The public API takes a byte `length`; the on-wire count field is in **units**:
`0xA1` / `0xA2` / `0xA4` carry 16-bit **words**, `0xAF` carries 32-bit
**dwords** (`MtkBootRom._frame()` divides the byte length by 2 or 4). This
matches the hardware-verified framing, and `FakeDevice._dispatch_frame`
multiplies by the same unit. Sending a byte count instead is rejected or returns
the wrong amount of data.

### Read responses carry **no** checksum trailer

`0xA2` / `0xAF` return the echo followed by the data and nothing else. The
checksum is a separate `0xA4` command, which is the only command that answers
with a two-byte value. Consuming a two-byte trailer desynchronises every read —
that was a real bug in this toolkit, fixed and covered by
`tests/test_protocol_brom.py::test_read_has_no_trailing_checksum_word`.

The checksum is computed over the **memory-order** bytes (the wire order is the
16-bit-pair-swapped stream, and the chip unswaps before checksumming);
`byte_swap=True` returns those memory-order bytes, `byte_swap=False` returns the
raw wire order unchanged.

## 4. Download-agent report and commands

* 20-byte report, `struct ">BHBLHHHHL"`, all fields big-endian, starting at the
  first `0xC0` sync byte. Bytes before the sync byte are skipped (and counted
  in the error message if no sync byte ever arrives).
* `flash_size` is a big-endian u32 in the DA report and is already in
  **bytes** (see `docs/reference/01-protocol.md`); `info["flash_size"]` and
  `flash_size_bytes` both expose bytes. `flash_device_id` is the byte-wide id
  (retail unit: `0x49`); the 16-bit `flash_device_code` is a separate field
  (retail unit: `0x22BA`).
* `0xD3` is `>BBLL` = (opcode, 1, start, end) with `end = start + len - 1` and
  is acknowledged with `0x5A` **plus a status byte**; `0xD5` is `>BL` =
  (opcode, 0x100) and is answered with two `0x5A` bytes; each 0x100-byte packet
  is followed by a big-endian u16 sum-of-bytes checksum and answered with
  `0x69` (CONT); two final `0x5A` bytes close the transfer. Any `0xA5` is
  surfaced as `DownloadAgentError` including the packet offset.
* `0xD2` (set baud) is acknowledged with one `0x5A`; `0xD9` (restart) is
  written and not read back. The vendor documentation for these two is less
  specific than for the write path.

## 5. Simulator scope

`m241.protocol.simulator.FakeDevice` models only what the real host code needs:

* BootROM: sync sequence, per-byte command echo, `0xA2` reads (returned
  pair-swapped), `0xAF` reads (each 32-bit word byte-reversed), `0xA4`
  checksum, `0xA1` writes (stored little-endian), `0xA8` jump; all 9-byte
  frames big-endian.
* DA: info report, `0xD3`/`0xD5`/`0xD2`/`0xD9`, per-packet sum-of-bytes
  checksum, `0x69` continuation, two final `0x5A` acknowledgements.
* Address map: flash at `0x00200000` (the measured BootROM window; the
  MT333x-style `0x20000000` is also mapped so legacy profile tests keep working)
  *and* at `0x0` (the DA's linear view used by `flash --start-addr 0x0`), RAM at
  `0x00100000-0x001FFFFF` with the DA at `0x00100800`; total backing store is
  8 MiB.
* Fault injection for the error-path tests: `silent`, `corrupt_echo`,
  `fail_packet`, `nak_set_baud`.

It is a test/`doctor --simulate` aid, not a cycle-accurate emulator: it does
not model flash erase timings, real DA code execution, or NMEA output.
