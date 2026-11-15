# MTK BootROM / Download Agent protocol reference (as implemented)

This is the protocol our `m241.protocol` package speaks. Every value below was
checked against a real M-241 (MT3318, `B-core_1.20`, model `01017-00F`) and is
mirrored by the fake device in `tests/test_protocol_*.py`, so a regression in
the client fails the test suite.

All multi-byte values on the wire are **big-endian** unless stated otherwise.

## 1. Normal mode: NMEA + PMTK

| Item | Value |
|---|---|
| Baud | 38400 8N1 (M-241 factory default) |
| NMEA | `$GP…` sentences, 1 Hz |
| PMTK | `$PMTK<cmd>,<args>*<xor checksum>\r\n` |

Commands the toolkit uses:

| Command | Meaning | Reply |
|---|---|---|
| `$PMTK000*32` | no-op / liveness | `$PMTK001,0,3*30` |
| `$PMTK605*31` | release request | `$PMTK705,<release>,<build>,<model>,<sdk>*hh` |
| `$PMTK180*3B` | enter BootROM | (NMEA stops) |
| `$PMTK182,2,8*33` | log usage (log must be disabled) | `$PMTK182,3,8,<hex addr>` |
| `$PMTK182,7,<addr>,<len>` | read log bytes | `$PMTK182,8,<addr>,<hex data>` |
| `$PMTK182,4` / `,5` / `,6` | log enable / disable / erase | `$PMTK001,182,n,3` |

`m241 logs download` uses `,5` and `,4` around the transfer, and
`m241 logs erase` uses `,6`. All three require logging to be stopped first;
the toolkit always resumes it in a `finally` block.
| `$PMTK715`/`716`/`717` | undocumented aiding (time / position / both) | `$PMTK001,nnn,3` |

The aiding commands are a bonus find from the original analysis;
`m241 info --aiding` sends them so you can shorten a cold start. They are
harmless no-ops on firmware that does not implement them.

## 2. BootROM entry

```
host: 0xA0 ... 0xA0   (repeat up to 10 s until a reply)
chip: 0x5F
host: 0x0A   chip: 0xF5
host: 0x50   chip: 0xAF
host: 0x05   chip: 0xFA
```

The three follow-up probes must be sent **back to back**: the chip drops out of
the handshake if there is a pause between them.

Two ways to get there:

- **`$PMTK180*3B` (verified route on a retail M-241).** Send the sentence at the
  NMEA baud **while NMEA is still streaming**; the stream stops and the chip
  listens for `0xA0` at 115200. `MtkBootRom.start(pmtk180=True)` does this.
  If NMEA is not flowing the sentence is ignored, so the client checks for the
  stream first and reports a precise error instead of a bare sync timeout.
- **AutoBoot (reset lines).** Pulse RTS/DTR to reset the chip and probe `0xA0`.
  `MtkBootRom.start()` uses this by default. It works when the firmware is not
  running (for example after an interrupted flash), but **pulsing the reset
  lines at a logger that is running normally can leave it silent** -- so the
  client only takes this route when it is certain nothing is streaming.

If you are already in BootROM (e.g. a previous run died), `--assume-brom` skips
the reset and only does the 4-byte sync.

## 3. BootROM commands

Every opcode, address and count byte is **echoed back** by the chip. Any
mismatch means the framing is desynchronised.

| Opcode | Name | Arguments | Response |
|---|---|---|---|
| `0xA1` | WRITE16 | addr (BE32), **word**-count (BE32), data stream | data echoed as it arrives |
| `0xA2` | READ16 | addr (BE32), **word**-count (BE32) | data only |
| `0xA4` | CHECKSUM | addr (BE32), **word**-count (BE32) | XOR16 |
| `0xA8` | JUMP | addr (BE32) | control transfer |
| `0xAF` | READ32 | addr (BE32), **dword**-count (BE32) | data only |

`word-count = byte_length / 2`, `dword-count = byte_length / 4`.

**Reads carry no checksum trailer.** `0xA2`/`0xAF` return the echo followed by
the data and nothing else; the checksum is a separate `0xA4` command. Consuming
a two-byte trailer desynchronises every later frame -- that was a real bug in
this toolkit, fixed and now covered by
`tests/test_protocol_brom.py::test_read_has_no_trailing_checksum_word`.

### Checksum

```
cksum = 0
for each big-endian u16 word in data: cksum ^= word
if odd length: cksum ^= last_byte << 8
```

The same function computes the checksum the chip returns for `0xA4`.

### Byte order (the gotcha)

The two read/write widths order their bytes differently, and getting this wrong
produces plausible-looking garbage:

| Opcode | Wire order | Conversion |
|---|---|---|
| `0xA2` / `0xA1` | 16-bit pairs swapped | `swap_pairs` |
| `0xAF` | each 32-bit word byte-reversed | `reverse_words32` |

`0xA1` carries **byte-swapped 16-bit pairs**: the chip expects each 16-bit word
with its bytes in the opposite order from how they land in memory (it re-swaps
internally). In practice:

```python
brom.write16(0x00100800, da_image, byte_swap=True, verify_echo=True, skip_cksum=True)
```

With `byte_swap=False` you get `1c 00 e0 00` garbage back from the DA and the
chip never starts it. With `byte_swap=True` the DA boots and reports `0xC0`
(SYNC) followed by the 20-byte info report.

### Write transaction shape, and the one-byte echo lag

The write command sends `0xA1 + addr + count` **once**, then streams the entire
payload in ten-byte chunks. It does **not** re-issue the header per chunk.

The chip's echo **trails by exactly one byte**: after sending N bytes only N-1
echo bytes are available, and the echo of the final byte never arrives because
nothing follows it. A client that requires an exact echo for every byte -- or
that writes one byte and waits for its echo -- hangs on real hardware.

`MtkBootRom.write16` therefore accumulates the echo and compares it against
everything sent so far *minus its last byte*:

```python
want = len(sent) - 1
if want > len(echo):
    echo += read_exact(want - len(echo))
if echo != sent[:-1]:
    raise BootRomError(...)
```

After the last chunk the client drains whatever is left so the next command
starts on a frame boundary.

## 4. Memory map (MT3318 as seen through BootROM)

| Range | Contents |
|---|---|
| `0x000000`–`0x00FFFF` | Bootloader (64 KiB, mirrored at `0x080000`) |
| `0x100000`–`0x1007FF` | **BootROM scratch / stack — never write here** |
| `0x100800`–`0x1FFFFF` | RAM; the DA is loaded at `0x00100800` |
| `0x00200000`– | SPI flash, **main firmware at `0x00200000`** |
| `0x80000000`+ | registers (touching these from BootROM kills it) |
| `0xA0000000`+ | MT3318 mask ROM |

The firmware base is `0x00200000`, measured: reading `0x20000000` returns
`42 20 02 02` (unmapped bus) while `0x00200000` returns the vector table
`18 f0 9f e5 …`. `m241 dump` reads from `0x00200000`.

The DA sees flash through a **linear view that starts at the firmware image**,
so DA offset `0x0000` is BootROM address `0x00200000`. `m241 flash` uses that
view; the `--start-addr` option exists for completeness.

## 5. Download Agent

After `JUMP 0x00100800` the DA prints a 20-byte info report. Skip anything before
the first `0xC0`:

```
struct DA_INFO {
    uint8_t  sync_char;              // 0xC0
    uint16_t da_version;             // 0x0308 for DA v3.8
    uint8_t  flash_device_id;        // 0xFF = unsupported
    uint32_t flash_size;             // bytes
    uint16_t flash_manufacturer_code;
    uint16_t flash_device_code;
    uint16_t flash_ext_device_code1;
    uint16_t flash_ext_device_code2;
    uint32_t ext_sram_size;
};   // packed, big-endian, ">BHBLHHHHL" = 20 bytes
```

A retail M-241 reports `flash_size = 0x80000` (512 KiB, MX29LV400CB,
manufacturer `0xC2`, device `0x22BA`). Do not assume 2 MiB.

| Constant | Value | Meaning |
|---|---|---|
| `0xD2` | SET_BAUD | `[0xD2][code][0]`, code 1=912600, 2=460800, 3=230400 |
| `0xD3` | MEM_CMD | `[0xD3][01][BE32 start][BE32 end]` → **`0x5A` + status byte** |
| `0xD5` | WRITE | `[0xD5][BE32 0x100]` → `0x5A 0x5A`, then packets |
| `0xD9` | RESTART | — (reboots into the firmware) |
| `0x5A` | ACK | |
| `0xA5` | NAK | |
| `0x69` | CONT | per-packet "send next" |
| `0xC0` | SYNC | first byte of the DA info report |
| `0x100` | packet size | |

### The `0xD3` reply is two bytes

`0xD3` is answered by **ACK followed by a status byte** (`0x00` on success).
Reading only the ACK leaves the status byte in the buffer, which shifts every
later frame by one byte; the next command then times out. This is the single
most important detail in the whole write path.

### Write sequence

1. `D3` set the block range → **ACK + status byte** (the wait can be long: this
   also erases the affected sectors).
2. `D5` + packet length → two ACKs.
3. For each 256-byte packet: send packet, send **sum of bytes mod 65536** as
   BE16, expect `0x69`.
4. Two ACKs at the end.

The final packet may be shorter than 256 bytes; the DA accepts it. A full
406,620-byte image is 1,588 packets of 256 bytes plus a 92-byte tail, and that
exact shape is written by `m241 flash` and covered by
`tests/test_protocol_da.py::test_write_firmware_handles_the_real_image_length`.

Erasing is implicit: the DA erases the affected sectors when you set the range.
On the 512 KiB part a full-image flash takes a few seconds.

## 6. Failure modes worth knowing

| Symptom | Cause | Fix |
|---|---|---|
| `timeout @ BOOT_ROM_START_CMD1` | chip not in BootROM, or wrong port | power-cycle; check `m241 ports`; try `--nmea-baud 38400` |
| `unexpected response @ CMD2/3/4` | another program holds the port, or the baud is wrong | close serial monitors; retry |
| `no NMEA stream before $PMTK180` | the logger is not in normal mode (already in BootROM, off, or silent) | power-cycle the logger |
| DA info never shows `0xC0` | DA written with the wrong byte order, or the wrong address | `byte_swap=True`, `0x00100800` |
| `DA SYNC mismatch` / version `!= 0x0308` | not the Holux DA | use `DA_v3.8.4.bin` from the drop |
| `0xD5` ACK times out right after `0xD3` | the `0xD3` status byte was left unread | read two bytes for `0xD3` |
| write16 echo mismatch mid-payload | echo compared without allowing the one-byte lag | compare against `sent[:-1]` |
| Write stalls mid-image | machine slept, cable glitched | re-flash the original dump |
| Logger silent after a failed run | the chip is waiting in BootROM, or the reset lines were pulsed at a running firmware | power-cycle, then retry |

### Recovering from an interrupted flash

The BootROM lives in mask ROM, so a failed flash is a soft brick: the chip still
answers `0xA0` once the bootloader runs. To restore:

1. power-cycle the logger,
2. `m241 flash PORT --da DA --fw <your stock dump> --backup <your stock dump> --yes`
   (an image identical to the backup is accepted -- zero differing bytes),
3. power-cycle again.

`m241 flash` refuses images that are not a patched copy of your own backup, so
recovery always uses your own dump.
