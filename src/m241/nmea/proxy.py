"""NMEA proxy: read a GPS stream from a serial-ish source, rewrite dates, fan out.

The M-241 speaks NMEA at 38400 baud over USB/Bluetooth.  This module turns the
logger into a "fixed" NMEA source for mapping software: it reads raw lines from
any duck-typed serial object (``read``/``write``/``close``), repairs WNRO dates
with :func:`m241.nmea.repair.repair_sentence` and serves the result either to
stdout or over TCP.

The proxy never imports ``pyserial`` itself.  When it is given a device name it
resolves it lazily through ``m241.protocol.ports.open_serial`` so that using the
tool on a machine without a serial port still imports cleanly.

Testing hooks:

* pass any object with ``read(size) -> bytes`` instead of a port name,
* :func:`make_proxy` builds a proxy around an ``io.BytesIO``-style input and a
  file-like output, and :meth:`NmeaProxy.pump` runs it in-process,
* :meth:`NmeaProxy.serve_tcp` binds an ephemeral port when ``port=0`` and
  exposes it as :attr:`NmeaProxy.bound_port` once :attr:`NmeaProxy.ready_event`
  is set.
"""

from __future__ import annotations

import contextlib
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Protocol, TextIO, runtime_checkable

from ..errors import M241Error
from .repair import repair_sentence

DEFAULT_BAUD = 38400
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 10110
DEFAULT_TIMEOUT = 1.0
DEFAULT_POLL_INTERVAL = 0.05


@runtime_checkable
class SerialLike(Protocol):
    """Minimal duck type of :class:`serial.Serial` used by the proxy."""

    def read(self, size: int = 1) -> bytes: ...  # pragma: no cover - protocol

    def write(self, data: bytes) -> Any: ...  # pragma: no cover - protocol

    def close(self) -> None: ...  # pragma: no cover - protocol


class NmeaProxy:
    """Read NMEA from a serial source, repair dates, serve to stdout or TCP.

    Args:
        port: A device name (opened lazily through ``m241.protocol.ports``) or
            any duck-typed serial-like object for tests.
        offset_days: Explicit WNRO shift in days (``0`` = derive from *auto*).
        auto: Only shift dates that look WNRO-broken.
        baud: Serial baud rate used when *port* is a device name.
        wall_clock: Date used by the auto heuristic (defaults to today).
        serial_factory: Optional ``callable(port, baud) -> serial-like`` used
            instead of ``m241.protocol.ports.open_serial``.
        stop_on_eof: Stop when the source returns no data.  Defaults to True for
            an injected serial-like object (a fake or file ends at EOF) and to
            False for a device name (a real port returns b"" on a read timeout
            and must keep polling).
        poll_interval: Seconds to sleep after an empty read when
            ``stop_on_eof`` is False.
        encoding: Text codec for serial traffic (NMEA is ASCII).
    """

    def __init__(
        self,
        port: str | SerialLike | None = None,
        *,
        offset_days: int = 0,
        auto: bool = True,
        baud: int = DEFAULT_BAUD,
        wall_clock: Any = None,
        serial_factory: Callable[..., Any] | None = None,
        stop_on_eof: bool | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        encoding: str = "latin-1",
    ) -> None:
        if port is None:
            raise M241Error("NmeaProxy requires a port name or a serial-like object")
        self.port: str | None = port if isinstance(port, str) else None
        self.offset_days = int(offset_days)
        self.auto = bool(auto)
        self.baud = int(baud)
        self.wall_clock = wall_clock
        self.encoding = encoding
        # An injected object (a fake or a file) ends at EOF; a real serial port
        # opened through pyserial returns b"" on a read timeout and must keep
        # polling instead.  A caller-supplied factory is assumed fake-like.
        if stop_on_eof is None:
            real_serial = isinstance(port, str) and serial_factory is None
            self.stop_on_eof = not real_serial
        else:
            self.stop_on_eof = bool(stop_on_eof)
        self.poll_interval = float(poll_interval)

        self._serial_arg = None if isinstance(port, str) else port
        self._serial: Any = None
        self._serial_factory = serial_factory
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._server_socket: socket.socket | None = None
        self._client_sockets: list[socket.socket] = []
        self._thread: threading.Thread | None = None
        self.lines_processed = 0
        self.lines_changed = 0
        self.ready_event = threading.Event()
        self.bound_address: tuple[str, int] | None = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> NmeaProxy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the serial source and every open socket (idempotent)."""
        self.stop()
        with self._lock:
            serial_obj, self._serial = self._serial, None
            injected, self._serial_arg = self._serial_arg, None
        for handle in (serial_obj, injected if serial_obj is None else None):
            if handle is None:
                continue
            with contextlib.suppress(Exception):
                handle.close()

    def stop(self) -> None:
        """Ask every serve loop to stop and wake it up."""
        self._stop.set()
        with self._lock:
            server, self._server_socket = self._server_socket, None
            clients = list(self._client_sockets)
            self._client_sockets.clear()
        for sock in [server, *clients]:
            if sock is None:
                continue
            with contextlib.suppress(OSError):
                sock.close()

    @property
    def stopped(self) -> bool:
        """True once :meth:`stop` has been called."""
        return self._stop.is_set()

    @property
    def bound_port(self) -> int | None:
        """TCP port actually bound by :meth:`serve_tcp` (``None`` before)."""
        return None if self.bound_address is None else self.bound_address[1]

    # -- serial plumbing ---------------------------------------------------
    def _open_serial(self) -> Any:
        with self._lock:
            if self._serial is not None:
                return self._serial
            if self._serial_arg is not None:
                self._serial = self._serial_arg
                return self._serial
            factory = self._serial_factory
            if factory is None:
                from ..protocol.ports import open_serial  # lazily avoids pyserial

                factory = open_serial
            try:
                self._serial = factory(self.port, baud=self.baud)
            except TypeError:
                self._serial = factory(self.port, self.baud)
            return self._serial

    def _read_chunk(self, size: int = 4096) -> bytes:
        serial_obj = self._open_serial()
        data = serial_obj.read(size)
        if isinstance(data, str):
            return data.encode(self.encoding)
        return bytes(data or b"")

    def _write_bytes(self, sink: Any, payload: bytes) -> None:
        try:
            sink.write(payload)
        except TypeError:
            sink.write(payload.decode(self.encoding, errors="replace"))

    def iter_raw_lines(self) -> Iterator[str]:
        """Yield decoded lines from the source until EOF or :meth:`stop`."""
        buffer = b""
        while not self.stopped:
            chunk = self._read_chunk()
            if not chunk:
                if self.stop_on_eof or self.stopped:
                    break
                time.sleep(self.poll_interval)
                continue
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                yield raw.decode(self.encoding, errors="replace") + "\n"
        if buffer:
            yield buffer.decode(self.encoding, errors="replace")

    def rewrite(self, line: str) -> tuple[str, bool]:
        """Repair one line and update the counters."""
        repaired, changed = repair_sentence(
            line,
            offset_days=self.offset_days,
            auto=self.auto,
            wall_clock=self.wall_clock,
        )
        self.lines_processed += 1
        if changed:
            self.lines_changed += 1
        return repaired, changed

    def iter_repaired_lines(self) -> Iterator[str]:
        """Yield repaired lines (timestamps shifted, checksums recomputed)."""
        for line in self.iter_raw_lines():
            repaired, _changed = self.rewrite(line)
            yield repaired

    def pump(
        self,
        sink: Any,
        *,
        stop_event: threading.Event | None = None,
        max_lines: int | None = None,
        flush: bool = True,
    ) -> int:
        """Copy repaired lines from the source to *sink*; return the line count.

        *sink* is any object with a ``write(str)``/``write(bytes)`` method
        (``io.StringIO``, ``io.BytesIO``, a socket wrapper, ...).  Stops at EOF,
        after *max_lines* lines, or when *stop_event* / :meth:`stop` fires.
        """
        count = 0
        for line in self.iter_repaired_lines():
            if stop_event is not None and stop_event.is_set():
                break
            self._write_bytes(sink, line.encode(self.encoding))
            count += 1
            if flush and hasattr(sink, "flush"):
                with contextlib.suppress(OSError, ValueError):
                    sink.flush()
            if max_lines is not None and count >= max_lines:
                break
        return count

    # -- serving modes -----------------------------------------------------
    def serve_stdout(
        self,
        stop_event: threading.Event | None = None,
        *,
        out: TextIO | None = None,
    ) -> None:
        """Stream repaired sentences to *out* (default: ``sys.stdout``)."""
        stream = out if out is not None else sys.stdout
        for line in self.iter_repaired_lines():
            if stop_event is not None and stop_event.is_set():
                break
            if self.stopped and not self.stop_on_eof:
                break
            stream.write(line)
            stream.flush()

    def serve_tcp(
        self,
        host: str = DEFAULT_TCP_HOST,
        port: int = DEFAULT_TCP_PORT,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Serve repaired sentences over TCP until stopped.

        Clients connect and receive NMEA lines as they arrive.  The listener
        uses ``SO_REUSEADDR``; pass ``port=0`` for an ephemeral port and read
        :attr:`bound_port` after :attr:`ready_event` is set.
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
            server.listen(1)
            server.settimeout(0.2)
        except OSError as exc:
            server.close()
            raise M241Error(f"cannot listen on {host}:{port}: {exc}") from exc
        self.bound_address = server.getsockname()
        with self._lock:
            self._server_socket = server
        self.ready_event.set()
        wait_event = stop_event if stop_event is not None else self._stop
        try:
            while not wait_event.is_set():
                try:
                    conn, _addr = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                if wait_event.is_set():
                    conn.close()
                    break
                with self._lock:
                    self._client_sockets.append(conn)
                try:
                    self._serve_client(conn, wait_event)
                finally:
                    with self._lock:
                        if conn in self._client_sockets:
                            self._client_sockets.remove(conn)
                    with contextlib.suppress(OSError):
                        conn.close()
        except OSError:
            pass
        finally:
            with self._lock:
                if self._server_socket is server:
                    self._server_socket = None
            self.ready_event.set()
            with contextlib.suppress(OSError):
                server.close()

    def _serve_client(self, conn: socket.socket, wait_event: threading.Event) -> None:
        conn.settimeout(0.5)
        for line in self.iter_repaired_lines():
            if wait_event.is_set() or self.stopped:
                break
            try:
                conn.sendall(line.encode(self.encoding))
            except OSError:
                return

    def serve_tcp_in_thread(
        self,
        host: str = DEFAULT_TCP_HOST,
        port: int = DEFAULT_TCP_PORT,
        stop_event: threading.Event | None = None,
        *,
        ready_timeout: float = 5.0,
    ) -> threading.Thread:
        """Start :meth:`serve_tcp` on a daemon thread; wait for the bind.

        Returns the thread so callers can ``join`` it after :meth:`stop`.
        """
        thread = threading.Thread(
            target=self.serve_tcp,
            args=(host, port, stop_event),
            name="m241-nmea-proxy",
            daemon=True,
        )
        thread.start()
        if not self.ready_event.wait(ready_timeout):
            raise M241Error("NMEA proxy TCP server did not start in time")
        self._thread = thread
        return thread


def make_proxy(
    source: Any,
    sink: Any = None,
    *,
    offset_days: int = 0,
    auto: bool = True,
    wall_clock: Any = None,
    **kw: Any,
) -> NmeaProxy:
    """Build an in-process proxy around *source* (and optionally *sink*).

    *source* is any object with ``read(size) -> bytes`` -- an
    ``io.BytesIO`` filled with NMEA lines works.  When *sink* is supplied it is
    attached as :attr:`NmeaProxy.sink` for :func:`pump_proxy`.
    """
    proxy = NmeaProxy(
        source,
        offset_days=offset_days,
        auto=auto,
        wall_clock=wall_clock,
        stop_on_eof=True,
        **kw,
    )
    proxy.sink = sink  # type: ignore[attr-defined]
    return proxy


def pump_proxy(source: Any, sink: Any, **kw: Any) -> int:
    """Convenience one-shot: repair everything from *source* into *sink*."""
    proxy = make_proxy(source, sink, **kw)
    return proxy.pump(sink)


def rewrite_lines(
    lines: Iterable[str],
    *,
    offset_days: int = 0,
    auto: bool = True,
    wall_clock: Any = None,
) -> list[str]:
    """Repair a finite list of lines in memory (helper for callers and tests)."""
    result: list[str] = []
    for line in lines:
        repaired, _changed = repair_sentence(
            line, offset_days=offset_days, auto=auto, wall_clock=wall_clock
        )
        result.append(repaired)
    return result


__all__ = [
    "DEFAULT_BAUD",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_TCP_HOST",
    "DEFAULT_TCP_PORT",
    "DEFAULT_TIMEOUT",
    "NmeaProxy",
    "SerialLike",
    "make_proxy",
    "pump_proxy",
    "rewrite_lines",
]
