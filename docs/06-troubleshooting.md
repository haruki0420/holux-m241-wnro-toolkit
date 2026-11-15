# Troubleshooting

`m241 doctor` is the first thing to run. It checks the Python environment, the
`pyserial` version, the serial port list, the vendor drop (if you point at it),
the device handshake, and the firmware profile of a dump — and prints the exact
next command to try.

```bash
m241 doctor
m241 doctor --json
m241 doctor --simulate        # no hardware: proves the install is healthy
m241 doctor --from-drop ../holux --extract-to vendor/
```

Exit codes: `0` ok, `1` runtime error, `2` usage, `3` hardware not found,
`4` verification failed, `5` safety refusal.

---

## Environment / install

### `ModuleNotFoundError: No module named 'serial'`

`pyserial` is not installed in the interpreter you are running.

```bash
python -m pip install pyserial
# or, inside the venv:
pip install -e ".[dev]"
m241 doctor
```

Note the package name is `pyserial`, but the import name is `serial`.

### `m241: command not found` after `pip install -e .`

The venv's `Scripts/` (Windows) or `bin/` (POSIX) is not on `PATH`. Either
activate the venv or run:

```bash
python -m m241 --help
```

### `m241` works but `import m241` fails in my IDE

The project uses a `src/` layout. Point your IDE at `src/` as a source root, or
`pip install -e .` in the same interpreter the IDE uses.

### `ruff` or `pytest` complains about Python 3.9

The project requires **3.10+** (`X | Y` type syntax at runtime). Upgrade, or use
a newer venv.

---

## Serial port discovery

### `m241 ports` shows nothing

1. Is the cable plugged in? Try another USB port (direct, not through a hub).
2. Windows: install the CP210x driver; check Device Manager → Ports (COM & LPT).
3. Linux: `dmesg | tail` after plugging in — you should see `cp210x` and
   `ttyUSB0`.
4. macOS: `ls /dev/*usb*` — you should see `/dev/tty.usbserial-*` and
   `/dev/cu.usbserial-*`.
5. Is the M-241 powered on? The bridge chip powers up with USB, but the GPS
   answers only when the device is on.

### `m241 ports` sees the port but `auto` refuses to pick it

`auto` picks a port only if exactly one *plausible* bridge is present
(CP210x/PL2303/FTDI/CH340). If you have several, name it explicitly:

```bash
m241 dump COM5 -o backup/stock.bin
m241 dump /dev/ttyUSB0 -o backup/stock.bin
```

### `PermissionError` / `Access is denied` / `Resource busy`

Something else has the port open. On each platform:

**Windows**
```powershell
Get-Process | Where-Object { $_.ProcessName -match 'Holux|screen|putty|arduino' }
# close vendor utilities, serial monitors, gpsd, IDE serial plotter
```

**Linux**
```bash
sudo lsof /dev/ttyUSB0
sudo fuser -v /dev/ttyUSB0
```

**macOS**
```bash
lsof /dev/tty.usbserial-0001
```

On Linux also check group membership: `groups | grep dialout`.

---

## BootROM sync failures

### `timeout @ BOOT_ROM_START_CMD1 (no 0x5f)`

The chip never answered `0xA0`. In order of likelihood:

1. **The device is not in a state where the BootROM listens.** Power-cycle it
   (pull the battery for 2 s), turn it on, retry.
2. **Wrong port** — you picked a different USB-serial device.
3. **The port is held open** by another program.
4. **RTS/DTR reset is not wired** on your cable. Try the legacy entry:
   ```bash
   m241 dump auto --pmtk180 -o backup/stock.bin
   ```
   This sends `$PMTK180*3B` at the NMEA baud first.
5. **NMEA baud mismatch.** The device is normally 38400, but a firmware setting
   can change it. Tell the tool:
   ```bash
   m241 dump auto --nmea-baud 38400 -o backup/stock.bin
   ```
6. **Marginal USB hub.** Plug directly into the machine.

### `unexpected response @ BOOT_ROM_START_CMD2/CMD3/CMD4`

The `0xA0` sync worked but a later handshake byte came back wrong. Usually the
device reset itself mid-sequence (brown-out, bad cable) or the port is being
probed by the OS (ModemManager on Linux, a vendor app on Windows).

### Already in BootROM and don't want to reset

```bash
m241 dump auto --assume-brom -o backup/stock.bin
```

This skips the reset pulse and only performs the 4-byte sync. Use it when a
previous run died mid-way and left the chip listening.

---

## Dump problems

### `firmware size from header is invalid (0x0)`

The read at `base+0xF4` returned zero — the chip answered but the flash was not
mapped where we looked. Almost always a **wrong `--base-addr`**:

```bash
m241 dump auto --base-addr 0x0 -o backup/stock.bin          # linear flash view
m241 dump auto --base-addr 0x20000000 -o backup/stock.bin   # default
```

or you were not really in BootROM (see above).

### `read32 checksum mismatch`

Transient — the chip was still busy. `m241 dump` retries automatically; if it
keeps failing, lower the read size or try `--no-swap` (MT3318 variants differ in
32-bit byte order):

```bash
m241 dump auto --no-swap -o backup/stock.bin
```

### Two dumps differ

The tool refuses to proceed, because a flaky read is a flaky write. Causes:
unstable cable, USB power saving, or a device that reset during the dump.
Re-run; if it is reproducible, the cable is the prime suspect.

### `FirmwareProfileError: no supported profile matches`

Your dump is not a stock V1.11/V1.12/V1.13 image. Either:

- the dump is corrupt (compare the two passes),
- the image is a variant this project has not catalogued (please open an issue
  with the header fields — `m241 logs info`-style output for firmware is in
  `m241 patch --dry-run`),
- or you already patched it (`m241 doctor` will say "already patched").

Never force the patch onto an unknown image. Use `--profile` only if you have
verified the offsets by hand.

---

## Patch problems

### `FirmwareError: already patched`

The epoch constant at `0x402F4` already equals `935280000`. You are patching a
patched image. Use the original dump.

### `--dry-run` says the offsets do not match

Your image is a different version than the profile table knows. Do **not** force
it; the two firmware images in the drop are the only verified ones.

### Can I patch the `.bin` straight out of the Holux zip?

Yes — those are stock images and match the profiles exactly (hashes are in
`docs/04-firmware-profiles.md`). The recommended route is still dump → patch, so
you keep a known-good backup of your *own* unit.

---

## Flash problems

### `DA SYNC byte 0xc0 not found`

The DA did not boot. Causes:

- The DA was written with `byte_swap=False` (a bug in older tooling; this
  implementation always uses `byte_swap=True` for the DA).
- Wrong load address. It must be `0x00100800`; `0x00100000` is the BootROM stack
  and writing there kills the chip.
- Wrong DA file (not `DA_v3.8.4.bin`).

### `unsupported DA version: 0x....`

You are using a global `MTK_AllInOne_DA_*.bin` instead of Holux's DA v3.8. Use
the file from the official logger utility.

### Write stops part-way / `device reports readiness to read but returned no data`

The write is now incomplete. **The device state is undefined.** Re-flash your
original dump with the same command:

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin \
    --fw backup/m241_stock.bin --backup backup/m241_stock.bin
```

Do not power-cycle into the half-written firmware expecting it to boot.

### `--high-baud` makes it worse

Drop it. High baud is an optimisation, not a requirement; marginal cables fail
at 460800/912600.

### The device seems dead after a failed flash

Remove and re-insert the battery, then plug in USB **without turning it on** and
try the dump/flash again with `--assume-brom`. The MT3318 BootROM lives in mask
ROM and survives flash corruption; a power-cycle usually brings it back. If the
port never appears at all, you have a hard brick (see `DISCLAIMER.md`).

---

## Log download / parsing

### Record length looks wrong / coordinates are garbage

You forced the wrong firmware profile. Let the parser decide:

```bash
m241 logs info data.bin            # shows the bitmask and the computed record size
```

`--firmware V1.12` / `--firmware V1.13` only change the *capacity* estimate; the
record length always comes from the block header's bitmask.

### Fewer points than the manual claims

Expected. See `docs/02-log-format.md`: 130,048 points only happens with 16-byte
records; a V1.13 unit (20-byte records) holds 104,038 on 2 MiB.

### Dates are all in 2006

That is the bug this project exists for. Either patch the firmware, or export
with a correction:

```bash
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx
```

### `strict mode` raises on my dump

A real dump can legitimately contain stale/truncated records. Use the default
(non-strict) mode; inspect `crc_ok` per record in the JSON export.

---

## NMEA repair / proxy

### `m241 nmea repair` changes nothing

Auto mode only shifts dates inside the stale window (1999-08-22…2019-04-06) when
the wall clock says now is much later. If your capture is already correct, that
is the desired no-op. Force it with `--offset-days 7168`.

### Proxy: the mapping app sees no data

- Only one program can read the serial port. The proxy owns the port; point the
  app at the **TCP** endpoint (`127.0.0.1:10110`), not at the COM port.
- Check the port name and the device's baud (default 38400).
- Windows Firewall may prompt on first bind — loopback binding is enough.

### Proxy: dates still wrong

Auto detection refuses to shift fresh data. Use `--offset-days 7168`
explicitly.

---

## Tests / CI

### `pytest` fails only on Windows

Path handling: every path must go through `pathlib.Path`. Report the test name —
if it is ours, it is a bug.

### `pytest` tries to open a serial port

It should not. The suite uses `m241.protocol.simulator`, a pure-Python fake
device. If a test hangs on a real port, that is a bug; please report it.

### Running without hardware

```bash
m241 selftest        # dump -> patch -> flash -> read, entirely in memory
m241 doctor --simulate
```
