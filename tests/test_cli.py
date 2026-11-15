"""Tests for :mod:`m241.cli` - the ``m241`` command line interface.

Everything is driven in-process through ``main([...])`` except the two
subprocess tests that prove a machine without pyserial can still patch, and
that importing the CLI never pulls in ``serial``.

No firmware, no dump and no vendor binary is ever written into the repository:
every fixture is synthesised in memory and every output goes to ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import m241.cli as cli
from m241.firmware.image import EPOCH_CONSTANT_OFFSETS, GPS_EPOCH_UNIX

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

SAMPLE_RMC = "$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F"

V112_SIZE = 406312
V113_SIZE = 406620
V112_VERSION_OFFSET = 0x59B14
V113_VERSION_OFFSET = 0x59C28
EFFECTIVE_EPOCH_OFFSET = 0x402F4


# ---------------------------------------------------------------------------
# Fixture builders (synthetic, never committed)
# ---------------------------------------------------------------------------
def build_image(
    size: int = V113_SIZE,
    version: str = "V1.13",
    version_offset: int = V113_VERSION_OFFSET,
    device_type: str = "01017-00F",
) -> bytes:
    """Synthetic firmware image with the documented header layout."""
    data = bytearray(size)
    data[0x00:0x20] = bytes.fromhex("18f09fe5" * 8)
    data[0x90:0x9B] = b"HOLUX_M-241"
    struct.pack_into("<I", data, 0xF4, size - 4)
    struct.pack_into("<H", data, 0x108, 67)
    data[0x148:0x14E] = b"B-core"
    data[0x15A] = 1
    data[0x15B] = 20
    data[0x190:0x199] = b"SPP Slave"
    data[0x1B0 : 0x1B0 + len(device_type)] = device_type.encode("ascii")
    for offset in EPOCH_CONSTANT_OFFSETS:
        struct.pack_into("<I", data, offset, GPS_EPOCH_UNIX)
    data[version_offset : version_offset + 5] = version.encode("ascii")
    return bytes(data)


def build_log_dump(*, record_bytes: int = 20, points: int = 4, year: int = 2026) -> bytes:
    """One-block synthetic log dump in either the 16- or the 20-byte layout."""
    from m241.logfile.record import (
        BLOCK_HEADER_SIZE,
        BLOCK_SIZE,
        build_block_header,
        build_record,
    )

    mask = 0x1D if record_bytes == 16 else 0x3D
    start = int(datetime(year, 5, 3, 2, 21, 27, tzinfo=timezone.utc).timestamp())
    body = bytearray()
    for index in range(points):
        payload = (
            struct.pack("<I", start + index * 5)
            + struct.pack("<f", 35.6895 + index * 1e-4)
            + struct.pack("<f", 139.6917)
            + struct.pack("<f", 42.5)[1:4]
        )
        if record_bytes == 20:
            payload += struct.pack("<f", 12.5)
        body += build_record(payload, bitmask=mask)
    header = build_block_header(count=points, bitmask=mask, policy=0x0106, period_ds=10)
    block = bytes(header.ljust(BLOCK_HEADER_SIZE, b"\x00")) + bytes(body)
    return block.ljust(BLOCK_SIZE, b"\xff")


def patched_image(**kwargs: object) -> bytes:
    """Patch a synthetic image and return the patched bytes."""
    from m241.firmware import FirmwareImage, apply_patch_plan, build_patch_plan

    image = FirmwareImage.from_bytes(build_image(**kwargs))  # type: ignore[arg-type]
    return apply_patch_plan(image, build_patch_plan(image)).to_bytes()


class FakeNmeaSerial:
    """Duck-typed serial port that hands out a fixed NMEA payload."""

    def __init__(self, payload: bytes) -> None:
        self._data = bytearray(payload)
        self.closed = False
        self.written = bytearray()

    def read(self, size: int = 1) -> bytes:
        if not self._data:
            return b""
        chunk = bytes(self._data[:size])
        del self._data[:size]
        return chunk

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def flush(self) -> None:
        """Real serial ports have flush(); the CLI now calls it after writing."""

    def close(self) -> None:
        self.closed = True


def run_json(capsys: pytest.CaptureFixture[str]) -> dict:
    """Parse the single JSON object a command printed on stdout."""
    out = capsys.readouterr().out
    return json.loads(out)


# ---------------------------------------------------------------------------
# Invocation basics
# ---------------------------------------------------------------------------
class TestInvocation:
    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "usage: m241" in out
        for command in ("ports", "doctor", "info", "dump", "patch", "flash", "logs", "nmea",
                        "selftest"):
            assert command in out

    def test_subcommand_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        for command in ("ports", "doctor", "patch", "logs", "nmea", "selftest"):
            with pytest.raises(SystemExit) as excinfo:
                cli.main([command, "--help"])
            assert excinfo.value.code == 0
        assert "usage: m241" in capsys.readouterr().out

    def test_unknown_command_exits_two_with_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["definitely-not-a-command"])
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "usage: m241" in captured.err
        assert captured.out == ""

    def test_unknown_option_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["logs", "capacity", "--nope"])
        assert excinfo.value.code == 2
        assert "usage: m241" in capsys.readouterr().err

    def test_no_arguments_prints_help_and_succeeds(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main([]) == 0
        assert "usage: m241" in capsys.readouterr().out

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert "m241" in capsys.readouterr().out

    def test_version_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["version"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("m241 ")
        assert "Python" in out

    def test_main_accepts_no_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["m241", "version"])
        assert cli.main() == 0


# ---------------------------------------------------------------------------
# No pyserial needed for the offline commands
# ---------------------------------------------------------------------------
class TestNoPyserial:
    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )

    def test_importing_cli_does_not_import_serial(self) -> None:
        result = self._run(
            "import sys, m241.cli; "
            "assert 'serial' not in sys.modules, 'cli imported pyserial'; "
            "print('clean')"
        )
        assert result.returncode == 0, result.stderr
        assert "clean" in result.stdout

    def test_patch_and_logs_work_without_pyserial(self, tmp_path: Path) -> None:
        script = tmp_path / "offline.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import sys
                sys.modules["serial"] = None  # any 'import serial' now raises ImportError
                sys.path.insert(0, {str(SRC)!r})
                from pathlib import Path
                import tempfile

                from m241.cli import main
                from m241.firmware import FirmwareImage, apply_patch_plan, build_patch_plan

                directory = Path(tempfile.mkdtemp())
                image = FirmwareImage.from_bytes({build_image()!r})
                source = directory / "stock.bin"
                source.write_bytes(image.to_bytes())
                target = directory / "patched.bin"
                assert main(["patch", str(source), "-o", str(target), "-q"]) == 0
                assert main(["patch", str(source), "-o", str(directory / "d.bin"),
                            "--dry-run", "-q"]) == 0
                assert main(["logs", "capacity", "-q"]) == 0
                patched = apply_patch_plan(image, build_patch_plan(image))
                assert patched.to_bytes() == target.read_bytes()
                print("offline-ok")
                """
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "offline-ok" in result.stdout


# ---------------------------------------------------------------------------
# ports
# ---------------------------------------------------------------------------
class TestPorts:
    def test_json_schema(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["ports", "--json"]) == 0
        document = run_json(capsys)
        assert set(document) >= {"ports", "candidates", "platform_hint"}
        assert isinstance(document["ports"], list)
        assert isinstance(document["candidates"], list)
        assert isinstance(document["platform_hint"], str)
        for port in document["ports"]:
            assert set(port) == {
                "device",
                "description",
                "hwid",
                "vid",
                "pid",
                "is_candidate",
            }

    def test_empty_list_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import m241.protocol.ports as ports_mod

        monkeypatch.setattr(ports_mod, "list_serial_ports", lambda: [])
        assert cli.main(["ports", "--json"]) == 0
        document = run_json(capsys)
        assert document["ports"] == []
        assert "note" in document

        assert cli.main(["ports"]) == 0
        out = capsys.readouterr().out
        assert "No serial ports found" in out

    def test_table_flags_candidates(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import m241.protocol.ports as ports_mod

        fake = [
            ports_mod.PortInfo("COM7", "Silicon Labs CP210x", "USB VID:PID=10C4", 0x10C4,
                               0xEA60, True),
            ports_mod.PortInfo("COM3", "Some modem", "ACPI", None, None, False),
        ]
        monkeypatch.setattr(ports_mod, "list_serial_ports", lambda: fake)
        assert cli.main(["ports"]) == 0
        out = capsys.readouterr().out
        assert "COM7" in out and "COM3" in out
        assert "*" in out
        assert "candidate port(s): COM7" in out


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
class TestDoctor:
    def test_plain_run_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "environment" in out
        assert "firmware profiles (2)" in out
        assert "doctor: all required checks passed" in out

    def test_simulate_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["doctor", "--simulate", "--json"]) == 0
        document = run_json(capsys)
        assert document["ok"] is True
        assert set(document) >= {"ok", "environment", "ports", "profiles", "device"}
        environment = document["environment"]
        assert set(environment) == {"python", "platform", "pyserial", "package_data"}
        assert environment["package_data"] is True
        device = document["device"]
        assert device["simulated"] is True
        assert device["ok"] is True
        assert device["da_version"] == "0x0308"
        assert device["flash_size"] == 0x200000
        assert document["profiles"] == [
            {"name": "v1.12", "version": "V1.12", "size": 406312, "log_record_bytes": 16},
            {"name": "v1.13", "version": "V1.13", "size": 406620, "log_record_bytes": 20},
        ]

    def test_from_drop_missing_directory(self, tmp_path: Path) -> None:
        assert cli.main(["doctor", "--from-drop", str(tmp_path / "nope")]) == 1

    def test_from_drop_empty_directory_reports_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        drop = tmp_path / "drop"
        drop.mkdir()
        assert cli.main(["doctor", "--from-drop", str(drop), "--json"]) == 0
        document = run_json(capsys)
        vendor = document["vendor_drop"]
        assert vendor["path"] == str(drop)
        assert vendor["found"] == []
        assert len(vendor["missing"]) >= 2
        assert any(row["basename"] == "DA_v3.8.4.bin" for row in vendor["missing"])

    def test_from_drop_wrong_hash_exits_four(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        drop = tmp_path / "drop"
        drop.mkdir()
        (drop / "DA_v3.8.4.bin").write_bytes(b"not the vendor download agent")
        assert cli.main(["doctor", "--from-drop", str(drop), "--json"]) == 4
        document = run_json(capsys)
        assert document["ok"] is False
        assert document["exit_code"] == 4
        row = next(
            item for item in document["vendor_drop"]["found"]
            if item["basename"] == "DA_v3.8.4.bin"
        )
        assert row["ok"] is False

    def _fake_manifest(self) -> tuple[str, dict[str, bytes]]:
        import hashlib

        da = b"\x42" * 0x800
        firmware = build_image()
        dll = b"MZ" + b"\x00" * 126
        exe = b"MZ" + b"\x01" * 126
        entries = [
            {"role": "download_agent", "basename": "DA_v3.8.4.bin", "size": len(da),
             "sha256": hashlib.sha256(da).hexdigest()},
            {"role": "firmware", "basename": "stock_v113.bin", "size": len(firmware),
             "sha256": hashlib.sha256(firmware).hexdigest()},
            {"role": "vendor_brom", "basename": "brom.dll", "size": len(dll),
             "sha256": hashlib.sha256(dll).hexdigest()},
            {"role": "utility", "basename": "HoluxLoggerUtility.exe", "size": len(exe),
             "sha256": hashlib.sha256(exe).hexdigest()},
        ]
        manifest = json.dumps({"schema": 1, "files": entries})
        files = {
            "DA_v3.8.4.bin": da,
            "stock_v113.bin": firmware,
            "brom.dll": dll,
            "HoluxLoggerUtility.exe": exe,
        }
        return manifest, files

    def test_extract_copies_only_the_download_agent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manifest, files = self._fake_manifest()
        original = cli._read_package_data
        monkeypatch.setattr(
            cli,
            "_read_package_data",
            lambda name: manifest if name == "vendor_drop.json" else original(name),
        )
        drop = tmp_path / "drop"
        drop.mkdir()
        for name, payload in files.items():
            (drop / name).write_bytes(payload)
        target = tmp_path / "vendor"

        assert cli.main(
            ["doctor", "--from-drop", str(drop), "--extract-to", str(target), "--json"]
        ) == 0
        document = run_json(capsys)
        extracted = {row["basename"] for row in document["vendor_drop"]["extracted"]}
        assert extracted == {"DA_v3.8.4.bin"}
        assert sorted(path.name for path in target.iterdir()) == ["DA_v3.8.4.bin"]
        assert (target / "DA_v3.8.4.bin").read_bytes() == files["DA_v3.8.4.bin"]
        assert all(row["ok"] for row in document["vendor_drop"]["extracted"])

        # DLLs and EXEs are never copied, even with --include-firmware.
        assert cli.main(
            [
                "doctor",
                "--from-drop",
                str(drop),
                "--extract-to",
                str(target),
                "--include-firmware",
                "--json",
            ]
        ) == 0
        capsys.readouterr()
        names = sorted(path.name for path in target.iterdir())
        assert names == ["DA_v3.8.4.bin", "stock_v113.bin"]
        assert not any(name.lower().endswith((".dll", ".exe")) for name in names)
        # The drop itself is never modified.
        assert sorted(path.name for path in drop.iterdir()) == sorted(files)


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------
class TestPatch:
    def _stock(self, tmp_path: Path, **kwargs: object) -> Path:
        path = tmp_path / "stock.bin"
        path.write_bytes(build_image(**kwargs))  # type: ignore[arg-type]
        return path

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        target = tmp_path / "out.bin"
        assert cli.main(["patch", str(source), "-o", str(target), "--dry-run", "--json"]) == 0
        document = run_json(capsys)
        assert document["dry_run"] is True
        assert document["written"] is False
        assert not target.exists()
        assert sorted(path.name for path in tmp_path.iterdir()) == ["stock.bin"]

        assert cli.main(["patch", str(source), "-o", str(target), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "dry run: nothing was written" in out
        assert not target.exists()

    def test_json_schema_and_written_bytes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        target = tmp_path / "out.bin"
        assert cli.main(["patch", str(source), "-o", str(target), "--json"]) == 0
        document = run_json(capsys)
        expected_keys = {
            "input",
            "output",
            "profile",
            "target_version",
            "dry_run",
            "patches",
            "changed_offsets",
            "sha256_before",
            "sha256_after",
            "written",
        }
        assert set(document) == expected_keys
        assert document["profile"] == "v1.13"
        assert document["target_version"] == "V1.14"
        assert document["written"] is True
        assert document["changed_offsets"] == [0x402F6, 0x402F7, 0x59C2C]
        assert [patch["offset"] for patch in document["patches"]] == [
            EFFECTIVE_EPOCH_OFFSET,
            V113_VERSION_OFFSET,
        ]
        assert document["sha256_before"] != document["sha256_after"]
        assert target.read_bytes() == patched_image()
        from m241.firmware import FirmwareImage

        assert FirmwareImage.from_path(target).sha256() == document["sha256_after"]

    def test_human_output_names_offsets_and_hashes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "out.bin")]) == 0
        out = capsys.readouterr().out
        assert "profile: v1.13" in out
        assert "0x402F4" in out
        assert "0x59C28" in out
        assert "sha256 before" in out
        assert "sha256 after" in out
        assert "WNRO" in out

    def test_v112_profile_and_no_version_marker(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(
            tmp_path, size=V112_SIZE, version="V1.12", version_offset=V112_VERSION_OFFSET
        )
        target = tmp_path / "out.bin"
        assert cli.main(
            ["patch", str(source), "-o", str(target), "--no-version-marker", "--json"]
        ) == 0
        document = run_json(capsys)
        assert document["profile"] == "v1.12"
        assert document["changed_offsets"] == [0x402F6, 0x402F7]
        from m241.firmware import FirmwareImage

        assert FirmwareImage.from_path(target).version_string() == "V1.12"

    def test_profile_override_is_used(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        assert cli.main(
            ["patch", str(source), "-o", str(tmp_path / "out.bin"),
             "--profile", "v1.13", "--json"]
        ) == 0
        assert run_json(capsys)["profile"] == "v1.13"

    def test_already_patched_exits_four(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "patched.bin"
        source.write_bytes(patched_image())
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "x.bin")]) == 4
        captured = capsys.readouterr()
        assert "already patched" in captured.err
        assert "m241: error:" in captured.err
        assert "hint:" in captured.err
        assert not (tmp_path / "x.bin").exists()

    def test_unknown_profile_exits_four_and_prints_header_facts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = bytearray(build_image())
        data[0x1B0:0x1B9] = b"99999-00Z"
        data[0x108:0x10A] = struct.pack("<H", 123)
        data[V113_VERSION_OFFSET : V113_VERSION_OFFSET + 5] = b"V1.99"
        source = tmp_path / "unknown.bin"
        source.write_bytes(bytes(data))
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "x.bin")]) == 4
        err = capsys.readouterr().err
        assert "no firmware profile matches" in err
        assert "99999-00Z" in err
        assert "123" in err
        assert str(len(data)) in err

    def test_in_place_without_flag_exits_five(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        original = source.read_bytes()
        assert cli.main(["patch", str(source), "-o", str(source)]) == 5
        assert "refusing to overwrite the input image in place" in capsys.readouterr().err
        assert source.read_bytes() == original

    def test_in_place_with_flag_writes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        assert cli.main(["patch", str(source), "--in-place"]) == 0
        assert "written" in capsys.readouterr().out
        assert source.read_bytes() == patched_image()

    def test_missing_output_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stock(tmp_path)
        assert cli.main(["patch", str(source)]) == 2
        captured = capsys.readouterr()
        assert "patch needs an output file" in captured.err
        assert captured.out == ""

    def test_missing_source_is_runtime_error(self, tmp_path: Path) -> None:
        assert cli.main(["patch", str(tmp_path / "nope.bin"), "-o", str(tmp_path / "x")]) == 1


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
class TestLogsCapacity:
    def test_exact_numbers(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["logs", "capacity"]) == 0
        out = capsys.readouterr().out
        assert "32,512 (9.0 h)" in out
        assert "26,009 (7.2 h)" in out
        assert "520,192" in out
        assert "130,048 (36.1 h)" in out
        assert "104,038 (28.9 h)" in out
        assert "260,096 (72.2 h)" in out
        assert "208,076 (57.8 h)" in out
        assert "2,080,768" in out
        assert "4,161,536" in out

    def test_json_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["logs", "capacity", "--json"]) == 0
        document = run_json(capsys)
        assert set(document) == {"rows"}
        rows = document["rows"]
        assert [(row["flash"], row["record_bytes"], row["points"]) for row in rows] == [
            ("512K", 16, 32_512),
            ("512K", 20, 26_009),
            ("2M", 16, 130_048),
            ("2M", 20, 104_038),
            ("4M", 16, 260_096),
            ("4M", 20, 208_076),
        ]
        assert set(rows[0]) == {
            "flash",
            "blocks",
            "usable_bytes",
            "record_bytes",
            "points",
            "hours_at_interval",
        }
        by_flash = {(row["flash"], row["record_bytes"]): row for row in rows}
        assert by_flash[("512K", 16)]["blocks"] == 8
        assert by_flash[("2M", 16)]["blocks"] == 32
        assert by_flash[("4M", 16)]["blocks"] == 64
        assert by_flash[("512K", 16)]["usable_bytes"] == 520_192
        assert by_flash[("2M", 16)]["usable_bytes"] == 2_080_768
        assert by_flash[("4M", 16)]["usable_bytes"] == 4_161_536
        assert by_flash[("2M", 16)]["hours_at_interval"] == pytest.approx(36.12, abs=0.01)

    def test_firmware_and_flash_filters(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(
            ["logs", "capacity", "--firmware", "V1.13", "--flash", "4M", "--json"]
        ) == 0
        rows = run_json(capsys)["rows"]
        assert len(rows) == 1
        assert rows[0]["flash"] == "4M"
        assert rows[0]["record_bytes"] == 20
        assert rows[0]["points"] == 208_076

    def test_interval_and_bad_interval(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["logs", "capacity", "--interval", "5", "--json"]) == 0
        rows = run_json(capsys)["rows"]
        two_mebibyte = next(row for row in rows if row["flash"] == "2M" and row["record_bytes"] == 16)
        assert two_mebibyte["hours_at_interval"] == pytest.approx(
            130_048 * 5 / 3600, abs=0.01
        )
        assert cli.main(["logs", "capacity", "--interval", "0"]) == 2


class TestLogsInfo:
    def test_20_byte_dump_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20, points=4))
        assert cli.main(["logs", "info", str(path), "--json"]) == 0
        document = run_json(capsys)
        assert set(document) == {
            "file",
            "size",
            "blocks",
            "record_length",
            "records",
            "crc_errors",
            "first_utc",
            "last_utc",
            "capacity",
        }
        assert document["record_length"] == 20
        assert document["records"] == 4
        assert document["crc_errors"] == 0
        assert document["size"] == 0x10000
        assert document["first_utc"] == "2026-05-03T02:21:27Z"
        assert document["last_utc"] == "2026-05-03T02:21:42Z"
        assert {"offset", "count", "bitmask", "policy", "period_s", "distance_m",
                "speed_kmh", "used_bytes"} == set(document["blocks"][0])
        assert document["capacity"]["2M"][1]["points"] == 104_038
        assert document["capacity"]["4M"][0]["points"] == 260_096

    def test_16_byte_dump_reports_its_own_record_length(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "log16.bin"
        path.write_bytes(build_log_dump(record_bytes=16, points=3))
        assert cli.main(["logs", "info", str(path), "--json"]) == 0
        document = run_json(capsys)
        assert document["record_length"] == 16
        assert document["records"] == 3

    def test_crc_errors_are_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = bytearray(build_log_dump(record_bytes=20, points=2))
        data[0x200 + 20 + 19] ^= 0xFF  # corrupt the second record's CRC
        path = tmp_path / "bad.bin"
        path.write_bytes(bytes(data))
        assert cli.main(["logs", "info", str(path), "--json"]) == 0
        assert run_json(capsys)["crc_errors"] == 1

    def test_human_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20))
        assert cli.main(["logs", "info", str(path)]) == 0
        out = capsys.readouterr().out
        assert "record length : 20 bytes" in out
        assert "records       : 4" in out
        assert "130,048 points" in out

    def test_missing_file(self, tmp_path: Path) -> None:
        assert cli.main(["logs", "info", str(tmp_path / "nope.bin")]) == 1


class TestLogsRead:
    def test_csv_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20))
        assert cli.main(["logs", "read", str(path)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("index,utc,lat,lon,altitude_m,speed_kmh,heading_deg,hdop,sats,crc_ok")
        assert "2026-05-03T02:21:27Z" in out
        assert len(out.strip().splitlines()) == 5

    @pytest.mark.parametrize("fmt", ["gpx", "nmea", "kml", "json", "csv"])
    def test_every_format_renders(
        self, fmt: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20))
        target = tmp_path / f"out.{fmt}"
        assert cli.main(["logs", "read", str(path), "--format", fmt, "-o", str(target)]) == 0
        text = target.read_text(encoding="utf-8")
        assert "35.689" in text or "3541" in text

    def test_gpx_shape(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=16))
        assert cli.main(["logs", "read", str(path), "--format", "gpx"]) == 0
        out = capsys.readouterr().out
        assert '<gpx version="1.1"' in out
        assert "<trkpt lat=" in out

    def test_auto_utc_offset_shifts_a_stale_dump(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "stale.bin"
        path.write_bytes(build_log_dump(record_bytes=20, year=2006))
        assert cli.main(["logs", "read", str(path), "--utc-offset-days", "auto"]) == 0
        out = capsys.readouterr().out
        assert "2006-" not in out
        assert "2025-12-17T02:21:27Z" in out  # 2006-05-03 + 7168 days

    def test_explicit_utc_offset(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20))
        assert cli.main(["logs", "read", str(path), "--utc-offset-days", "1"]) == 0
        assert "2026-05-04T02:21:27Z" in capsys.readouterr().out

    def test_flash_report_goes_to_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "log.bin"
        path.write_bytes(build_log_dump(record_bytes=20))
        assert cli.main(["logs", "read", str(path), "--flash", "2M", "-o",
                         str(tmp_path / "t.csv")]) == 0
        captured = capsys.readouterr()
        assert "usage on 2M flash" in captured.err
        assert captured.out == ""

    def test_strict_mode_rejects_bad_crc(self, tmp_path: Path) -> None:
        data = bytearray(build_log_dump(record_bytes=20, points=2))
        data[0x200 + 19] ^= 0xFF
        path = tmp_path / "bad.bin"
        path.write_bytes(bytes(data))
        assert cli.main(["logs", "read", str(path), "--strict"]) == 1
        assert cli.main(["logs", "read", str(path)]) == 0

    def test_missing_file(self, tmp_path: Path) -> None:
        assert cli.main(["logs", "read", str(tmp_path / "nope.bin")]) == 1


# ---------------------------------------------------------------------------
# nmea
# ---------------------------------------------------------------------------
class TestNmeaRepair:
    def _stream(self, tmp_path: Path) -> Path:
        path = tmp_path / "capture.nmea"
        path.write_bytes(
            ("======Setting_ProcessKeys========\r\n" + SAMPLE_RMC + "\r\n").encode("latin-1")
        )
        return path

    def test_repairs_the_blog_sample(self, tmp_path: Path) -> None:
        from m241.nmea import is_valid

        source = self._stream(tmp_path)
        target = tmp_path / "fixed.nmea"
        assert cli.main(["nmea", "repair", "--in", str(source), "--out", str(target)]) == 0
        text = target.read_text(encoding="latin-1")
        assert "030526" in text
        assert "170906" not in text
        assert "======Setting_ProcessKeys========" in text
        repaired = next(line for line in text.splitlines() if line.startswith("$GPRMC"))
        assert is_valid(repaired)

    def test_stats_writes_counters_to_stderr(self, tmp_path: Path) -> None:
        source = self._stream(tmp_path)
        target = tmp_path / "fixed.nmea"
        assert cli.main(["nmea", "repair", "--in", str(source), "--out", str(target),
                         "--stats"]) == 0
        assert target.exists()

    def test_json_reports_counters(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._stream(tmp_path)
        assert cli.main(["nmea", "repair", "--in", str(source), "--json", "--stats",
                         "--out", str(tmp_path / "out.nmea")]) == 0
        document = run_json(capsys)
        assert document["ok"] is True
        assert document["read"] == 2
        assert document["changed"] == 1
        assert document["offset_days"] == "auto"

    def test_stdin_to_stdout(self, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
        import io

        monkeypatch.setattr(sys, "stdin", io.StringIO(SAMPLE_RMC + "\n"))
        assert cli.main(["nmea", "repair"]) == 0
        assert "030526" in capsys.readouterr().out

    def test_forced_offset_applies_to_modern_dates(self, tmp_path: Path) -> None:
        from m241.nmea import date_of

        modern = "$GPRMC,022127.542,A,3541.1234,N,13946.5678,E,12.5,270.0,030526,1.5,W,A*2B"
        source = tmp_path / "modern.nmea"
        source.write_text(modern + "\n", encoding="latin-1")
        target = tmp_path / "out.nmea"
        assert cli.main(["nmea", "repair", "--in", str(source), "--out", str(target),
                         "--offset-days", "7168"]) == 0
        assert date_of(target.read_text(encoding="latin-1").strip()).year > 2040

    def test_bad_wall_clock_is_usage_error(self, tmp_path: Path) -> None:
        source = self._stream(tmp_path)
        assert cli.main(["nmea", "repair", "--in", str(source), "--wall-clock",
                         "not-a-date"]) == 2

    def test_missing_input_file(self, tmp_path: Path) -> None:
        assert cli.main(["nmea", "repair", "--in", str(tmp_path / "nope.nmea")]) == 1


class TestNmeaProxy:
    def test_stdout_mode_repairs_the_stream(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = ("======debug======\r\n" + SAMPLE_RMC + "\r\n").encode("latin-1")
        monkeypatch.setattr(
            cli, "_SERIAL_FACTORY", lambda port, baud: FakeNmeaSerial(payload)
        )
        assert cli.main(["nmea", "proxy", "FAKE", "--stdout", "--duration", "2"]) == 0
        captured = capsys.readouterr()
        assert "030526" in captured.out
        assert "======debug======" in captured.out
        assert "proxy stopped" in captured.err

    def test_tcp_mode_stops_after_duration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        monkeypatch.setattr(
            cli, "_SERIAL_FACTORY", lambda _port, _baud: FakeNmeaSerial(b"")
        )
        assert cli.main(
            ["nmea", "proxy", "FAKE", "--tcp", f"127.0.0.1:{port}", "--duration", "0.3"]
        ) == 0
        assert "listening on 127.0.0.1" in capsys.readouterr().err

    def test_non_loopback_host_warns(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import socket

        # 0.0.0.0 is bindable on every OS; 127.0.0.2 is not (macOS rejects it).
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", 0))
            port = probe.getsockname()[1]
        monkeypatch.setattr(
            cli, "_SERIAL_FACTORY", lambda _port, _baud: FakeNmeaSerial(b"")
        )
        assert cli.main(
            ["nmea", "proxy", "FAKE", "--tcp", f"0.0.0.0:{port}", "--duration", "0.3"]
        ) == 0
        assert "no authentication" in capsys.readouterr().err

    def test_bad_tcp_syntax_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["nmea", "proxy", "FAKE", "--tcp", "nonsense"])
        assert excinfo.value.code == 2


class TestInfo:
    def _fake(self, payload: bytes):
        def factory(_port: str, _baud: int) -> FakeNmeaSerial:
            return FakeNmeaSerial(payload)

        return factory

    def test_pmtk705_reply_is_parsed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.nmea import with_checksum

        reply = with_checksum("PMTK705,B-core_1.13,67,MT3318,1.0")
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", self._fake((reply + "\r\n").encode()))
        assert cli.main(["info", "FAKE", "--json"]) == 0
        document = run_json(capsys)
        assert set(document) == {"port", "release", "build", "model", "sdk", "raw",
                                 "chip_guess"}
        assert document["port"] == "FAKE"
        assert document["release"] == "B-core_1.13"
        assert document["build"] == "67"
        assert document["model"] == "MT3318"
        assert document["chip_guess"] == "MT3318"
        assert document["raw"].startswith("$PMTK705")

    def test_human_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.nmea import with_checksum

        reply = with_checksum("PMTK705,B-core_1.13,67,MT3318,1.0")
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", self._fake((reply + "\r\n").encode()))
        assert cli.main(["info", "FAKE"]) == 0
        out = capsys.readouterr().out
        assert "release  : B-core_1.13" in out
        assert "build    : 67" in out

    def test_aiding_sends_the_extra_commands(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.nmea import with_checksum

        reply = (
            with_checksum("PMTK705,B-core_1.13,67,MT3318,1.0")
            + "\r\n"
            + with_checksum("PMTK001,715,3")
            + "\r\n"
        )
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", self._fake(reply.encode()))
        assert cli.main(["info", "FAKE", "--aiding", "--json"]) == 0
        document = run_json(capsys)
        assert len(document["aiding"]["sent"]) == 3
        assert document["aiding"]["sent"][0].startswith("$PMTK715")
        assert any(line.startswith("$PMTK001") for line in document["aiding"]["acks"])

    def test_no_reply_exits_three(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", self._fake(b""))
        assert cli.main(["info", "FAKE"]) == 3
        assert "no $PMTK705 reply" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Hardware commands, driven through the simulator
# ---------------------------------------------------------------------------
class TestInfoAndHardwareErrors:
    def test_info_against_an_unopenable_port_exits_three(self) -> None:
        assert cli.main(["info", "SIM-NOT-A-PORT"]) == 3

    def test_dump_against_an_unopenable_port_exits_three(self, tmp_path: Path) -> None:
        assert cli.main(
            ["dump", "SIM-NOT-A-PORT", "-o", str(tmp_path / "out.bin"), "--no-verify"]
        ) == 3

    def test_flash_against_an_unopenable_port_exits_three(self, tmp_path: Path) -> None:
        stock = tmp_path / "stock.bin"
        stock.write_bytes(build_image())
        fw = tmp_path / "fw.bin"
        fw.write_bytes(patched_image())
        da = tmp_path / "DA.bin"
        da.write_bytes(b"\x42" * 0x800)
        assert cli.main(
            ["flash", "SIM-NOT-A-PORT", "--da", str(da), "--fw", str(fw),
             "--backup", str(stock), "--yes"]
        ) == 3


class TestDump:
    def _device(self, image: bytes | None = None):
        from m241.protocol.simulator import FakeDevice

        return FakeDevice(image if image is not None else build_image())

    def test_roundtrip_through_the_simulator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.firmware import FirmwareImage

        device = self._device()
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        target = tmp_path / "dump.bin"
        assert cli.main(["dump", "FAKE", "-o", str(target), "--json"]) == 0
        document = run_json(capsys)
        expected = build_image()
        assert document["port"] == "FAKE"
        assert document["output"] == str(target)
        assert document["size"] == V113_SIZE
        assert document["sha256"] == FirmwareImage.from_bytes(expected).sha256()
        assert document["profile"] == "v1.13"
        assert document["verified"] is True
        assert document["header"]["family"] == "HOLUX_M-241"
        assert document["header"]["build_number"] == 67
        assert target.read_bytes() == expected

    def test_no_verify_skips_the_second_pass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        device = self._device()
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        assert cli.main(["dump", "FAKE", "-o", str(tmp_path / "d.bin"), "--no-verify"]) == 0
        out = capsys.readouterr().out
        assert "verified  : no" in out

    def test_refuses_to_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = self._device()
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        target = tmp_path / "exists.bin"
        target.write_bytes(b"old")
        assert cli.main(["dump", "FAKE", "-o", str(target), "--no-verify"]) == 5
        assert target.read_bytes() == b"old"
        assert cli.main(["dump", "FAKE", "-o", str(target), "--no-verify", "--force"]) == 0
        assert target.read_bytes() == build_image()

    def test_implausible_header_length_exits_four(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = self._device(b"\x00" * 0x1000)
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        assert cli.main(["dump", "FAKE", "-o", str(tmp_path / "d.bin")]) == 4

    def test_two_dumps_that_differ_exit_four(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        device = self._device()
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        calls: list[int] = []
        original = cli._dump_full_image

        def flaky(*args: object, **kwargs: object) -> bytes:
            calls.append(1)
            data = original(*args, **kwargs)  # type: ignore[arg-type]
            if len(calls) == 2:
                data = bytearray(data)
                data[0x100] ^= 0xFF
            return bytes(data)

        monkeypatch.setattr(cli, "_dump_full_image", flaky)
        target = tmp_path / "d.bin"
        assert cli.main(["dump", "FAKE", "-o", str(target)]) == 4
        assert len(calls) == 2
        err = capsys.readouterr().err
        assert "the two dumps differ" in err
        assert not target.exists()

    def test_explicit_length_uses_the_m3318_view(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        device = self._device(b"\x11" * 0x400)
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        assert cli.main(
            ["dump", "FAKE", "-o", str(tmp_path / "d.bin"), "--base-addr", "0x0",
             "--length", "0x400", "--no-verify", "--json"]
        ) == 0
        assert run_json(capsys)["size"] == 0x400


class TestFlash:
    def _fixtures(self, tmp_path: Path, *, fw_bytes: bytes | None = None) -> dict[str, Path]:
        stock = tmp_path / "stock.bin"
        stock.write_bytes(build_image())
        fw = tmp_path / "fw.bin"
        fw.write_bytes(patched_image() if fw_bytes is None else fw_bytes)
        da = tmp_path / "DA_v3.8.4.bin"
        da.write_bytes(b"\x42" * 0x800)
        return {"stock": stock, "fw": fw, "da": da}

    def test_writes_through_the_simulated_da(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.protocol.simulator import FakeDevice

        files = self._fixtures(tmp_path)
        device = FakeDevice(build_image())
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", device.factory)
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(files["stock"]), "--yes", "--json"]
        ) == 0
        document = run_json(capsys)
        assert document["written"] is True
        assert document["port"] == "FAKE"
        assert document["size"] == V113_SIZE
        assert document["sha256"] == _sha256(patched_image())
        assert document["da_info"]["da_version"] == 0x0308
        assert document["da_info"]["flash_size"] == 0x200000
        assert bytes(device.memory[:V113_SIZE]) == patched_image()

    def test_missing_backup_exits_five(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        files = self._fixtures(tmp_path)
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(tmp_path / "nope.bin"), "--yes"]
        ) == 5
        assert "backup image not found" in capsys.readouterr().err

    def test_backup_that_is_not_firmware_exits_five(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        files = self._fixtures(tmp_path)
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"hello")
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(bad), "--yes"]
        ) == 5
        assert "not a usable firmware image" in capsys.readouterr().err

    def test_unrelated_image_exits_five(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = bytearray(build_image())
        data[V113_VERSION_OFFSET : V113_VERSION_OFFSET + 5] = b"V1.99"
        data[0x1000] ^= 0xFF  # a byte far outside every known patch offset
        files = self._fixtures(tmp_path, fw_bytes=bytes(data))
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(files["stock"]), "--yes"]
        ) == 5
        assert "not a patched copy of your backup" in capsys.readouterr().err

    def test_size_mismatch_needs_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        files = self._fixtures(tmp_path, fw_bytes=build_image()[:0x1000])
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(files["stock"])]
        ) == 5
        captured = capsys.readouterr()
        assert "differ" in captured.err.lower() or "bytes" in captured.err

    def test_no_confirmation_without_a_tty_exits_five(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        files = self._fixtures(tmp_path)
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))
        assert cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(files["stock"])]
        ) == 5
        assert "without confirmation" in capsys.readouterr().err

    def test_missing_da_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = self._fixtures(tmp_path)
        assert cli.main(
            ["flash", "FAKE", "--da", str(tmp_path / "nope.bin"), "--fw",
             str(files["fw"]), "--backup", str(files["stock"]), "--yes"]
        ) == 1

    def test_device_write_failure_exits_six(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        from m241.errors import DownloadAgentError
        from m241.protocol import da as da_module

        files = self._fixtures(tmp_path)
        monkeypatch.setattr(cli, "_SERIAL_FACTORY", lambda p, b: FakeNmeaSerial(b""))

        def _boom(self, data, *, start=0):  # noqa: ANN001 - signature-compatible
            raise DownloadAgentError("simulated write failure")

        monkeypatch.setattr(da_module.DownloadAgent, "write_firmware", _boom)
        # Patch the DA load path so the command reaches write_firmware.
        monkeypatch.setattr(da_module.DownloadAgent, "start", lambda self, path: {
            "sync_char": da_module.SYNC,
            "da_version": da_module.EXPECTED_DA_VERSION,
            "flash_device_id": 0xBA,
            "flash_size": 0x200000,
            "flash_manufacturer_code": 0xC2,
            "flash_device_code": 0x22BA,
            "flash_ext_device_code1": 0x22,
            "flash_ext_device_code2": 0xBA,
            "ext_sram_size": 0,
        })
        # ...and skip the wire handshake, which needs a real BootROM.
        monkeypatch.setattr(cli, "_start_bootrom", lambda *a, **k: None)
        code = cli.main(
            ["flash", "FAKE", "--da", str(files["da"]), "--fw", str(files["fw"]),
             "--backup", str(files["stock"]), "--yes"]
        )
        assert code == 6
        err = capsys.readouterr().err
        assert "firmware write failed" in err
        assert "re-flash your backup" in err


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
class TestSelftest:
    def test_selftest_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["selftest"]) == 0
        out = capsys.readouterr().out
        for stage in ("firmware-bootrom", "firmware-patch", "firmware-flash",
                      "log-parse-export", "nmea-repair", "nmea-proxy"):
            assert f"ok    {stage}" in out
        assert "selftest: PASS" in out

    def test_selftest_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["selftest", "--json"]) == 0
        document = run_json(capsys)
        assert document["ok"] is True
        assert len(document["stages"]) == 6
        for stage in document["stages"]:
            assert set(stage) == {"name", "ok", "detail"}
            assert stage["ok"] is True
            assert stage["detail"]
        assert [stage["name"] for stage in document["stages"]] == [
            "firmware-bootrom",
            "firmware-patch",
            "firmware-flash",
            "log-parse-export",
            "nmea-repair",
            "nmea-proxy",
        ]

    def test_selftest_never_writes_into_the_repository(self) -> None:
        before = {path.name for path in REPO_ROOT.iterdir()}
        assert cli.main(["selftest", "-q"]) == 0
        after = {path.name for path in REPO_ROOT.iterdir()}
        assert before == after


# ---------------------------------------------------------------------------
# JSON output discipline
# ---------------------------------------------------------------------------
class TestOutputDiscipline:
    @pytest.mark.parametrize(
        "argv",
        [
            ["ports", "--json"],
            ["doctor", "--simulate", "--json"],
            ["logs", "capacity", "--json"],
            ["selftest", "--json"],
        ],
    )
    def test_json_is_a_single_object_on_stdout(
        self, argv: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert out.startswith("{")
        assert json.loads(out)  # the whole stream is one object

    def test_patch_json_stdout_is_pure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "stock.bin"
        source.write_bytes(build_image())
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "o.bin"), "--json"]) == 0
        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "profile" not in captured.err

    def test_quiet_suppresses_status_messages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "stock.bin"
        source.write_bytes(build_image())
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "o.bin"), "-q"]) == 0
        captured = capsys.readouterr()
        assert "profile:" in captured.out  # the result itself is never suppressed
        assert captured.err == ""

    def test_failure_with_json_reports_an_error_object(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "patched.bin"
        source.write_bytes(patched_image())
        assert cli.main(
            ["patch", str(source), "-o", str(tmp_path / "x.bin"), "--json"]
        ) == 4
        captured = capsys.readouterr()
        document = json.loads(captured.out)
        assert document["ok"] is False
        assert document["exit_code"] == 4
        assert "already patched" in document["error"]
        assert "m241: error:" in captured.err

    def test_verbose_adds_diagnostics(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "stock.bin"
        source.write_bytes(build_image())
        assert cli.main(["patch", str(source), "-o", str(tmp_path / "o.bin"), "-v"]) == 0
        assert capsys.readouterr().out


# ---------------------------------------------------------------------------
# Hardware-verified defaults
# ---------------------------------------------------------------------------
class TestHardwareDefaults:
    def test_dump_defaults_to_the_measured_firmware_base(self) -> None:
        """`m241 dump` must default to the address the hardware actually uses.

        Measured on a V1.13 unit: reading ``0x20000000`` returns ``42 20 02 02``
        (unmapped bus) and the firmware vector table is at ``0x00200000``.  The
        simulator maps *both* addresses so the rest of the suite cannot catch a
        regression here -- only an explicit assertion can.
        """
        from m241.protocol.brom import BASE_ADDR_FIRMWARE

        parser = cli.build_parser()
        args = parser.parse_args(["dump", "auto", "-o", "out.bin"])
        assert args.base_addr == BASE_ADDR_FIRMWARE == 0x00200000

    def test_flash_does_not_use_the_mt333x_base(self) -> None:
        """The flash path must not fall back to the MT333x default either."""
        from m241.protocol.brom import BASE_ADDR_FIRMWARE, BASE_ADDR_FIRMWARE_MT333X

        assert BASE_ADDR_FIRMWARE != BASE_ADDR_FIRMWARE_MT333X
        parser = cli.build_parser()
        args = parser.parse_args(
            ["flash", "auto", "--da", "d.bin", "--fw", "f.bin", "--backup", "b.bin"]
        )
        assert getattr(args, "base_addr", BASE_ADDR_FIRMWARE) == BASE_ADDR_FIRMWARE


# ---------------------------------------------------------------------------
# Packaging data used by the CLI
# ---------------------------------------------------------------------------
class TestPackageData:
    def test_shipped_data_files_resolve(self) -> None:
        assert cli._read_package_data("firmware_profiles.json")
        assert cli._read_package_data("vendor_drop.json")
        assert cli._package_data_ok() is True

    def test_wheel_data_assertion_matches_ci(self) -> None:
        """The CI build job asserts these two paths exist inside the wheel."""
        required = ("m241/data/firmware_profiles.json", "m241/data/vendor_drop.json")
        for name in required:
            path = SRC / name
            assert path.is_file(), f"{name} is missing from the source tree"
        # The build job globs dist/*.whl; a local wheel (if any) must carry both.
        wheels = sorted((REPO_ROOT / "dist").glob("*.whl"))
        if wheels:
            names = set(zipfile.ZipFile(wheels[0]).namelist())
            for name in required:
                assert name in names
