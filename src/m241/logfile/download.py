"""Download the track log from the M-241 over PMTK.

The log lives in the device's own flash and is read with PMTK commands, not
through the BootROM:

    $PMTK182,2,8            -> $PMTK182,3,8,<hex bytes used>
    $PMTK182,5              -> $PMTK001,182,5,3   (logging disabled)
    $PMTK182,7,<addr>,<len> -> $PMTK182,8,<addr>,<hex data>
    $PMTK182,4              -> $PMTK001,182,4,3   (logging enabled again)

Two details cost real time to discover on hardware:

* **The address and length fields are hexadecimal without a ``0x`` prefix.**
  Sending ``$PMTK182,7,64,64`` reads from address 0x64 and returns 0x64 bytes,
  not 64 and 64.
* **Logging must be disabled first.** Reading while the device is writing
  returns inconsistent blocks, so the downloader brackets the transfer with
  ``$PMTK182,5`` / ``$PMTK182,4`` and always re-enables logging, even on error.

Measured on a retail unit: chunks of 0x800 bytes (2048) are accepted, which
keeps a 200 KB log to about 100 round trips.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import M241Error

#: Largest chunk the device accepted during bring-up.
DEFAULT_CHUNK = 0x800

#: Per-command reply timeout in seconds.
DEFAULT_TIMEOUT = 5.0

ProgressCallback = Callable[[int, int], None]


class LogDownloadError(M241Error):
    """The device refused or truncated a log transfer."""


def nmea_checksum(sentence: str) -> int:
    """XOR of every character between ``$`` and ``*``."""
    value = 0
    for char in sentence[1:]:
        value ^= ord(char)
    return value


def with_checksum(sentence: str) -> str:
    """Return ``$…*HH`` for a sentence body without a checksum."""
    body = sentence[1:] if sentence.startswith("$") else sentence
    return f"${body}*{nmea_checksum('$' + body):02X}"


@dataclass(frozen=True)
class LogStatus:
    """What the device reports about its own log."""

    used_bytes: int
    enabled: bool
    interval_raw: int = 0

    @property
    def interval_seconds(self) -> float:
        """Logging interval in seconds (the device stores tenths)."""
        return self.interval_raw / 10.0 if self.interval_raw else 0.0


class LogDownloader:
    """Read the track log from a device that is streaming NMEA."""

    def __init__(
        self,
        serial_port: Any,
        *,
        chunk: int = DEFAULT_CHUNK,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if chunk <= 0:
            raise ValueError("chunk must be positive")
        self.ser = serial_port
        self.chunk = chunk
        self.timeout = timeout

    # -- low level -------------------------------------------------------
    def _write(self, sentence: str) -> None:
        payload = with_checksum(sentence) + "\r\n"
        self.ser.write(payload.encode("ascii"))
        flush = getattr(self.ser, "flush", None)
        if flush is not None:
            flush()

    def _read_lines(self, deadline: float) -> list[str]:
        lines: list[str] = []
        buffer = b""
        while time.monotonic() < deadline:
            chunk = self.ser.read(1)
            if not chunk:
                time.sleep(0.002)
                continue
            buffer += bytes(chunk)
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                text = raw.decode("ascii", "replace").strip()
                if text:
                    lines.append(text)
        return lines

    def _command(
        self,
        sentence: str,
        *,
        expect: str,
        timeout: float | None = None,
    ) -> str | None:
        """Send one PMTK sentence and return the first reply with *expect*."""
        self.ser.reset_input_buffer()
        self._write(sentence)
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        for line in self._read_lines(deadline):
            if line.startswith(expect):
                return line
        return None

    # -- status ----------------------------------------------------------
    def status(self) -> LogStatus:
        """Read the log usage, enable state and interval."""
        usage = self._command("$PMTK182,2,8", expect="$PMTK182,3,8")
        if usage is None:
            raise LogDownloadError(
                "the device did not answer $PMTK182,2,8 (log usage). "
                "Is the logger powered on and streaming NMEA?"
            )
        try:
            used = int(usage.split(",")[3].split("*")[0], 16)
        except (IndexError, ValueError) as exc:
            raise LogDownloadError(f"cannot parse the log usage reply {usage!r}") from exc

        enabled = False
        state = self._command("$PMTK182,2,1", expect="$PMTK182,3,1")
        if state is not None:
            fields = state.split(",")
            if len(fields) > 3:
                enabled = fields[3].split("*")[0].strip() not in ("0", "")

        interval_raw = 0
        mode = self._command("$PMTK182,2,2", expect="$PMTK182,3,2")
        if mode is not None:
            fields = mode.split(",")
            if len(fields) > 3:
                try:
                    interval_raw = int(fields[3].split("*")[0], 16)
                except ValueError:
                    interval_raw = 0
        return LogStatus(used_bytes=used, enabled=enabled, interval_raw=interval_raw)

    # -- transfer --------------------------------------------------------
    def _disable_logging(self) -> None:
        reply = self._command("$PMTK182,5", expect="$PMTK001,182,5")
        if reply is None:
            raise LogDownloadError("the device did not acknowledge $PMTK182,5")
        if ",3" not in reply:
            raise LogDownloadError(f"the device refused to stop logging: {reply!r}")

    def _enable_logging(self) -> None:
        # Best effort: a failure here must not mask the original error.
        with contextlib.suppress(Exception):
            self._command("$PMTK182,4", expect="$PMTK001,182,4")

    def _read_chunk(self, address: int, length: int) -> bytes:
        reply = self._command(
            f"$PMTK182,7,{address:X},{length:X}",
            expect="$PMTK182,8",
            timeout=self.timeout,
        )
        if reply is None:
            raise LogDownloadError(
                f"no reply for $PMTK182,7,{address:X},{length:X}"
            )
        fields = reply.split(",")
        if len(fields) < 4:
            raise LogDownloadError(f"malformed log reply: {reply[:80]!r}")
        hexdata = fields[3].split("*")[0].strip()
        try:
            data = bytes.fromhex(hexdata)
        except ValueError as exc:
            raise LogDownloadError(
                f"the log reply at 0x{address:X} is not valid hex"
            ) from exc
        return data

    def download(
        self,
        *,
        progress_cb: ProgressCallback | None = None,
        length: int | None = None,
    ) -> bytes:
        """Return the raw log contents.

        *length* defaults to the byte count the device reports.  Logging is
        disabled for the duration and re-enabled afterwards.
        """
        status = self.status()
        total = status.used_bytes if length is None else length
        if total <= 0:
            return b""
        if total < 0:
            raise LogDownloadError(f"invalid log length {total}")

        self._disable_logging()
        try:
            collected = bytearray()
            while len(collected) < total:
                want = min(self.chunk, total - len(collected))
                block = self._read_chunk(len(collected), want)
                if not block:
                    raise LogDownloadError(
                        f"the device returned no data at 0x{len(collected):X} "
                        f"({len(collected)} of {total} bytes)"
                    )
                collected += block
                if progress_cb is not None:
                    progress_cb(min(len(collected), total), total)
                if len(block) < want:
                    # The device may clamp the final chunk; stop rather than
                    # spin on the same address.
                    break
            return bytes(collected[:total])
        finally:
            self._enable_logging()


def download_log(
    serial_port: Any,
    *,
    chunk: int = DEFAULT_CHUNK,
    timeout: float = DEFAULT_TIMEOUT,
    progress_cb: ProgressCallback | None = None,
    length: int | None = None,
) -> bytes:
    """Convenience wrapper around :class:`LogDownloader`."""
    return LogDownloader(serial_port, chunk=chunk, timeout=timeout).download(
        progress_cb=progress_cb, length=length
    )
