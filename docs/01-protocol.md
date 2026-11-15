# MTK BootROM / Download Agent protocol reference (as implemented)

This is the protocol our `m241.protocol` package speaks. It was recovered from
`brom.dll` + `HoluxLoggerUtility.exe` + `PowerFlash.exe` behaviour (see the
original write-up in `docs/00-background.md`) and re-verified against a fake
device in `tests/test_protocol_*.py`.

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
| `$PMTK715`/`716`/`717` | undocumented aiding (time / position / both) | `$PMTK001,nnn,3` |

The undocumented aiding commands are a bonus find from the original analysis;
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

Two ways to get there:

- **AutoBoot (preferred, no PMTK180):** pulse RTS+DTR low→high to reset the
  chip; the BootROM listens for `0xA0` on its own. This is what
  `brom.dll!BRom_AutoBoot::StartCmd` does and what `MtkBootRom.start()` uses by
  default (`use_rts_dtr=True, pmtk180=False`).
- **Legacy (MT3301 style):** send `$PMTK180*3B\r\n` on the NMEA port at the
  current NMEA baud, then sync. Kept as `start(pmtk180=True)`.

If you are already in BootROM (e.g. a previous run died), `--assume-brom` skips
the reset and only does the 4-byte sync.

## 3. BootROM commands

Every opcode, address and count byte is **echoed back** by the chip and must be
verified. Any mismatch means the framing is desynchronised.

| Opcode | Name | Arguments | Response |
|---|---|---|---|
| `0xA1` | WRITE16 | addr (BE32), word-count (BE32), data stream | data echoed per chunk |
| `0xA2` | READ16 | addr (BE32), word-count (BE32) | data + XOR16 checksum |
| `0xA4` | CHECKSUM | addr (BE32), word-count (BE32) | XOR16 |
| `0xA8` | JUMP | addr (BE32) | control transfer |
| `0xAF` | READ32 | addr (BE32), dword-count (BE32) | data + XOR16 checksum |

`word-count = byte_length / 2`, `dword-count = byte_length / 4`.

### Checksum

```
cksum = 0
for each big-endian u16 word in data: cksum ^= word
if odd length: cksum ^= last_byte << 8
```

The same function computes the checksum the chip returns for `0xA4`.

### WRITE16 byte order (the gotcha)

The wire format of `0xA1` carries **byte-swapped 16-bit pairs**: the chip expects
each 16-bit word with its bytes in the opposite order from how they land in
memory (it re-swaps internally). In practice:

```python
brom.write16(0x00100800, da_image, byte_swap=True, verify_echo=True, skip_cksum=True)
```

With `byte_swap=False` you get `1c 00 e0 00` garbage back from the DA and the
chip never starts it. With `byte_swap=True` the DA boots and reports `0xC0`
(SYNC) followed by the 20-byte info report.

### Write transaction shape

`brom.dll!DownloadData` sends `0xA1 + addr + count` **once**, then streams the
entire payload, echoing 10-byte chunks as it goes. It does **not** re-issue the
header per chunk. `MtkBootRom.write16` follows that; the last chunk's echo is
tolerated as lossy because the chip sometimes drops it.

## 4. Memory map (MT3318 as seen through BootROM)

| Range | Contents |
|---|---|
| `0x000000`–`0x00FFFF` | Bootloader (64 KiB, mirrored at `0x080000`) |
| `0x100000`–`0x1007FF` | **BootROM scratch / stack — never write here** |
| `0x100800`–`0x1FFFFF` | RAM; the DA is loaded at `0x00100800` |
| `0x20000000`–`0x20…` | SPI flash (main firmware at `0x20000000`) |
| `0x80000000`+ | registers (touching these from BootROM kills it) |
| `0xA0000000`+ | MT3318 mask ROM |

`m241 dump` reads from `0x20000000` by default. `m241 flash --start-addr 0x0`
writes through the DA's linear flash view, which is what the original write-up
did; both are supported and documented on the command.

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

| Constant | Value | Meaning |
|---|---|---|
| `0xD2` | SET_BAUD | `[0xD2][code][0]`, code 1=912600, 2=460800, 3=230400 |
| `0xD3` | MEM_CMD | `[0xD3][01][BE32 start][BE32 end]` → `0x5A` |
| `0xD5` | WRITE | `[0xD5][BE32 0x100]` → `0x5A 0x5A`, then packets |
| `0xD9` | RESTART | — (reboots into the firmware) |
| `0x5A` | ACK | |
| `0xA5` | NAK | |
| `0x69` | CONT | per-packet "send next" |
| `0xC0` | SYNC | first byte of the DA info report |
| `0x100` | packet size | |

Write sequence:

1. `D3` set the block range → ACK.
2. `D5` + packet length → two ACKs.
3. For each 256-byte packet: send packet, send **sum of bytes mod 65536** as
   BE16, expect `0x69`.
4. Two ACKs at the end.

Erasing is implicit: the DA erases the affected blocks when you set the range.
The M-241's 2 MiB flash takes a few seconds.

## 6. Failure modes worth knowing

| Symptom | Cause | Fix |
|---|---|---|
| `timeout @ BOOT_ROM_START_CMD1` | chip not in BootROM, or wrong port | power-cycle; check `m241 ports`; try `--nmea-baud 38400` |
| `unexpected response @ CMD2/3/4` | another program holds the port, or the baud is wrong | close serial monitors; retry |
| DA info never shows `0xC0` | DA written with the wrong byte order, or the wrong address | `byte_swap=True`, `0x00100800` |
| `DA SYNC mismatch` / version `!= 0x0308` | not the Holux DA | use `DA_v3.8.4.bin` from the drop |
| Write stalls mid-image | machine slept, cable glitched | re-flash the original dump |
| `read32 checksum mismatch` | read issued while the chip is still busy | retry; the client already retries small reads |
