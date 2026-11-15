# src/

**This is the program.** `pip install` puts exactly this directory on your
machine and creates the `m241` command.

```
src/m241/
  cli.py           every command lives here
  errors.py        the exception hierarchy and the CLI exit codes
  protocol/        talking to the chip (BootROM, download agent, serial ports)
  firmware/        reading, patching and validating firmware images
  logfile/         reading, downloading, converting and geotagging track logs
  nmea/            NMEA sentence parsing, date repair and the TCP relay
  data/            JSON manifests shipped inside the package
```

## Dependency direction

```
cli ──┬── protocol
      ├── firmware
      ├── logfile
      └── nmea
              all of them ──> errors
```

Nothing imports `cli`. `protocol` is the only package that touches `pyserial`,
and it imports it lazily, so `m241 patch`, `m241 logs …` and `m241 nmea repair`
work on a machine without it.

## If you are not changing the code

You do not need this directory. Use the `m241` command; see the top-level
`README.md`.
