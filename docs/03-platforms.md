# Installation and platform notes

The toolkit is pure Python. The only hard dependency is `pyserial>=3.5`. There
is nothing to compile and no platform-specific code path outside a handful of
helpful error hints.

## 1. Common steps

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"
m241 --help
```

Verify:

```bash
m241 doctor --simulate     # exercises the whole stack against the fake device
m241 selftest              # end-to-end dump -> patch -> flash -> read, no hardware
```

Both must exit 0 on a healthy checkout; CI runs them on all three platforms.

## 2. Windows 10 / 11

### Driver

The bundled cable is a **Silicon Labs CP210x** USB-UART bridge. Windows 10/11
usually installs it automatically from Windows Update. If `m241 ports` shows
nothing:

1. Run `CP210xVCPInstaller_x64.exe` from the official Holux `USB Cable
   Driver(Win10)` package (x86 for 32-bit Windows).
2. Replug the cable.
3. `m241 ports` should now list something like
   `COM5 - Silicon Labs CP210x USB to UART Bridge (COM5)`.

### Naming

Windows uses `COM<n>` everywhere. The tool accepts:

```bash
m241 dump COM5 -o backup/stock.bin
m241 dump auto -o backup/stock.bin     # picks the CP210x automatically
```

`auto` refuses to guess if more than one plausible cable is attached; it lists
them and exits with code 3.

### Common Windows errors

| Message | Cause | Fix |
|---|---|---|
| `Access is denied` / `PermissionError: COM5` | another program has the port open | close vendor utilities, Arduino IDE, PuTTY, serial monitors; check Task Manager for `HoluxLoggerUtility.exe` |
| `FileNotFoundError: COM5` | wrong COM number, or the cable was unplugged | `m241 ports` again |
| `device reports readiness to read but returned no data` | the device reset mid-read | power-cycle the M-241 and retry |
| Antivirus blocks the write | overly eager behavioural AV | whitelist the venv or run from an excluded folder |

Windows has no `dialout` group and no udev rules: if the port exists and is not
in use, you can open it.

## 3. Linux

### Permissions

Your user needs to be in the group that owns the tty. On most distributions:

```bash
sudo usermod -aG dialout "$USER"
# log out and back in (or `newgrp dialout`)
```

Check ownership with `ls -l /dev/ttyUSB0` — it should be `root:dialout`.

### udev rule (optional, for a stable name)

```bash
# /etc/udev/rules.d/99-holux-m241.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
    SYMLINK+="ttyM241", MODE="0660", GROUP="dialout"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
m241 dump /dev/ttyM241 -o backup/stock.bin
```

### ModemManager

On desktops, ModemManager may probe and reset the port. If you see the device
disconnecting immediately after plugging in:

```bash
sudo systemctl stop ModemManager
# or add an exclusion rule:
# /etc/udev/rules.d/99-holux-ignore.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ENV{ID_MM_DEVICE_IGNORE}="1"
```

### Common Linux errors

| Message | Cause | Fix |
|---|---|---|
| `PermissionError: /dev/ttyUSB0` | not in `dialout` | the `usermod` above |
| `device reports readiness to read but returned no data` | ModemManager or brltty grabbing the port | stop/ignore them |
| `/dev/ttyUSB0` disappears | power management suspending the port | `echo -1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer` |

## 4. macOS

Modern macOS (Big Sur and later, Intel and Apple Silicon) needs **no driver**
for CP210x: the built-in `AppleUSBACM`/`Silicon Labs` stack exposes
`/dev/tty.usbserial-*` (sometimes `/dev/cu.usbserial-*`).

```bash
m241 ports
# /dev/tty.usbserial-0001  -  CP2102 USB to UART Bridge Controller
m241 dump /dev/tty.usbserial-0001 -o backup/stock.bin
```

Prefer **`/dev/cu.*`** over `/dev/tty.*` in scripts: `cu` does not block waiting
for DCD, which matters because the M-241 does not assert it. The tool accepts
either.

### Common macOS errors

| Message | Cause | Fix |
|---|---|---|
| `[Errno 16] Resource busy` | another app has the port | quit serial monitors / vendor app; `lsof /dev/tty.usbserial-0001` |
| `[Errno 2] No such file` | unplugged, or the device enumerated as `cu.*` | `ls /dev/*usbserial*` |
| `OSError: [Errno 6] Device not configured` | Apple Silicon + a flaky hub | connect directly to the Mac, not through a hub |
| The port never appears | a third-party CP210x driver from 2015 is installed | uninstall it; use the built-in one |

## 5. WSL2 / Docker / VM

Serial passthrough works but is fragile, and a bad flash is unrecoverable. If
you must:

- **WSL2:** use `usbipd-win` to attach the CP210x, then talk to `/dev/ttyUSB0`.
- **Docker:** `--device /dev/ttyUSB0` (Linux host only).
- **Windows/macOS VM:** USB passthrough, and disable sleep while flashing.

`m241 doctor` warns when it detects WSL or a container, because timing-sensitive
BootROM sync is the first thing to break there.

## 6. Baud rates

| Stage | Baud |
|---|---|
| NMEA (device default) | 38400 |
| BootROM | 115200 (the BootROM ignores anything higher) |
| DA, optional fast mode | 230400 / 460800 / 912600 via `--high-baud` |

`--high-baud` speeds up a 400 KiB write a lot, but if the cable is marginal the
write fails part-way. Start without it; if a full flash takes ~60 s and you
want it faster, try `--high-baud 460800`.

## 7. What to do if the port is fine but the sync fails

1. Power-cycle the M-241 (remove the battery for 2 s).
2. Make sure the device is **on** and not in log-download mode in a vendor app.
3. Try the legacy entry: `--pmtk180` (`$PMTK180*3B` at 38400).
4. Try `--assume-brom` if a previous run left it in BootROM.
5. Try another USB port (prefer a direct port over a hub).
6. On Windows, disable USB selective suspend for that hub.

`m241 doctor` walks through these checks interactively and prints the exact
commands to try.
