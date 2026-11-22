"""Tests for ops/wq5074-miko-uart-bench/boot_log_analyze.py (wq#5074).

No hardware. The clean input is the committed fixture tests/fixtures/mtk_boot_sample.txt
(synthetic, MTK preloader/BL31/LK/kernel-shaped lines; not a real Miko capture). Wrong-baud
input is produced by a bit-level 8N1 UART simulator that transmits the fixture at one rate and
receives it at another, so the baud_mismatch probe is driven by the same physics a real
mismatched FT232R sees rather than by hand-picked "garbage" bytes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import boot_log_analyze as bla  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "mtk_boot_sample.txt")


def _fixture() -> bytes:
    with open(FIXTURE, "rb") as fh:
        return fh.read()


def uart_resample(data: bytes, tx_baud: int, rx_baud: int) -> bytes:
    """Transmit `data` 8N1 at tx_baud back-to-back, receive with a 16x-oversampling 8N1 UART at rx_baud."""
    bits = [1] * 20
    for b in data:
        bits += [0] + [(b >> i) & 1 for i in range(8)] + [1]
    bits += [1] * 40
    end = len(bits) / tx_baud

    def level(t: float) -> int:
        i = int(t * tx_baud)
        return bits[i] if 0 <= i < len(bits) else 1

    out = bytearray()
    step = 1.0 / (16 * rx_baud)
    t, prev = 0.0, 1
    while t < end:
        cur = level(t)
        if prev == 1 and cur == 0:
            byte = 0
            for k in range(8):
                byte |= level(t + (k + 1.5) / rx_baud) << k
            out.append(byte)
            t += 9.5 / rx_baud  # mid stop bit; resume edge search from there
            prev = level(t)
            continue
        prev = cur
        t += step
    return bytes(out)


def test_simulator_roundtrips_at_matching_baud():
    data = _fixture()
    assert uart_resample(data, 921600, 921600) == data


def test_clean_fixture_identifies_every_stage():
    r = bla.analyze(_fixture(), 921600)
    assert r["verdict"] == "clean"
    assert r["stages"] == ["preloader", "tf-a", "lk", "kernel", "android"]
    assert bla.EXIT[r["verdict"]] == 0


def test_kernel_only_log_still_clean():
    data = b"[    0.000000] Booting Linux on physical CPU 0x0\r\n" * 3
    r = bla.analyze(data, 921600)
    assert r["verdict"] == "clean" and r["stages"] == ["kernel"]


def test_wrong_baud_is_not_clean_and_advises_another_rate():
    data = _fixture()
    for tx, rx in ((115200, 921600), (921600, 115200), (460800, 921600), (1500000, 921600)):
        got = uart_resample(data, tx, rx)
        r = bla.analyze(got, rx)
        assert r["verdict"] in ("baud_mismatch", "noisy", "stuck_line"), (tx, rx, r)
        assert r["verdict"] != "clean"
        assert r["stages"] == [], (tx, rx, r["stages"])


def test_wrong_baud_921600_receiver_advises_sweep():
    got = uart_resample(_fixture(), 115200, 921600)
    r = bla.analyze(got, 921600)
    assert r["verdict"] == "baud_mismatch", r
    assert "115200" in r["advice"] and "921600" not in r["advice"].split("next:")[1]


def test_no_data():
    r = bla.analyze(b"", 921600)
    assert r["verdict"] == "no_data" and bla.EXIT["no_data"] == 1


def test_line_held_low_is_stuck_not_mismatch():
    r = bla.analyze(b"\x00" * 200 + b"x", 921600)
    assert r["verdict"] == "stuck_line" and r["stuck_byte"] == 0


def test_readable_without_markers_is_clean_but_says_so():
    r = bla.analyze(b"hello from an unknown bootloader stage\r\n" * 2, 921600)
    assert r["verdict"] == "clean" and r["stages"] == []
    assert "no known boot marker" in r["advice"]


def test_tiny_capture_is_not_judged():
    r = bla.analyze(b"\x81\x92\xa3", 921600)
    assert r["verdict"] == "noisy"


def test_next_bauds_excludes_current():
    assert 921600 not in bla.next_bauds(921600)
    assert bla.next_bauds(None) == list(bla.SWEEP)


def test_cli_file_json(tmp_path, capsys):
    rc = bla.main([FIXTURE, "--baud", "921600", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "clean"


def test_cli_mismatch_exit_code(tmp_path, capsys):
    p = tmp_path / "bad.bin"
    p.write_bytes(uart_resample(_fixture(), 115200, 921600))
    assert bla.main([str(p), "--baud", "921600"]) == 2


def test_cli_missing_file_is_io_error(tmp_path, capsys):
    assert bla.main([str(tmp_path / "nope.bin")]) == 3


def test_capture_never_writes_and_deasserts_lines(monkeypatch):
    """--capture must be read-only: no write(), DTR/RTS False before open()."""
    events = []

    class FakeSerial:
        def __init__(self):
            self._dtr = self._rts = True
            self._chunks = [b"[PLFM] hi\r\n", b""]

        dtr = property(lambda s: s._dtr, lambda s, v: (events.append(("dtr", v)), setattr(s, "_dtr", v)))
        rts = property(lambda s: s._rts, lambda s, v: (events.append(("rts", v)), setattr(s, "_rts", v)))

        def open(self):
            events.append(("open", self._dtr, self._rts))

        def read(self, n):
            return self._chunks.pop(0) if self._chunks else b""

        def write(self, b):  # pragma: no cover - must never be reached
            raise AssertionError("capture wrote to the port")

        def close(self):
            events.append(("close",))

    fake_mod = type(sys)("serial")
    fake_mod.Serial = FakeSerial
    fake_mod.EIGHTBITS, fake_mod.PARITY_NONE, fake_mod.STOPBITS_ONE = 8, "N", 1
    monkeypatch.setitem(sys.modules, "serial", fake_mod)

    data = bla.capture("/dev/ttyUSB0", 921600, 0.05)
    assert data.startswith(b"[PLFM]")
    assert ("open", False, False) in events
    assert events[-1] == ("close",)
