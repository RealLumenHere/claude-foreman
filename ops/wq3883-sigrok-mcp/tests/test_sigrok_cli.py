"""Unit tests for sigrok_cli argv-building and output-parsing (wq#3883).

Scan/decode fixture text below is copied verbatim from sigrok-cli(1) EXAMPLES
and the sigrok wiki (File_format:protocoldata, Protocol_decoder:I2c) rather
than invented, so a parsing regression here is a real regression, not a
mismatch against a guessed fixture.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sigrok_cli import (  # noqa: E402
    SigrokCliError,
    capture_argv,
    decode_argv,
    driver_arg,
    export_argv,
    normalize_samplerate,
    parse_decode_output,
    parse_scan_output,
    scan_argv,
    show_argv,
)


@pytest.mark.parametrize("raw,expected_hz", [
    (1_000_000, 1_000_000),
    ("1000000", 1_000_000),
    ("1MHz", 1_000_000),
    ("1 MHz", 1_000_000),
    ("1m", 1_000_000),
    ("500kHz", 500_000),
    ("500khz", 500_000),
    ("2GHz", 2_000_000_000),
    ("2G", 2_000_000_000),
])
def test_normalize_samplerate_accepts_documented_forms(raw, expected_hz):
    assert normalize_samplerate(raw) == expected_hz


@pytest.mark.parametrize("raw", ["", "nope", "-1MHz", -5, 0, "MHz"])
def test_normalize_samplerate_rejects_garbage(raw):
    with pytest.raises(SigrokCliError):
        normalize_samplerate(raw)


def test_driver_arg_plain():
    assert driver_arg("fx2lafw") == "fx2lafw"


def test_driver_arg_with_conn():
    assert driver_arg("ols", conn="/dev/ttyACM0") == "ols:conn=/dev/ttyACM0"


def test_driver_arg_requires_driver():
    with pytest.raises(SigrokCliError):
        driver_arg("")


def test_scan_argv_no_driver():
    assert scan_argv() == ["sigrok-cli", "--scan"]


def test_scan_argv_with_driver_conn():
    assert scan_argv(driver="digitek-dt4000zc", conn="/dev/ttyUSB0") == [
        "sigrok-cli", "--driver", "digitek-dt4000zc:conn=/dev/ttyUSB0", "--scan",
    ]


# Verbatim from sigrok-cli(1) --scan EXAMPLES.
SCAN_OUTPUT_AUTODETECT = """\
The following devices were found:
demo - Demo device with 12 channels: D0 D1 D2 D3 D4 D5 D6 D7 A0 A1 A2 A3
fx2lafw:conn=3.26 - CWAV USBee SX with 8 channels: 0 1 2 3 4 5 6 7
"""

SCAN_OUTPUT_PINNED_DRIVER = """\
The following devices were found:
Digitek DT4000ZC with 1 channel: P1
"""


def test_parse_scan_output_autodetect():
    devices = parse_scan_output(SCAN_OUTPUT_AUTODETECT)
    assert devices == [
        {
            "driver": "demo", "conn": "",
            "description": "Demo device",
            "channel_count": 12,
            "channels": ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "A0", "A1", "A2", "A3"],
        },
        {
            "driver": "fx2lafw", "conn": "3.26",
            "description": "CWAV USBee SX",
            "channel_count": 8,
            "channels": ["0", "1", "2", "3", "4", "5", "6", "7"],
        },
    ]


def test_parse_scan_output_pinned_driver_has_no_driver_prefix():
    devices = parse_scan_output(SCAN_OUTPUT_PINNED_DRIVER)
    assert devices == [{
        "driver": "", "conn": "",
        "description": "Digitek DT4000ZC",
        "channel_count": 1,
        "channels": ["P1"],
    }]


def test_parse_scan_output_ignores_unmatched_lines():
    assert parse_scan_output("garbage\n\n") == []


def test_show_argv_driver():
    assert show_argv(driver="fx2lafw") == ["sigrok-cli", "--driver", "fx2lafw", "--show"]


def test_show_argv_decoder():
    assert show_argv(decoder="i2c") == ["sigrok-cli", "--protocol-decoders", "i2c", "--show"]


def test_show_argv_requires_a_target():
    with pytest.raises(SigrokCliError):
        show_argv()


def test_capture_argv_with_samples():
    argv = capture_argv("fx2lafw", 1_000_000, "/tmp/out.sr", channels="1=CLK,2-4,7", samples="100")
    assert argv == [
        "sigrok-cli",
        "--driver", "fx2lafw",
        "--config", "samplerate=1000000",
        "--channels", "1=CLK,2-4,7",
        "--samples", "100",
        "--output-file", "/tmp/out.sr",
    ]


def test_capture_argv_with_duration_and_format():
    argv = capture_argv("fx2lafw", 500_000, "/tmp/out.csv", duration="2s", output_format="csv")
    assert argv == [
        "sigrok-cli",
        "--driver", "fx2lafw",
        "--config", "samplerate=500000",
        "--time", "2s",
        "--output-format", "csv",
        "--output-file", "/tmp/out.csv",
    ]


def test_capture_argv_rejects_samples_and_duration_together():
    with pytest.raises(SigrokCliError):
        capture_argv("fx2lafw", 1_000_000, "/tmp/out.sr", samples="100", duration="2s")


def test_capture_argv_requires_output_path():
    with pytest.raises(SigrokCliError):
        capture_argv("fx2lafw", 1_000_000, "")


def test_decode_argv():
    argv = decode_argv("/tmp/cap.sr", "spi:miso=1:mosi=5:clk=3:cs=0", annotations="spi")
    assert argv == [
        "sigrok-cli",
        "--input-file", "/tmp/cap.sr",
        "--protocol-decoders", "spi:miso=1:mosi=5:clk=3:cs=0",
        "--protocol-decoder-annotations", "spi",
    ]


def test_decode_argv_requires_input_and_decoders():
    with pytest.raises(SigrokCliError):
        decode_argv("", "i2c")
    with pytest.raises(SigrokCliError):
        decode_argv("/tmp/cap.sr", "")


# Verbatim from sigrok wiki Protocol_decoder:I2c and File_format:Protocoldata.
DECODE_OUTPUT_I2C = """\
i2c-1: Address write: A0
i2c-1: Data write: 01
i2c-1: Data write: 50
i2c-1: nack: NACK
i2c-1: stop: Stop
"""

DECODE_OUTPUT_UART = """\
uart-1: T
uart-1: [0A]
"""


def test_parse_decode_output_i2c():
    assert parse_decode_output(DECODE_OUTPUT_I2C) == [
        {"decoder": "i2c", "row": 1, "text": "Address write: A0"},
        {"decoder": "i2c", "row": 1, "text": "Data write: 01"},
        {"decoder": "i2c", "row": 1, "text": "Data write: 50"},
        {"decoder": "i2c", "row": 1, "text": "nack: NACK"},
        {"decoder": "i2c", "row": 1, "text": "stop: Stop"},
    ]


def test_parse_decode_output_uart():
    assert parse_decode_output(DECODE_OUTPUT_UART) == [
        {"decoder": "uart", "row": 1, "text": "T"},
        {"decoder": "uart", "row": 1, "text": "[0A]"},
    ]


def test_export_argv():
    assert export_argv("/tmp/cap.sr", "/tmp/out.vcd", "vcd") == [
        "sigrok-cli",
        "--input-file", "/tmp/cap.sr",
        "--output-format", "vcd",
        "--output-file", "/tmp/out.vcd",
    ]


@pytest.mark.parametrize("input_file,output_path,output_format", [
    ("", "/tmp/out.vcd", "vcd"),
    ("/tmp/cap.sr", "", "vcd"),
    ("/tmp/cap.sr", "/tmp/out.vcd", ""),
])
def test_export_argv_requires_all_fields(input_file, output_path, output_format):
    with pytest.raises(SigrokCliError):
        export_argv(input_file, output_path, output_format)
