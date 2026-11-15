"""Exception hierarchy for the m241 toolkit.

Everything the package raises derives from :class:`M241Error` so callers can
catch one type. :class:`ProtocolError` marks wire-level trouble (timeouts,
framing, checksum) and :class:`FirmwareError` marks image-level trouble. Both
carry an optional byte ``offset`` so a CLI can point at the offending location.
"""

from __future__ import annotations


class M241Error(Exception):
    """Base class for all toolkit errors."""

    def __init__(self, message: str, *, offset: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.offset = offset

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.offset is None:
            return self.message
        return f"{self.message} (at offset 0x{self.offset:X})"


class ProtocolError(M241Error):
    """Wire protocol problem: timeout, framing, unexpected reply, checksum."""


class BootRomError(ProtocolError):
    """The BootROM handshake or a BootROM command failed."""


class DownloadAgentError(ProtocolError):
    """The Download Agent rejected or mishandled a request."""


class FirmwareError(M241Error):
    """The firmware image is unusable or a patch cannot be applied."""


class FirmwareProfileError(FirmwareError):
    """No supported firmware profile matches this image."""


class LogParseError(M241Error):
    """A log dump could not be parsed."""


class NmeaError(M241Error):
    """An NMEA sentence is malformed."""
