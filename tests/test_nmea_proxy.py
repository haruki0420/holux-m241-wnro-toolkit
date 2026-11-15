"""Tests for the NMEA proxy: in-process fakes, stdout sink and real TCP."""

from __future__ import annotations

import io
import socket
import threading
import time

import pytest

from m241.errors import M241Error
from m241.nmea import NmeaProxy, is_valid, make_proxy, pump_proxy, rewrite_lines
from m241.nmea.proxy import SerialLike

SAMPLE = "$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F"
MODERN = (
    "$GPRMC,022127.542,A,3541.1234,N,13946.5678,E,12.5,270.0,030526,1.5,W,A*2B"
)
DEBUG = "======Setting_ProcessKeys========\r\n"
GGA = "$GPGGA,022127.542,3541.1234,N,13946.5678,E,1,08,0.95,42.5,M,35.0,M,,*5E"


class FakeSerial:
    """Minimal duck-typed serial port: feeds bytes and records writes."""

    def __init__(self, data: bytes, *, chunk: int = 64) -> None:
        self._data = bytearray(data)
        self._chunk = chunk
        self.written = bytearray()
        self.closed = False
        self.read_calls = 0
        self._lock = threading.Lock()

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            self.read_calls += 1
            if not self._data:
                return b""
            take = min(size, self._chunk, len(self._data))
            chunk = bytes(self._data[:take])
            del self._data[:take]
            return chunk

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class StrSerial:
    """A fake whose ``read`` returns ``str`` instead of ``bytes``."""

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True)
        self.closed = False

    def read(self, size: int = 1) -> str:  # noqa: ARG002 - signature compatibility
        if not self._lines:
            return ""
        return self._lines.pop(0)

    def write(self, data: str) -> int:  # noqa: ARG002
        return 0

    def close(self) -> None:
        self.closed = True


def feed(*lines: str) -> bytes:
    return "".join(line if line.endswith("\n") else line + "\r\n" for line in lines).encode(
        "ascii"
    )


# ---------------------------------------------------------------------------
# In-process pumping
# ---------------------------------------------------------------------------
def test_pump_repairs_and_keeps_debug_lines() -> None:
    source = FakeSerial(feed(DEBUG, SAMPLE, MODERN, GGA))
    sink = io.StringIO()
    proxy = make_proxy(source)
    count = proxy.pump(sink)

    assert count == 4
    out = sink.getvalue().splitlines()
    assert len(out) == 4
    assert out[0].strip() == DEBUG.strip()
    assert "030526" in out[1]
    assert is_valid(out[1])
    assert out[2].startswith("$GPRMC") and "030526" in out[2]
    assert out[2] == MODERN  # modern date: untouched
    assert out[3] == GGA
    assert proxy.lines_processed == 4
    assert proxy.lines_changed == 1


def test_pump_without_auto_keeps_dates() -> None:
    source = FakeSerial(feed(SAMPLE))
    sink = io.StringIO()
    proxy = make_proxy(source, auto=False)
    proxy.pump(sink)
    assert sink.getvalue().strip() == SAMPLE


def test_pump_with_forced_offset() -> None:
    source = FakeSerial(feed(MODERN))
    sink = io.StringIO()
    proxy = make_proxy(source, offset_days=7168)
    proxy.pump(sink)
    out = sink.getvalue().strip()
    assert out != MODERN
    assert is_valid(out)


def test_pump_max_lines_and_stop_event() -> None:
    source = FakeSerial(feed(*([SAMPLE] * 10)))
    sink = io.StringIO()
    proxy = make_proxy(source)
    assert proxy.pump(sink, max_lines=3) == 3
    assert len(sink.getvalue().splitlines()) == 3

    stop = threading.Event()
    stop.set()
    source2 = FakeSerial(feed(*([SAMPLE] * 10)))
    sink2 = io.StringIO()
    proxy2 = make_proxy(source2)
    assert proxy2.pump(sink2, stop_event=stop) == 0
    assert sink2.getvalue() == ""


def test_pump_proxy_helper() -> None:
    sink = io.StringIO()
    count = pump_proxy(FakeSerial(feed(SAMPLE)), sink)
    assert count == 1
    assert "030526" in sink.getvalue()


def test_pump_handles_str_reads() -> None:
    sink = io.StringIO()
    proxy = make_proxy(StrSerial("======Setting_ProcessKeys========\r\n" + SAMPLE + "\r\n"))
    assert proxy.pump(sink) == 2
    assert "030526" in sink.getvalue()


def test_pump_handles_bytes_sink() -> None:
    sink = io.BytesIO()
    proxy = make_proxy(FakeSerial(feed(SAMPLE)))
    proxy.pump(sink)
    assert b"030526" in sink.getvalue()


def test_rewrite_lines_helper() -> None:
    out = rewrite_lines(["debug", SAMPLE, MODERN])
    assert out[0] == "debug"
    assert "030526" in out[1]
    assert out[2] == MODERN


# ---------------------------------------------------------------------------
# Stdout mode
# ---------------------------------------------------------------------------
def test_serve_stdout_with_explicit_stream() -> None:
    source = FakeSerial(feed(DEBUG, SAMPLE))
    sink = io.StringIO()
    proxy = NmeaProxy(source)
    proxy.serve_stdout(out=sink)
    out = sink.getvalue().splitlines()
    assert out[0].strip() == DEBUG.strip()
    assert "030526" in out[1]


def test_serve_stdout_honours_stop_event() -> None:
    source = FakeSerial(feed(*([SAMPLE] * 5)))
    sink = io.StringIO()
    proxy = NmeaProxy(source)
    stop = threading.Event()
    stop.set()
    proxy.serve_stdout(stop, out=sink)
    assert sink.getvalue() == ""


# ---------------------------------------------------------------------------
# Serial object handling
# ---------------------------------------------------------------------------
def test_accepts_duck_typed_serial() -> None:
    fake = FakeSerial(feed(SAMPLE))
    assert isinstance(fake, SerialLike)
    proxy = NmeaProxy(fake)
    sink = io.StringIO()
    proxy.pump(sink)
    assert proxy._serial is fake


def test_serial_factory_is_used_for_port_names() -> None:
    created: dict[str, object] = {}

    def factory(port: str, baud: int) -> FakeSerial:
        created["port"] = port
        created["baud"] = baud
        return FakeSerial(feed(SAMPLE))

    proxy = NmeaProxy("COM_TEST", baud=38400, serial_factory=factory)
    sink = io.StringIO()
    proxy.pump(sink)
    assert created == {"port": "COM_TEST", "baud": 38400}
    assert "030526" in sink.getvalue()


def test_close_closes_serial_and_sockets() -> None:
    fake = FakeSerial(feed(SAMPLE))
    proxy = NmeaProxy(fake)
    proxy.close()
    assert fake.closed is True
    proxy.close()  # idempotent


def test_requires_port() -> None:
    with pytest.raises(M241Error):
        NmeaProxy(None)  # type: ignore[arg-type]


def test_context_manager_closes() -> None:
    fake = FakeSerial(feed(SAMPLE))
    with NmeaProxy(fake) as proxy:
        assert proxy.stopped is False
    assert fake.closed is True
    assert proxy.stopped is True


# ---------------------------------------------------------------------------
# TCP mode (real loopback socket, ephemeral port)
# ---------------------------------------------------------------------------
def _read_until_eof(sock: socket.socket, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    chunks = []
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def test_serve_tcp_end_to_end() -> None:
    payload = feed(DEBUG, SAMPLE, MODERN, GGA)
    fake = FakeSerial(payload, chunk=17)
    proxy = NmeaProxy(fake)

    thread = proxy.serve_tcp_in_thread("127.0.0.1", 0)
    try:
        assert proxy.bound_port is not None
        assert proxy.bound_port > 0
        host, port = proxy.bound_address or ("127.0.0.1", 0)

        with socket.create_connection((host, port), timeout=5.0) as client:
            received = _read_until_eof(client)

        text = received.decode("ascii")
        lines = [line for line in text.splitlines() if line]
        assert len(lines) == 4
        assert lines[0].strip() == DEBUG.strip()
        assert "030526" in lines[1]
        assert is_valid(lines[1])
        assert lines[2] == MODERN
        assert lines[3] == GGA
    finally:
        proxy.stop()
        thread.join(timeout=5.0)

    assert not thread.is_alive()
    proxy.close()
    assert fake.closed is True


def test_serve_tcp_second_client_gets_eof() -> None:
    proxy = NmeaProxy(FakeSerial(feed(SAMPLE)))
    thread = proxy.serve_tcp_in_thread("127.0.0.1", 0)
    try:
        host, port = proxy.bound_address or ("127.0.0.1", 0)
        with socket.create_connection((host, port), timeout=5.0) as client:
            first = _read_until_eof(client)
        assert b"030526" in first

        with socket.create_connection((host, port), timeout=5.0) as client:
            second = _read_until_eof(client)
        assert second == b""
    finally:
        proxy.stop()
        thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_stop_interrupts_a_blocked_tcp_server() -> None:
    proxy = NmeaProxy(FakeSerial(b""))
    thread = proxy.serve_tcp_in_thread("127.0.0.1", 0)
    proxy.stop()
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_serve_tcp_bad_bind_raises() -> None:
    # An unresolvable/unusable host makes bind fail; the proxy must surface a
    # clean M241Error instead of leaking a socket exception.
    proxy = NmeaProxy(FakeSerial(b""))
    try:
        with pytest.raises(M241Error):
            proxy.serve_tcp("256.256.256.256", 0)
    finally:
        proxy.close()
    assert proxy.stopped is True
    assert proxy.bound_port is None


def test_serve_tcp_stops_when_external_event_set() -> None:
    proxy = NmeaProxy(FakeSerial(b""))
    stop = threading.Event()
    thread = threading.Thread(target=proxy.serve_tcp, args=("127.0.0.1", 0, stop), daemon=True)
    thread.start()
    try:
        assert proxy.ready_event.wait(5.0)
        stop.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    finally:
        proxy.stop()


def test_poll_interval_used_without_stop_on_eof() -> None:
    # stop_on_eof=False makes the iterator poll; the stop event must end it.
    proxy = NmeaProxy(FakeSerial(feed(SAMPLE)), stop_on_eof=False, poll_interval=0.01)
    lines: list[str] = []

    def consume() -> None:
        for line in proxy.iter_repaired_lines():
            lines.append(line)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    time.sleep(0.2)
    proxy.stop()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert len(lines) == 1
    assert "030526" in lines[0]
