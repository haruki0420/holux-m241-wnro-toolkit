# ⚠️ DISCLAIMER — read before you flash anything

**Short version: you can permanently destroy your Holux M-241. Do not run the
flash step unless you accept that.**

## 1. This is firmware modification of an abandoned device

The Holux M-241 was discontinued years ago and Holux left the GPS business in
2019. There is no manufacturer support, no service centre, no replacement
mainboard, and **no JTAG header on the PCB**. If you hard-brick the unit, the
only recovery is chip-level work on the MT3318 BGA package. Practically, a
hard brick means the device is scrap.

## 2. What can go wrong

| Failure | Consequence |
|---|---|
| Power lost / cable unplugged mid-write | Flash left half-erased → **soft brick** (usually recoverable by re-flashing a full dump) |
| Wrong `--start-addr` | Firmware written to the wrong flash region → unbootable |
| Wrong image for your hardware | Unbootable, possible BRICK |
| Writing to `0x00100000`–`0x001007FF` | The BootROM's own stack; the chip **dies instantly** (this is why the DA load address is `0x00100800`) |
| Interrupting the DA load | BootROM session dies; power-cycle and retry (usually fine) |
| Battery dies during a log download | Log data may be lost; device itself is fine |
| Hard brick | No JTAG → the unit is permanently unusable |

> The BootROM is in mask ROM and cannot be erased, so a failed write is usually
> recoverable by power-cycling and re-flashing your own backup. A wrong image, or
> a power loss at the wrong moment, can still leave the unit permanently
> unusable.

## 3. Non-negotiable pre-flight checklist

1. **Dump your own firmware first** (`m241 dump`). It reads the image twice and
   refuses to save unless both SHA256 hashes are identical, so you do not have
   to do that by hand. Keep the file somewhere safe and read-only. It is your
   only rollback.
2. **Verify the dump matches a known profile** (`m241 patch --dry-run`
   prints the detected profile). If detection fails, stop — do not force it.
3. Use a **known-good USB cable** and a machine that will not sleep, update,
   or lose power during the write. On a laptop, plug in the charger.
4. Fresh battery in the M-241 (flashing is powered over USB, but a write
   interrupted by a battery-brownout is still a bad day).
5. Close every other program that might open the serial port (vendor
   utilities, gpsd, serial monitors, screen/minicom).
6. **Only flash an image you generated yourself** from your own dump. Do not
   flash an image you found on the internet — including one from this
   project's users.

## 4. Warranty, liability, law

The software is provided **AS IS**, without warranty of any kind, under the MIT
licence. The authors are not liable for any damage to hardware, data, or
property, nor for any injury, nor for any legal consequence of your use.

Modifying firmware for personal repair is lawful in many jurisdictions, but not
all. **You are responsible for knowing the rules where you live.**

## 5. Resale

A patched unit is a **modified** unit. Selling it as "original" or "unmodified"
is fraud. The default patch set rewrites the on-device version string to
`V1.14` precisely so a modified unit is identifiable at a glance. If you pass
the device on, say that it is modified.

## 6. Flash wear and data loss

The log area is a NOR flash chip. Erasing and re-writing is normal, but
`m241` never erases logs unless you explicitly ask it to. Downloading logs does
not erase them. If you use the overwrite logging mode, old tracks are lost as
new ones are written — that is the device's behaviour, not this tool's.

## 7. What this project deliberately does not do

- It does not ship proprietary binaries, firmware images, or vendor tools.
- It does not modify your device without an explicit confirmation step.
- It does not "phone home", collect telemetry, or upload your logs anywhere.
  Everything runs locally; the only network feature is the optional NMEA proxy
  which binds to `127.0.0.1` by default.

## 8. If you are not comfortable with the above

Don't flash. The alternative fixes are non-destructive:

- Post-process logs with a `+7168 days` correction (GPSBabel, `m241 logs read
  --utc-offset-days 7168`, or any spreadsheet).
- Run `m241 nmea proxy` to correct the live NMEA stream on your computer
  (the device's own OLED clock stays wrong, but every connected application
  sees the right date).

Both are documented in the README. Neither can brick anything.
