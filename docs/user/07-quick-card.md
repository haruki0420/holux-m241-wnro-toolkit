# Quick card — the M-241 date fix in plain language

Print this page. It explains the words, the five commands, how to tell each step
worked, and what to do when it does not.

## Words you will see

```
firmware  the program inside the logger — the thing with the wrong date
BootROM   unchangeable helper program in the chip; lets the PC read/write the firmware
DA        download agent (DA_v3.8.4.bin): helper the tool sends so it can write
NMEA      the $GP... text sentences the logger sends to map apps
OLED      the small screen on the M-241
profile   the tool's record of one known firmware (V1.12 or V1.13)
hash      file fingerprint; yours is unique — never compare it with an example
offset    a position inside the file, e.g. 0x402F4
byte      smallest unit of data; the fix changes 3 bytes out of ~400,000
port      the PC's name for the cable, e.g. COM3 on Windows
driver    software that lets Windows talk to the cable (CP210x)
dump      copy the firmware FROM the logger to a file (read-only, safe)
patch     change that copy on the PC (the logger is not touched)
flash     write the changed firmware BACK to the logger (the only risky step)
```

## The five commands

Run them in this order. `auto` means "find the cable yourself".

| # | Command | What it does |
|---|---|---|
| 1 | `m241 ports` | Lists the cable. Look for `candidate port(s): COM3`. |
| 2 | `m241 info` | Asks the logger for its firmware version and model. |
| 3 | `m241 dump auto -o backup/stock.bin` | Saves a backup copy of your firmware. |
| 4 | `m241 patch backup/stock.bin -o backup/fixed.bin` | Makes the fixed copy on the PC. |
| 5 | `m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/fixed.bin --backup backup/stock.bin` | Writes the fixed copy. Type `WRITE` when asked. |

## How to tell each step worked

| Step | Success looks like |
|---|---|
| 1 | `candidate port(s): COM3` (your COM number may differ). |
| 2 | `release  : B-core_1.20` and `model    : 01017-00F` are printed. |
| 3 | `verified  : yes (two identical dumps)` and a `backup/stock.bin` of 406,620 bytes. Between the two reads the tool asks you to power-cycle: take the battery out for ~10 s, put it back, switch on, press Enter. |
| 4 | `profile: v1.13`, `changed offsets: 0x402F6, 0x402F7, 0x59C2C` (3 bytes), and `backup/fixed.bin` exists. |
| 5 | `flash     : written and the download agent was restarted`. Then power-cycle and check the date with `m241 nmea proxy auto --stdout`: the 9th field of `$GPRMC` is `ddmmyy`. |

## The three most likely failures

1. **`m241 ports` shows nothing.** The CP210x driver is missing, the cable is
   loose, or the logger is off. Replug the cable, try another USB port, check
   Device Manager → Ports (COM & LPT), and install the Silicon Labs CP210x
   driver if no COM port appears.

2. **`BootROM sync failed: no 0x5F reply within ...`** The logger did not enter
   the mode the tool needs. Remove the battery for ~10 s, put it back, switch
   on, retry. Close any other program holding the port. If it still fails, try
   `--pmtk180` or `--assume-brom`.

3. **The write fails part-way.** The logger's state is undefined — do not keep
   using it. Power-cycle and re-flash your own backup with
   `m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/stock.bin --backup backup/stock.bin`.
   The BootROM cannot be erased, so this usually works. A wrong image or a power
   loss at the wrong moment can still leave the unit unusable.

## Good to know

- A **failed write is usually recoverable**: the BootROM is mask ROM and cannot
  be erased. Power-cycle and re-flash your own backup.
- **Never flash an image you did not make from your own dump.**
- The **hash is per unit**. `ee52d339...` and `9f192786...` are one developer's
  logger, not a pass/fail test.
- If something looks wrong, run `m241 doctor` first.
- Writing the firmware needs `DA_v3.8.4.bin` from the old official Holux
  utility; it is not included here. See [install](02-install.md).
- More detail: [install](02-install.md), [step-by-step fix](03-fix-firmware.md),
  [recovery](04-recovery.md), [troubleshooting](06-troubleshooting.md).
