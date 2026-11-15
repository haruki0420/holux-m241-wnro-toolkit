# tests/

Automated checks that the program in `src/` still works. **Nothing here is
installed**, and nothing here is needed to use the `m241` command.

## Running them

```bash
pytest -q                      # everything
pytest tests/test_protocol_brom.py -q    # one file
python tools/check_all.py      # lint + docs + tests in one go
```

No hardware is required. Every device interaction is driven through
`m241.protocol.simulator`, a pure-Python stand-in for the MT3318 chip.

## What each file covers

| Test file | Covers |
|---|---|
| `test_cli.py` | every CLI command, exit codes, JSON output, prompts |
| `test_protocol_brom.py` | BootROM framing, reads, writes, dump |
| `test_protocol_da.py` | download-agent handshake and the flash write path |
| `test_protocol_simulator.py` | the fake device itself |
| `test_protocol_ports.py` | serial-port discovery heuristics |
| `test_firmware_image.py` | firmware header parsing and hashing |
| `test_firmware_patcher.py` | the WNRO patch and its verification |
| `test_firmware_profiles.py` | the shipped profile database |
| `test_logfile_reader.py` | raw log-dump parsing |
| `test_logfile_export.py` | GPX / CSV / NMEA / KML / JSON output |
| `test_logfile_capacity.py` | capacity arithmetic |
| `test_logfile_download_geotag.py` | PMTK182 download and photo matching |
| `test_nmea_sentence.py` | NMEA parsing and checksums |
| `test_nmea_repair.py` | WNRO date repair |
| `test_nmea_proxy.py` | the TCP relay |
| `test_portability.py` | guards against hard-coded paths and ports |

## Rules

* **Every protocol change needs a simulator change and a test.** The fake
  device is what makes a regression fail loudly instead of silently corrupting
  a real unit.
* Hardware-shaped regressions are pinned on purpose: the real image length
  (406,620 bytes, 92-byte tail) and a full write-then-read-back.
* Do not add a test that needs a real device. If a behaviour can only be
  checked on hardware, put it in `tools/hardware/` and document it.
