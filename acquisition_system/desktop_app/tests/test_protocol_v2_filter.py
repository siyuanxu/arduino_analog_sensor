import csv
import json
import time

import pytest

from desktop_app import __version__
from desktop_app.controller import AcquisitionEngine
from desktop_app.models import ChannelConfig
from desktop_app.protocol import Capabilities, Parser, ProtocolError, Sample
from desktop_app.recording import recording_paths


FILTER = "trim5_200ms_A0_A1"
HEADER = (
    "device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,"
    "A0_filtered,A1_filtered,A0_window,A1_window,adc_bits"
)


def info_v2(**changes):
    payload = {
        "protocol_version": 2,
        "firmware_version": "1.2.0",
        "board_model": "UNO_R4_MINIMA",
        "channel_count": 6,
        "channels": [f"A{i}" for i in range(6)],
        "adc_bits": 14,
        "sample_interval_ms": 500,
        "filter": FILTER,
    }
    payload.update(changes)
    return "#INFO " + json.dumps(payload)


def frame_v2(t=1.0, raw0=12, raw1=22, filtered0=11, filtered1=21):
    return (
        f"{t:.3f},{raw0},{raw1},30,40,50,60,{filtered0},{filtered1},"
        "9;10;100;11;12,19;20;200;21;22,14"
    )


def parser_v2():
    parser = Parser()
    cap = parser.parse(info_v2())
    assert cap.filter == FILTER
    assert cap.header == HEADER.split(",")
    assert parser.parse("#HEADER " + HEADER) == "header"
    return parser


def test_v2_parses_firmware_filter_and_preserves_wire_raw():
    parser = parser_v2()
    sample = parser.parse(frame_v2())
    assert sample == Sample(
        1.0,
        (12, 22, 30, 40, 50, 60),
        14,
        (11, 21),
        ((9, 10, 100, 11, 12), (19, 20, 200, 21, 22)),
    )
    assert sample.raw == (12, 22, 30, 40, 50, 60)
    assert sample.measurement_counts == (11, 21, 30, 40, 50, 60)


def test_old_sample_and_capability_constructors_remain_compatible():
    cap = Capabilities(
        1, "MEGA-LEGACY", "MEGA_2560", 16,
        tuple(f"A{i}" for i in range(16)), 10, 1000,
    )
    sample = Sample(1, tuple(range(16)), 10)
    assert cap.filter is None
    assert sample.filtered == sample.windows == ()
    assert sample.measurement_counts == sample.raw
    assert cap.header[-1] == "adc_bits"


@pytest.mark.parametrize(
    "line, message",
    [
        (frame_v2(raw0=13), "窗口末点"),
        (frame_v2(filtered0=12), "固件滤值"),
        (frame_v2().replace("9;10;100;11;12", "9;10;11;12"), "5 个"),
        (frame_v2().replace("19;20;200;21;22", "19;20;x;21;22"), "非负整数"),
        (frame_v2().replace("9;10;100;11;12", "9;10;20000;11;12"), "ADC 范围"),
    ],
)
def test_v2_rejects_inconsistent_or_malformed_filter_evidence(line, message):
    parser = parser_v2()
    with pytest.raises(ProtocolError, match=message):
        parser.parse(line)
    assert parser.last_time is None


@pytest.mark.parametrize(
    "info_line, message",
    [
        (info_v2(filter="other"), "滤波能力"),
        (info_v2(board_model="MEGA_2560", channel_count=16,
                 channels=[f"A{i}" for i in range(16)], adc_bits=10,
                 sample_interval_ms=1000), "滤波能力"),
        (info_v2(sample_interval_ms=1000), "滤波能力"),
        (info_v2(protocol_version=3), "协议版本"),
    ],
)
def test_v2_rejects_wrong_capabilities(info_line, message):
    with pytest.raises(ProtocolError, match=message):
        Parser().parse(info_line)


def test_controller_uses_filtered_counts_for_conversion_configure_and_zero():
    engine = AcquisitionEngine()
    try:
        engine.connected = True
        engine.simulated = False
        received = time.monotonic()
        payload = (info_v2() + "\n#HEADER " + HEADER + "\n" + frame_v2() + "\n").encode()
        with engine.lock:
            engine.ingest(payload, mono=received)
        configs = [
            ChannelConfig(True, "拉力", 0, 16383),
            ChannelConfig(True, "水平位移", 0, 16383),
            *[ChannelConfig() for _ in range(4)],
        ]
        engine.configure(configs)
        with engine.lock:
            assert engine.latest.raw[:2] == (12, 22)
            assert engine.latest.measurement_counts[:2] == (11, 21)
            assert engine.physical[:2] == [11, 21]
            assert engine.history == []
        engine.zero([0, 1])
        with engine.lock:
            assert engine.offsets[:2] == [11, 21]
            assert engine.history == [(0.0, [0, 0, None, None, None, None])]
    finally:
        engine.shutdown()


def test_recording_keeps_wire_raw_while_result_uses_firmware_filter(tmp_path):
    engine = AcquisitionEngine()
    try:
        engine.connected = True
        engine.simulated = False
        with engine.lock:
            engine.ingest(
                (info_v2() + "\n#HEADER " + HEADER + "\n" + frame_v2() + "\n").encode(),
                mono=time.monotonic(),
            )
        engine.configure([
            ChannelConfig(True, "拉力", 0, 16383),
            ChannelConfig(True, "水平位移", 0, 16383),
            *[ChannelConfig() for _ in range(4)],
        ])
        selected = tmp_path / "filter.csv"
        engine.start_recording(selected)
        engine.stop_recording()
        raw_path, result_path = recording_paths(selected)
        with raw_path.open(encoding="utf-8-sig") as stream:
            next(stream)
            raw_rows = list(csv.DictReader(stream))
        sample_row = next(row for row in raw_rows if row["record_type"] == "sample")
        assert (sample_row["A0_raw"], sample_row["A1_raw"]) == ("12", "22")
        with result_path.open(encoding="utf-8-sig") as stream:
            result_rows = list(csv.DictReader(stream))
        assert tuple(result_rows[0].values())[1:] == ("11.000", "21.000")
    finally:
        engine.shutdown()


def test_package_version_is_1_2_6():
    assert __version__ == "1.2.6"
