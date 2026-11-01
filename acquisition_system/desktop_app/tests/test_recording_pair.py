import csv
import json
from pathlib import Path

import pytest

from desktop_app.models import ChannelConfig
from desktop_app.protocol import Capabilities, Sample
from desktop_app.recording import Recorder, recording_paths, result_columns


def capability():
    return Capabilities(1, "PAIR-TEST", "UNO_R4_MINIMA", 6, tuple(f"A{i}" for i in range(6)), 14, 1000)


def filtered_capability():
    return Capabilities(
        2,
        "1.2.0",
        "UNO_R4_MINIMA",
        6,
        tuple(f"A{i}" for i in range(6)),
        14,
        500,
        "trim5_200ms_A0_A1",
    )


def configs():
    return [
        ChannelConfig(True, "水平位移", 0, 100, decimals=2, raw1=16383),
        ChannelConfig(True, "拉力", 0, 50000, decimals=0, raw1=16383),
        *[ChannelConfig(raw1=16383) for _ in range(4)],
    ]


@pytest.mark.parametrize("selected", ["trial.csv", "trial_原始数据.csv", "trial_测试结果.csv"])
def test_recording_paths_normalizes_base_or_either_suffix(tmp_path, selected):
    raw, result = recording_paths(tmp_path / selected)
    assert raw == (tmp_path / "trial_原始数据.csv").resolve()
    assert result == (tmp_path / "trial_测试结果.csv").resolve()
    assert raw.is_absolute() and result.is_absolute()


def test_result_columns_and_paired_csv_schemas(tmp_path):
    cap, cfg = capability(), configs()
    assert result_columns(cap, cfg) == ["记录时间(s)", "A0 水平位移1(mm)", "A1 拉力1(N)"]
    recorder = Recorder(tmp_path / "trial.csv", cap, cfg, [1, 2, 0, 0, 0, 0], False, 100, "配对文件")
    first = Sample(100, (10, 20, 30, 40, 50, 60), 14)
    second = Sample(101, (11, 21, 31, 41, 51, 61), 14)
    try:
        recorder.row("start", first, [12.345, 1000, None, None, None, None], [1, 2, 0, 0, 0, 0], "开始记录")
        recorder.row("sample", first, [12.345, 1000, None, None, None, None], [1, 2, 0, 0, 0, 0])
        zero_offsets = [12.345, 1000, 0, 0, 0, 0]
        recorder.row("zero", first, [12.345, 1000, None, None, None, None], zero_offsets, "清零：A0, A1")
        recorder.row("sample", second, [13.456, 1004.4, None, None, None, None], zero_offsets)
        recorder.row("stop", second, [13.456, 1004.4, None, None, None, None], zero_offsets, "完成")
    finally:
        recorder.close()

    raw_path, result_path = recording_paths(tmp_path / "trial.csv")
    with raw_path.open(encoding="utf-8-sig", newline="") as stream:
        meta = json.loads(stream.readline()[2:])
        raw_rows = list(csv.DictReader(stream))
    assert meta["format_version"] == 2 and meta["file_kind"] == "raw"
    assert "channel_status_definition" in meta
    assert meta["raw_file"] == str(raw_path) and meta["result_file"] == str(result_path)
    assert meta["result_columns"] == result_columns(cap, cfg)
    expected_raw_header = [
        "record_type", "pc_receive_time", "device_time_s", "elapsed_s",
        "adc_bits", "message", "source", *[f"A{i}_raw" for i in range(6)],
    ]
    assert list(raw_rows[0]) == expected_raw_header
    assert [row["record_type"] for row in raw_rows] == ["start", "sample", "zero", "sample", "stop"]
    assert all(None not in row for row in raw_rows)
    assert not any("physical" in column or "offset" in column or "status" in column for column in raw_rows[0])
    zero = next(row for row in raw_rows if row["record_type"] == "zero")
    assert json.loads(zero["message"].split("offsets_json=", 1)[1]) == dict(zip(cap.channels, zero_offsets))

    assert result_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with result_path.open(encoding="utf-8-sig", newline="") as stream:
        result_rows = list(csv.DictReader(stream))
    assert list(result_rows[0]) == result_columns(cap, cfg)
    assert result_rows == [
        {"记录时间(s)": "0.000", "A0 水平位移1(mm)": "11.35", "A1 拉力1(N)": "998"},
        {"记录时间(s)": "1.000", "A0 水平位移1(mm)": "1.11", "A1 拉力1(N)": "4"},
    ]
    assert not result_path.read_text(encoding="utf-8-sig").startswith("#")
    assert not (tmp_path / "trial.csv").exists()


def test_v2_raw_csv_preserves_filter_evidence_and_results_stay_clean(tmp_path):
    cap, cfg = filtered_capability(), configs()
    windows = (
        (90, 100, 5000, 110, 120),
        (200, 220, 240, 260, 280),
    )
    filtered = (110, 240)
    sample = Sample(10.5, (120, 280, 30, 40, 50, 60), 14, filtered, windows)
    physical = [
        config.convert(sample.measurement_counts[index], cap.adc_bits) if config.enabled else None
        for index, config in enumerate(cfg)
    ]
    recorder = Recorder(tmp_path / "filtered.csv", cap, cfg, [0] * 6, False, 10, "去尖峰证据")
    try:
        recorder.row("sample", sample, physical, [0] * 6)
    finally:
        recorder.close()

    raw_path, result_path = recording_paths(tmp_path / "filtered.csv")
    with raw_path.open(encoding="utf-8-sig", newline="") as stream:
        metadata = json.loads(stream.readline()[2:])
        rows = list(csv.DictReader(stream))
    assert metadata["capabilities"]["filter"] == "trim5_200ms_A0_A1"
    assert metadata["adc_processing"] == {
        "wire_protocol_version": 2,
        "filter": "trim5_200ms_A0_A1",
        "frame_interval_ms": 500,
        "filtered_channels": ["A0", "A1"],
        "window_samples": 5,
        "window_interval_ms": 200,
        "window_order": "oldest_to_newest",
        "algorithm": "discard one minimum and one maximum, then average the remaining three with nearest-integer rounding",
        "conversion_counts": "firmware A0_filtered/A1_filtered for A0/A1; raw ADC counts for A2-A5",
        "raw_evidence": "A0_raw/A1_raw are the newest window samples; A0_window/A1_window preserve all five unfiltered ADC counts",
    }
    assert list(rows[0])[-4:] == ["A0_filtered", "A1_filtered", "A0_window", "A1_window"]
    assert rows[0]["A0_raw"] == "120" and rows[0]["A1_raw"] == "280"
    assert rows[0]["A0_filtered"] == "110" and rows[0]["A1_filtered"] == "240"
    assert rows[0]["A0_window"] == "90;100;5000;110;120"
    assert rows[0]["A1_window"] == "200;220;240;260;280"

    with result_path.open(encoding="utf-8-sig", newline="") as stream:
        result_rows = list(csv.DictReader(stream))
    assert list(result_rows[0]) == ["记录时间(s)", "A0 水平位移1(mm)", "A1 拉力1(N)"]
    assert result_rows == [{"记录时间(s)": "0.500", "A0 水平位移1(mm)": "0.67", "A1 拉力1(N)": "732"}]


def test_v2_recorder_refuses_inconsistent_filter_evidence(tmp_path):
    recorder = Recorder(
        tmp_path / "bad.csv",
        filtered_capability(),
        configs(),
        [0] * 6,
        False,
        0,
    )
    bad = Sample(
        1,
        (5, 10, 0, 0, 0, 0),
        14,
        (999, 6),
        ((1, 2, 3, 4, 5), (2, 4, 6, 8, 10)),
    )
    try:
        with pytest.raises(ValueError, match="无法由 5 点"):
            recorder.row("sample", bad, [0] * 6, [0] * 6)
    finally:
        recorder.close()


@pytest.mark.parametrize("existing_kind", ["raw", "result"])
def test_any_existing_pair_member_prevents_overwrite_and_cleans_new_file(tmp_path, existing_kind):
    raw_path, result_path = recording_paths(tmp_path / "conflict.csv")
    existing = raw_path if existing_kind == "raw" else result_path
    existing.write_text("KEEP", encoding="utf-8")
    with pytest.raises(FileExistsError):
        Recorder(tmp_path / "conflict.csv", capability(), configs(), [0] * 6, False, 0)
    assert existing.read_text(encoding="utf-8") == "KEEP"
    other = result_path if existing_kind == "raw" else raw_path
    assert not other.exists()


def test_initialization_failure_removes_only_files_created_by_this_attempt(tmp_path, monkeypatch):
    raw_path, result_path = recording_paths(tmp_path / "initfail.csv")

    def fail_writer(*_args, **_kwargs):
        raise OSError("simulated header failure")

    monkeypatch.setattr("desktop_app.recording.csv.DictWriter", fail_writer)
    with pytest.raises(OSError, match="header failure"):
        Recorder(tmp_path / "initfail.csv", capability(), configs(), [0] * 6, False, 0)
    assert not raw_path.exists() and not result_path.exists()


def test_sample_write_error_is_reported_and_close_attempts_both_streams(tmp_path):
    recorder = Recorder(tmp_path / "writefail.csv", capability(), configs(), [0] * 6, False, 0)
    original_raw, original_result = recorder.raw_file, recorder.result_file
    sample = Sample(0, (1, 2, 3, 4, 5, 6), 14)

    class FailedWriter:
        def writerow(self, _row):
            raise OSError("simulated result write failure")

    recorder.result_writer = FailedWriter()
    with pytest.raises(OSError, match="result write failure"):
        recorder.row("sample", sample, [1, 2, None, None, None, None], [0] * 6)

    closed_second = []

    class BadClose:
        def close(self):
            raise OSError("first close error")

    class GoodClose:
        def close(self):
            closed_second.append(True)

    recorder.raw_file, recorder.result_file = BadClose(), GoodClose()
    try:
        with pytest.raises(OSError, match="first close error"):
            recorder.close()
        assert closed_second == [True]
    finally:
        original_raw.close()
        original_result.close()
