import csv
from dataclasses import asdict
import hashlib
import json

import pytest

from desktop_app.delivery import MAX_PREVIEW_BYTES, read_csv_preview
from desktop_app.models import ChannelConfig, labels
from desktop_app.protocol import Capabilities, Sample
from desktop_app.recording import Recorder, recording_paths


def capability():
    return Capabilities(
        1,
        "1.0.0",
        "UNO_R4_MINIMA",
        6,
        tuple(f"A{i}" for i in range(6)),
        14,
        1000,
    )


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


def configurations(horizontal_maximum=50):
    return [
        ChannelConfig(True, "水平位移", 0, horizontal_maximum, 2, raw1=16383),
        ChannelConfig(),
        ChannelConfig(True, "拉力", 0, 5000, 1, raw1=16383),
        ChannelConfig(),
        ChannelConfig(),
        ChannelConfig(),
    ]


def make_pair(path, *, simulated=True, horizontal_maximum=50, raw_delta=0):
    cap = capability()
    configs = configurations(horizontal_maximum)
    first_raw = tuple(value + raw_delta for value in (100, 200, 300, 400, 500, 600))
    second_raw = tuple(value + raw_delta for value in (110, 210, 310, 410, 510, 610))
    first_physical = [
        config.convert(first_raw[index], cap.adc_bits) if config.enabled else None
        for index, config in enumerate(configs)
    ]
    second_physical = [
        config.convert(second_raw[index], cap.adc_bits) if config.enabled else None
        for index, config in enumerate(configs)
    ]
    zero_offsets = [
        first_physical[index] if config.enabled else 0
        for index, config in enumerate(configs)
    ]
    recorder = Recorder(path, cap, configs, [0] * 6, simulated, 10, "成果只读测试")
    recorder.row(
        "sample",
        Sample(10, first_raw, 14),
        first_physical,
        [0] * 6,
    )
    recorder.row(
        "zero",
        Sample(10, first_raw, 14),
        first_physical,
        zero_offsets,
        "已清零",
    )
    recorder.row(
        "sample",
        Sample(11, second_raw, 14),
        second_physical,
        zero_offsets,
    )
    recorder.row("stop", message="正常停止")
    recorder.close()
    return recording_paths(path)


def make_filtered_pair(path):
    cap = filtered_capability()
    configs = [
        ChannelConfig(True, "水平位移", 0, 50, 2, raw1=16383),
        ChannelConfig(True, "拉力", 0, 50000, 0, raw1=16383),
        *[ChannelConfig(raw1=16383) for _ in range(4)],
    ]
    windows = (
        (90, 100, 5000, 110, 120),
        (200, 220, 240, 260, 280),
    )
    sample = Sample(
        10.5,
        (120, 280, 300, 400, 500, 600),
        14,
        (110, 240),
        windows,
    )
    physical = [
        config.convert(sample.measurement_counts[index], cap.adc_bits) if config.enabled else None
        for index, config in enumerate(configs)
    ]
    recorder = Recorder(path, cap, configs, [0] * 6, False, 10, "v2 可复算证据")
    recorder.row("sample", sample, physical, [0] * 6)
    recorder.close()
    return recording_paths(path)


def test_v2_raw_and_paired_results_preview(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "trial.csv")

    raw = read_csv_preview(raw_path)
    assert raw["file_kind"] == "raw"
    assert raw["metadata_available"] is True
    assert raw["metadata"]["format_version"] == 2
    assert raw["metadata"]["file_kind"] == "raw"
    assert raw["header"] == [
        "record_type",
        "pc_receive_time",
        "device_time_s",
        "elapsed_s",
        "adc_bits",
        "message",
        "source",
        *[f"A{i}_raw" for i in range(6)],
    ]
    assert [row[0] for row in raw["rows"]] == ["sample", "zero", "sample", "stop"]
    assert "offsets_json=" in raw["rows"][1][5]
    assert raw["pair_path"] == str(result_path)
    assert raw["limited"] is False

    results = read_csv_preview(result_path)
    assert results["file_kind"] == "results"
    assert results["metadata_available"] is True
    assert results["metadata"]["file_kind"] == "results"
    assert results["metadata"]["experiment_name"] == "成果只读测试"
    assert results["header"] == [
        "记录时间(s)",
        "A0 水平位移1(mm)",
        "A2 拉力1(N)",
    ]
    assert results["rows"] == [["0.000", "0.31", "91.6"], ["1.000", "0.03", "3.1"]]
    assert results["pair_path"] == str(raw_path)
    assert 0 < results["paired_bytes_read"] <= MAX_PREVIEW_BYTES
    assert "原始 ADC、量程和清零偏置" in results["pair_verification"]
    assert results["limited"] is False


def test_filtered_pair_preview_recomputes_firmware_values_from_windows(tmp_path):
    raw_path, result_path = make_filtered_pair(tmp_path / "filtered.csv")
    raw = read_csv_preview(raw_path)
    assert raw["header"][-4:] == ["A0_filtered", "A1_filtered", "A0_window", "A1_window"]
    assert raw["rows"][0][-4:] == [
        "110", "240", "90;100;5000;110;120", "200;220;240;260;280"
    ]
    assert raw["metadata"]["adc_processing"]["window_interval_ms"] == 200
    assert raw["metadata"]["adc_processing"]["conversion_counts"].startswith("firmware")

    results = read_csv_preview(result_path)
    assert results["metadata_available"] is True
    assert results["rows"] == [["0.500", "0.34", "732"]]
    assert "5 点原始窗口与滤值" in results["pair_verification"]


@pytest.mark.parametrize("column,replacement,message", [
    ("A0_filtered", "111", "固件滤值无法由 5 点"),
    ("A0_window", "90;100;5000;110;119", "原始值与窗口最新点"),
])
def test_filtered_pair_never_endorses_corrupt_evidence(tmp_path, column, replacement, message):
    raw_path, result_path = make_filtered_pair(tmp_path / "filtered.csv")
    with raw_path.open(encoding="utf-8-sig", newline="") as stream:
        metadata_line = stream.readline()
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        rows = list(reader)
    rows[0][column] = replacement
    with raw_path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write(metadata_line.lstrip("\ufeff"))
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match=message):
        read_csv_preview(raw_path)
    with pytest.raises(ValueError, match=message):
        read_csv_preview(result_path)


def test_filtered_metadata_processing_description_is_verified(tmp_path):
    raw_path, result_path = make_filtered_pair(tmp_path / "filtered.csv")
    lines = raw_path.read_text(encoding="utf-8-sig").splitlines()
    metadata = json.loads(lines[0][2:])
    metadata["adc_processing"]["window_interval_ms"] = 123
    raw_path.write_text(
        "# " + json.dumps(metadata, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError, match="ADC 时序/滤波处理说明"):
        read_csv_preview(result_path)


def test_standalone_copied_results_need_no_metadata(tmp_path):
    _, result_path = make_pair(tmp_path / "trial.csv")
    copied = tmp_path / "exports" / "renamed.csv"
    copied.parent.mkdir()
    copied.write_bytes(result_path.read_bytes())

    preview = read_csv_preview(copied)
    assert preview["file_kind"] == "results"
    assert preview["metadata_available"] is False
    assert "pair_path" not in preview
    assert preview["metadata"] == {
        "format_version": 2,
        "file_kind": "results",
        "software_version": "",
        "experiment_name": "",
        "source": "UNKNOWN",
        "created_at": "",
        "capabilities": None,
        "configs": [],
        "sensor_labels": [],
        "result_columns": preview["header"],
        "raw_file": None,
        "result_file": str(copied.resolve()),
        "pair_verification": "配对未验证：同目录下未找到规范同名原始数据文件",
    }
    assert preview["paired_bytes_read"] == 0


def test_existing_v1_protocol_pair_without_processing_metadata_stays_compatible(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "old-pair.csv")
    lines = raw_path.read_text(encoding="utf-8-sig").splitlines()
    metadata = json.loads(lines[0][2:])
    metadata.pop("adc_processing")
    raw_path.write_text(
        "# " + json.dumps(metadata, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
        encoding="utf-8-sig",
    )

    preview = read_csv_preview(result_path)
    assert preview["metadata_available"] is True
    assert preview["metadata"]["capabilities"]["protocol_version"] == 1
    assert preview["rows"] == [["0.000", "0.31", "91.6"], ["1.000", "0.03", "3.1"]]


def test_paired_results_reject_corrupt_or_mismatched_raw(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "trial.csv")
    lines = raw_path.read_text(encoding="utf-8-sig").splitlines()
    metadata = json.loads(lines[0][2:])
    metadata["result_columns"] = ["记录时间(s)", "错误列"]
    raw_path.write_text(
        "# " + json.dumps(metadata, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError, match="result_columns"):
        read_csv_preview(result_path)

    raw_path.write_text("not-json\nthis must not be mixed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="配对原始文件.*# JSON"):
        read_csv_preview(result_path)


def test_paired_results_reject_corrupt_raw_body_with_bounded_read(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "trial.csv")
    first_line = raw_path.read_bytes().splitlines(keepends=True)[0]
    raw_path.write_bytes(first_line + b"\xff" * (MAX_PREVIEW_BYTES * 2))
    with pytest.raises(ValueError, match="配对原始文件.*缺少表头"):
        read_csv_preview(result_path)


def test_same_name_and_header_do_not_hide_different_source_range_and_raw(tmp_path):
    _, result_a = make_pair(tmp_path / "trial_a.csv")
    _, result_b = make_pair(
        tmp_path / "trial_b.csv",
        simulated=False,
        horizontal_maximum=80,
        raw_delta=1000,
    )
    # Simulate an accidental overwrite by another trial with the same clean header.
    result_a.write_bytes(result_b.read_bytes())
    with pytest.raises(ValueError, match="原始 ADC、量程和清零偏置"):
        read_csv_preview(result_a)


def test_pair_verification_replays_zero_offset_events(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "trial.csv")
    with raw_path.open(encoding="utf-8-sig", newline="") as stream:
        metadata_line = stream.readline()
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        rows = list(reader)
    zero = next(row for row in rows if row["record_type"] == "zero")
    prefix, payload = zero["message"].split("offsets_json=", 1)
    offsets = json.loads(payload)
    offsets["A0"] = 0
    zero["message"] = prefix + "offsets_json=" + json.dumps(offsets, separators=(",", ":"))
    with raw_path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write(metadata_line.lstrip("\ufeff"))
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match="清零偏置"):
        read_csv_preview(result_path)


def test_insufficient_bounded_raw_downgrades_to_unverified_metadata(tmp_path):
    raw_path, result_path = make_pair(tmp_path / "trial.csv")
    lines = raw_path.read_bytes().splitlines(keepends=True)
    stop_line = lines[-1]
    raw_path.write_bytes(lines[0] + lines[1] + stop_line * 12000 + b"".join(lines[2:-1]))

    preview = read_csv_preview(result_path)
    assert preview["file_kind"] == "results"
    assert preview["metadata_available"] is False
    assert preview["metadata"]["capabilities"] is None
    assert preview["metadata"]["source"] == "UNKNOWN"
    assert "pair_path" not in preview
    assert 0 < preview["paired_bytes_read"] <= MAX_PREVIEW_BYTES
    assert "不足以重算" in preview["pair_verification"]


def test_paired_metadata_has_strict_64_kib_limit(tmp_path):
    result_path = tmp_path / "trial_测试结果.csv"
    result_path.write_text("记录时间(s),A0 拉力1(N)\n0,1\n", encoding="utf-8")
    raw_path = tmp_path / "trial_原始数据.csv"
    raw_path.write_bytes(b"# " + b"x" * (64 * 1024) + b"\n")
    with pytest.raises(ValueError, match="64 KiB"):
        read_csv_preview(result_path)


def test_legacy_v1_preview_remains_supported(tmp_path):
    cap = capability()
    configs = configurations()
    metadata = {
        "format_version": 1,
        "source": "SIMULATED",
        "experiment_name": "legacy",
        "capabilities": asdict(cap),
        "configs": [asdict(config) for config in configs],
        "sensor_labels": labels(configs),
    }
    header = [
        "record_type",
        "pc_receive_time",
        "device_time_s",
        "elapsed_s",
        "adc_bits",
        "message",
        "source",
    ]
    for channel in cap.channels:
        header.extend(
            [
                f"{channel}_raw",
                f"{channel}_physical",
                f"{channel}_offset",
                f"{channel}_zeroed",
                f"{channel}_status",
            ]
        )
    path = tmp_path / "legacy.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write("# " + json.dumps(metadata, ensure_ascii=False) + "\n")
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerow(["sample", "now", "1", "0", "14", "", "SIMULATED", *(["0"] * 30)])

    preview = read_csv_preview(path)
    assert preview["file_kind"] == "legacy"
    assert preview["metadata_available"] is True
    assert preview["metadata"]["format_version"] == 1
    assert preview["header"] == header
    assert preview["limited"] is False


def test_legacy_csv_cannot_claim_v2_protocol_without_filter_evidence(tmp_path):
    cap = filtered_capability()
    configs = configurations()
    metadata = {
        "format_version": 1,
        "source": "SERIAL",
        "capabilities": asdict(cap),
        "configs": [asdict(config) for config in configs],
    }
    header = list((
        "record_type", "pc_receive_time", "device_time_s", "elapsed_s",
        "adc_bits", "message", "source",
    ))
    for channel in cap.channels:
        header.extend(
            f"{channel}_{suffix}"
            for suffix in ("raw", "physical", "offset", "zeroed", "status")
        )
    path = tmp_path / "false-v2-legacy.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write("# " + json.dumps(metadata, ensure_ascii=False) + "\n")
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerow(["sample", "now", "1", "0", "14", "", "SERIAL", *(["0"] * 30)])

    with pytest.raises(ValueError, match="旧版 CSV 格式仅支持协议 v1"):
        read_csv_preview(path)


def test_bounded_preview_and_exact_row_limit_preserve_source(tmp_path):
    exact = tmp_path / "exact.csv"
    exact.write_text(
        "记录时间(s),A0 拉力1(N)\n"
        + "".join(f"{index},{index}.0\n" for index in range(40)),
        encoding="utf-8",
    )
    assert read_csv_preview(exact)["limited"] is False
    with exact.open("a", encoding="utf-8") as stream:
        stream.write("40,40.0\n")
    assert read_csv_preview(exact)["limited"] is True

    large = tmp_path / "large.csv"
    large.write_text(
        "记录时间(s),A0 拉力1(N)\n"
        + "".join(f"{index},{'9' * 100}\n" for index in range(12000)),
        encoding="utf-8",
    )
    before = hashlib.sha256(large.read_bytes()).digest()
    preview = read_csv_preview(large)
    assert len(preview["rows"]) == 40
    assert preview["bytes_read"] <= MAX_PREVIEW_BYTES
    assert preview["limited"] is True
    assert hashlib.sha256(large.read_bytes()).digest() == before


@pytest.mark.parametrize(
    "body,message",
    [
        ("-1,1\n", "负数"),
        ("0,nan\n", "NaN"),
        ("0,inf\n", "NaN"),
        ("0,1\n1,2\n0.5,3\n", "倒退"),
        ("0,not-a-number\n", "非数字"),
    ],
)
def test_results_reject_invalid_numeric_rows(tmp_path, body, message):
    path = tmp_path / "result.csv"
    path.write_text("记录时间(s),A0 拉力1(N)\n" + body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_csv_preview(path)


@pytest.mark.parametrize(
    "content,message",
    [
        ("# []\nheader\nrow\n", "JSON 必须是对象"),
        ("# {broken\nheader\nrow\n", "不是有效 JSON"),
        ("record_type,foo\nsample,1\n", "测试结果 CSV 表头无效"),
    ],
)
def test_preview_rejects_invalid_json_or_clean_header(tmp_path, content, message):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_csv_preview(path)
