# Installation and platform notes

The toolkit is one command, `m241`. It is a pure-Python package: the only runtime
dependency is `pyserial`, there is nothing to compile, and you do **not** need to
clone the repository.

## Step 0 — what your PC needs

| Need | Why | How to check |
|---|---|---|
| Python **3.10 or newer** | runs the toolkit | `python --version` |
| Git | `pip` uses it to fetch the package from GitHub | `git --version` |
| A terminal | you type the commands there | see below |
| The USB cable | connects the logger | — |

### Install Python

Download it from <https://www.python.org/downloads/> and run the installer.
On Windows, tick **“Add python.exe to PATH”** on the first screen. Then open a
new terminal and check:

```bash
python --version
```

If Windows opens the Microsoft Store instead of printing a version, install
Python from python.org as above (the Store stub is not the right thing).

### Install Git

* **Windows:** <https://git-scm.com/download/win> — the default options are fine.
* **macOS:** `xcode-select --install`, or the installer from git-scm.com.
* **Linux (Debian/Ubuntu):** `sudo apt install git`.

Check with `git --version`.

### Open a terminal

* **Windows 11:** right-click the Start button → **Terminal** (or Start →
  “PowerShell”).
* **Windows 10:** Start → “Windows PowerShell”.
* **macOS:** Applications → Utilities → Terminal.
* **Linux:** usually `Ctrl+Alt+T`.

Type each command below into that window and press Enter.

## Step 1 — install the toolkit

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
```

That is the whole install. `pip` also installs `pyserial`. If `pip` is not
found, use `python -m pip install ...` instead.

Verify the install — none of these needs the logger:

```bash
m241 --help
m241 doctor --simulate     # exercises the whole stack against the fake device
m241 selftest              # end-to-end dump -> patch -> flash -> read, no hardware
```

Both `doctor --simulate` and `selftest` must exit 0 on a healthy install. CI runs
them on Linux, Windows and macOS — but CI has no real logger attached, so that
only proves the code runs, not that a device works.

To uninstall: `pip uninstall m241-wnro-toolkit`.

## Step 2 — Windows 10 / 11

**Hardware-verified platform.** Everything in this project was measured on
Windows 11 with a retail M-241.

### Driver

The bundled cable is a **Silicon Labs CP210x** USB-UART bridge. Windows 10/11
usually installs the driver automatically from Windows Update. If `m241 ports`
shows nothing:

1. Install the Silicon Labs CP210x driver by running
   `CP210xVCPInstaller_x64.exe` (x86 for 32-bit Windows). The installer comes
   from Silicon Labs; it is also bundled in the official Holux
   `USB Cable Driver(Win10)` package.
2. Replug the cable.
3. `m241 ports` should now list a candidate port.

### Naming

Windows uses `COM<n>` everywhere. The tool accepts:

```bash
m241 dump COM5 -o backup/stock.bin
m241 dump auto -o backup/stock.bin     # picks the CP210x automatically
```

`auto` refuses to guess if more than one plausible cable is attached; it lists
them and exits with code 3.

### What `m241 ports` prints

```
DEVICE            CAND  DESCRIPTION                       HWID
COM5              *     CP210x USB to UART Bridge         USB VID:PID=10C4:EA60
candidate port(s): COM5
```

The `*` marks a plausible M-241 cable. The line to read is
`candidate port(s):` — that is the name to pass to the other commands.

### Common Windows errors

| Message | Cause | Fix |
|---|---|---|
| `Access is denied` / `PermissionError: COM5` | another program has the port open | close vendor utilities, Arduino IDE, PuTTY, serial monitors; check Task Manager for `HoluxLoggerUtility.exe` |
| `FileNotFoundError: COM5` | wrong COM number, or the cable was unplugged | `m241 ports` again |
| `device reports readiness to read but returned no data` | the device reset mid-read | power-cycle the M-241 and retry |
| Antivirus blocks the write | overly eager behavioural AV | whitelist the Python install, or run from an excluded folder |

Windows has no `dialout` group and no udev rules: if the port exists and is not
in use, you can open it.

## Step 3 — Linux (⚠️ unverified with a real logger)

The code runs in CI, but no Linux machine has ever been connected to a real
M-241 for this project. Treat the steps below as untested.

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
| `/dev/ttyUSB0` disappears | power management suspending the port | `echo -1 \| sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer` |

## Step 4 — macOS (⚠️ unverified with a real logger)

The code runs in CI, but no Mac has ever been connected to a real M-241 for this
project. Treat the steps below as untested.

Modern macOS (Big Sur and later, Intel and Apple Silicon) needs **no driver**
for CP210x: the built-in stack exposes `/dev/tty.usbserial-*` (sometimes
`/dev/cu.usbserial-*`). Run `m241 ports` and read the `candidate port(s):` line:

```bash
m241 ports
# candidate port(s): /dev/tty.usbserial-0001
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

## Step 5 — The DA file: DA_v3.8.4.bin

Writing the firmware needs one file that this project **cannot** give you:
`DA_v3.8.4.bin`, the **download agent**. It is proprietary, it is not in the
repository, and it shipped inside the old official **HoluxLoggerUtility**
Windows package. Holux left the GPS business in 2019, so you have to obtain that
package yourself.

* **Without `DA_v3.8.4.bin` the firmware cannot be written.** There is no
  substitute.
* You do **not** need it for reading logs, converting tracks, NMEA repair/proxy
  or photo geotagging — all of those work without it.

Once you have the package, point the tool at the folder and let it extract and
verify the file:

```bash
m241 doctor --from-drop <folder> --extract-to vendor/
```

This writes `vendor/DA_v3.8.4.bin` and checks its SHA-256. You can also pass the
file directly with `--da`.

## Step 6 — Baud rates

| Stage | Baud |
|---|---|
| NMEA (device default) | 38400 |
| BootROM | 115200 (the BootROM ignores anything higher) |
| DA, optional fast mode | 230400 / 460800 / 921600 via `--high-baud` |

`--high-baud` speeds up a 400 KiB write a lot, but if the cable is marginal the
write fails part-way. Start without it; if a full flash feels slow and you want
it faster, try `--high-baud 460800`.

## Step 7 — WSL2 / Docker / VM (⚠️ unverified)

Serial passthrough works but is fragile. If you must:

* **WSL2:** use `usbipd-win` to attach the CP210x, then talk to `/dev/ttyUSB0`.
* **Docker:** `--device /dev/ttyUSB0` (Linux host only).
* **Windows/macOS VM:** USB passthrough, and disable sleep while flashing.

The BootROM is in mask ROM and cannot be erased, so a failed write is usually
recoverable by power-cycling and re-flashing your own backup. A wrong image, or
a power loss at the wrong moment, can still leave the unit permanently unusable.

## Step 8 — If the port is fine but the sync fails

1. Power-cycle the M-241 (remove the battery for 10 s).
2. Make sure the device is **on** and not in log-download mode in a vendor app.
3. Try the legacy entry: `--pmtk180` (`$PMTK180*3B` at 38400).
4. Try `--assume-brom` if a previous run left it in BootROM.
5. Try another USB port (prefer a direct port over a hub).
6. On Windows, disable USB selective suspend for that hub.

If none of that helps, see [Troubleshooting](06-troubleshooting.md).

---

## Appendix — developing from a source checkout

Only needed if you want to change the code. It requires Git and a checkout:

```bash
git clone https://github.com/haruki0420/holux-m241-wnro-toolkit.git
cd holux-m241-wnro-toolkit
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"     # also installs pytest and ruff
m241 --help
```

Run all checks with `python tools/check_all.py` (tests, lint and the docs link
check). The editable install is for contributors only; users should use the
one-line install in Step 1.
