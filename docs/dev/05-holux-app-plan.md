# Plan: reimplementing the Holux M-241 applications

Holux shipped four programs. All are Windows-only, all are gone. This page
inventories what each one did, maps it onto what this toolkit already covers,
and lays out how to close the rest.

## 1. What Holux shipped

| Program | What it did | Status |
|---|---|---|
| **PowerFlash.exe** | Firmware flash only | discontinued |
| **HoluxLoggerUtility.exe** | Device settings, firmware update, log download | discontinued |
| **ezTour** | Log viewer, export (KML/NMEA/GPX), photo geotagging, map | discontinued |
| **Mini GPS Viewer** | Live GPS view, cold start, aiding | discontinued |

## 2. Coverage today

| Holux function | Ours | State |
|---|---|---|
| Firmware flash | `m241 flash` | done, verified on hardware |
| Firmware dump | `m241 dump` | done |
| Firmware patch | `m241 patch` | done |
| Device identity | `m241 info` | done |
| Log download | `m241 logs download` | done, verified |
| Log erase | `m241 logs erase` | done |
| Log parse / stats | `m241 logs info` | done |
| Log export (GPX/CSV/KML/NMEA/JSON) | `m241 logs read` | done |
| Photo geotagging | `m241 logs geotag` | done |
| Live NMEA relay | `m241 nmea proxy` | done |
| NMEA date repair | `m241 nmea repair` | done |
| Undocumented aiding | `m241 info --aiding` | done |
| **Device settings** | — | **missing** |
| **Log format configuration** | — | **missing** |
| **GPS start modes** | — | **missing** |
| **GUI** | — | **missing** |
| **Live map** | — | **missing** |

## 3. Protocol gaps to close

The command set is documented by GPSBabel's `mtk_logger.c`, which is the de
facto reference for MTK loggers. Several of its findings correct assumptions
this toolkit made:

| Command | Meaning | Note |
|---|---|---|
| `$PMTK182,2,7` | **log status** | bit 1 = auto-log enabled. This is the authoritative status query. |
| `$PMTK182,2,2` | **log format / bitmask** | returns the record bitmask, e.g. `8800003D` — *not* an interval |
| `$PMTK182,2,8` | bytes used | what `logs download` already uses |
| `$PMTK182,4` | log enable | |
| `$PMTK182,5` | log disable | |
| `$PMTK182,6,1` | **erase** (takes an argument) | our erase sends `$PMTK182,6` without the `,1` |
| `$PMTK182,7,<addr>,<len>` | read log | hex address and length |
| `$PMTK101/102/103/104` | hot / warm / cold / full-cold start | |
| `$PMTK251` | set NMEA baud | |
| `$PMTK314` | set which NMEA sentences are output | |
| `$PMTK320` | power-save mode | |
| `$PMTK330/331` | datum | |
| `$PMTK390` | user option | |
| `$PMTK605` | release info | already used |

**Correct these first.** `logs erase` should send `$PMTK182,6,1`, and the
status query should prefer `$PMTK182,2,7` over `$PMTK182,2,1`.

### Settings that may not have a PMTK command

The M-241's own menus control log interval, log mode (time/distance), units,
language, backlight and Bluetooth on/off. Whether each has a PMTK equivalent is
**unknown** and must be probed:

1. send the plausible query (`$PMTK182,2,<n>`) for every `n` in 0…15 and log
   the replies;
2. compare against the value shown on the OLED while changing it with the
   buttons;
3. only then implement a setter.

Do not guess a setter. A wrong write to a settings command can change the log
format mid-session.

## 4. Architecture

Keep the layering that already exists; add one layer at a time.

```
                 ┌─────────────┐
                 │  GUI / TUI  │   optional, thin
                 └──────┬──────┘
                 ┌──────┴──────┐
                 │  CLI (m241) │   the contract
                 └──────┬──────┘
      ┌────────────┬────┴────┬────────────┐
 protocol/     logfile/   firmware/    nmea/
 (brom, da)   (reader,    (image,     (sentence,
               download,   profiles,   repair,
               geotag,     patcher)    proxy)
               capacity,
               export)
```

Rules that keep it maintainable:

* **The CLI is the API.** A GUI must call the same functions the CLI calls, not
  shell out and not duplicate protocol code.
* **`--json` on every command.** A GUI then has a stable data contract and
  needs no parsing of human output.
* **No new runtime dependency for the core.** A GUI may add one, but it must be
  an optional extra (`pip install "m241-wnro-toolkit[gui]"`).
* **Every hardware command stays testable without hardware** via
  `m241.protocol.simulator`.

## 5. Phased plan

### Phase A — protocol completeness (no GUI)

1. Fix `logs erase` to send `$PMTK182,6,1`.
2. Add `m241 logs status` using `$PMTK182,2,7` and `$PMTK182,2,2`, reporting the
   bitmask decoded into field names.
3. Add `m241 gps start {hot,warm,cold,full-cold}` (`$PMTK101…104`).
4. Add `m241 gps output` (`$PMTK314`) and `m241 gps baud` (`$PMTK251`).
5. Probe for the settings commands (section 3) and document what exists.
6. Extend the simulator for every new command, with tests.

**Exit criterion:** every non-GUI Holux function has a CLI equivalent, and
`m241 doctor` can report the device's full configuration in one shot.

### Phase B — a thin local UI

A browser-based wizard served from the CLI (`m241 gui`) is the cheapest way to
get a usable front-end:

* one HTTP server on `127.0.0.1`, bound to loopback only;
* the page calls the same functions the CLI calls;
* no framework, no build step — a single HTML file with `fetch`;
* the dangerous operations (flash, erase) keep their confirmation step and
  still require `--yes` on the command line that starts the server.

Why not a native toolkit: `tkinter` looks different on every OS and cannot show
a map; a web page can, and the project already ships a TCP NMEA relay.

### Phase C — map and photo workflow

* live position on a map (Leaflet + OpenStreetMap tiles, no API key);
* track overlay from a downloaded log;
* the existing `logs geotag` output rendered as a photo list with matched
  coordinates.

### Phase D — parity polish

* log browser (list sessions, jump to a date, delete a range);
* settings editor once Phase A has found the commands;
* firmware update flow with the same safety gates as `m241 flash`.

## 6. What not to do

* **Do not reimplement the map rendering.** Use a tile-based web map.
* **Do not reverse-engineer the proprietary binaries again.** The protocol is
  documented; the remaining gaps are probed over NMEA, which is safe and
  observable.
* **Do not ship the proprietary DA or firmware.** Users supply their own from
  the official packages; `m241 doctor --from-drop` already extracts them.
* **Do not make the GUI the only path.** Everything must remain scriptable.

## 7. Risks

| Risk | Mitigation |
|---|---|
| A settings command turns out to be destructive | probe with queries only; never send an unknown setter |
| GUI drifts from CLI behaviour | GUI calls the same functions; `--json` is the contract |
| Bluetooth path untested | mark it experimental until someone verifies it |
| Log area size still unknown | keep `logs capacity` honest, do not invent numbers |

## 8. Immediate next step

Phase A item 1 and 2: fix the erase argument and add `m241 logs status`. Both
are small, both are testable against the simulator, and both are prerequisites
for a settings editor.
