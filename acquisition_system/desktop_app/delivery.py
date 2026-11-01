"""Bounded, read-only inspection of acquisition CSV artifacts."""
import csv
import io
import json
import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from .models import ChannelConfig, labels
from .protocol import Capabilities
from .recording import (
    adc_processing_metadata,
    raw_columns,
    recording_paths,
    result_columns,
)


MAX_PREVIEW_BYTES = 512 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_PREVIEW_ROWS = 40

COMMON_COLUMNS = [
    "record_type",
    "pc_receive_time",
    "device_time_s",
    "elapsed_s",
    "adc_bits",
    "message",
    "source",
]
RECORD_TYPES = {"start", "sample", "zero", "error", "stop"}
CONFIG_FIELDS = tuple(ChannelConfig.__dataclass_fields__)


def _read_bounded(path):
    """Return a complete-line prefix without reading more than 512 KiB."""
    try:
        size_before = path.stat().st_size
        with path.open("rb") as stream:
            raw = stream.read(MAX_PREVIEW_BYTES)
        size_after = path.stat().st_size
    except OSError as error:
        raise ValueError(f"CSV 文件无法读取：{error}") from error
    if not raw:
        raise ValueError("CSV 文件为空")
    byte_limited = max(size_before, size_after) > len(raw)
    if byte_limited:
        last_newline = raw.rfind(b"\n")
        if last_newline < 0:
            probe = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
            if probe.startswith(b"# "):
                raise ValueError("参数快照超过 64 KiB 限制")
            raise ValueError("CSV 首行超过 512 KiB 预览上限")
        raw = raw[: last_newline + 1]
    return raw, byte_limited


def _parse_metadata_line(line, *, paired=False):
    if len(line.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("参数快照超过 64 KiB 限制")
    if not line.startswith("# "):
        location = "配对原始文件" if paired else "CSV"
        raise ValueError(f"{location}缺少首行 # JSON 参数快照")
    try:
        metadata = json.loads(line[2:])
    except json.JSONDecodeError as error:
        raise ValueError(f"参数快照不是有效 JSON：{error.msg}") from error
    if not isinstance(metadata, dict):
        raise ValueError("参数快照 JSON 必须是对象")
    return metadata


def _read_paired_raw(path):
    """Read at most 512 KiB from a same-directory raw pairing candidate."""
    raw, byte_limited = _read_bounded(path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("配对原始文件不是有效 UTF-8") from error
    metadata_line, separator, body = text.partition("\n")
    if not separator:
        if len(metadata_line.encode("utf-8")) >= MAX_METADATA_BYTES:
            raise ValueError("配对原始文件的参数快照超过 64 KiB 限制")
        raise ValueError("配对原始文件缺少完整的首行参数快照")
    metadata = _parse_metadata_line(metadata_line.rstrip("\r"), paired=True)
    return metadata, body, len(raw), byte_limited


def _recorded_path_has_name(value, expected_name):
    """Compare metadata paths portably without following either path."""
    if not isinstance(value, str) or not value.strip():
        return False
    return expected_name in {
        PurePosixPath(value).name,
        PureWindowsPath(value).name,
    }


def _capabilities_and_configs(metadata, *, validate_values=False):
    try:
        cap = Capabilities.parse(json.dumps(metadata["capabilities"], ensure_ascii=False))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"参数快照中的设备能力无效：{error}") from error
    configs = metadata.get("configs")
    if (
        not isinstance(configs, list)
        or len(configs) != cap.channel_count
        or not all(isinstance(config, dict) for config in configs)
    ):
        raise ValueError("参数快照中的通道配置不完整")
    if not validate_values:
        return cap, configs, None

    config_objects = []
    try:
        for index, stored in enumerate(configs):
            missing = [name for name in CONFIG_FIELDS if name not in stored]
            if missing:
                raise ValueError(f"A{index} 缺少 {', '.join(missing)}")
            config = ChannelConfig(**{name: stored[name] for name in CONFIG_FIELDS})
            config.validate(cap.adc_bits)
            if "unit" in stored and stored["unit"] != config.unit:
                raise ValueError(f"A{index} 单位与传感器类型不一致")
            config_objects.append(config)
    except (TypeError, ValueError) as error:
        raise ValueError(f"参数快照中的通道配置无效：{error}") from error
    return cap, configs, config_objects


def _collect_rows(reader, header, limit, validate_row):
    rows = []
    for row_number in range(1, limit + 1):
        try:
            row = next(reader)
        except StopIteration:
            break
        if len(row) != len(header):
            raise ValueError(f"预览第 {row_number} 行列数错误，应为 {len(header)} 列")
        validate_row(row, row_number)
        rows.append(row)
    if not rows:
        raise ValueError("CSV 暂无完整数据行，请刷新预览")
    try:
        next(reader)
    except StopIteration:
        row_limited = False
    else:
        row_limited = True
    return rows, row_limited


def _validate_v2_raw_metadata(metadata, raw_path):
    if type(metadata.get("format_version")) is not int or metadata["format_version"] != 2:
        raise ValueError("原始数据参数快照的格式版本必须为 2")
    if metadata.get("file_kind") != "raw":
        raise ValueError("版本 2 的 # JSON 文件必须标记 file_kind=raw")
    cap, configs, config_objects = _capabilities_and_configs(metadata, validate_values=True)
    if metadata.get("source") not in ("SIMULATED", "SERIAL"):
        raise ValueError("参数快照缺少明确数据来源")
    expected_raw, expected_result = recording_paths(raw_path)
    if raw_path != expected_raw:
        raise ValueError("原始数据文件名不符合 *_原始数据.csv 规范")
    if not _recorded_path_has_name(metadata.get("raw_file"), expected_raw.name):
        raise ValueError("参数快照的 raw_file 与当前原始文件名不匹配")
    if not _recorded_path_has_name(metadata.get("result_file"), expected_result.name):
        raise ValueError("参数快照的 result_file 与配对测试结果文件名不匹配")
    stored_columns = metadata.get("result_columns")
    expected_columns = result_columns(cap, config_objects)
    if stored_columns != expected_columns:
        raise ValueError("参数快照的 result_columns 与通道配置不一致")
    if metadata.get("sensor_labels") != labels(config_objects):
        raise ValueError("参数快照的 sensor_labels 与通道配置不一致")
    initial_offsets = metadata.get("initial_offsets")
    if not isinstance(initial_offsets, list) or len(initial_offsets) != cap.channel_count:
        raise ValueError("参数快照的 initial_offsets 不完整")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in initial_offsets
    ):
        raise ValueError("参数快照的 initial_offsets 必须是有限数值")
    stored_processing = metadata.get("adc_processing")
    if (
        cap.protocol_version == 2
        and stored_processing != adc_processing_metadata(cap)
    ) or (
        cap.protocol_version == 1
        and stored_processing is not None
        and stored_processing != adc_processing_metadata(cap)
    ):
        raise ValueError("参数快照的 ADC 时序/滤波处理说明与设备能力不一致")
    return cap, configs, config_objects, list(initial_offsets), expected_raw, expected_result


def _parse_v2_window(value, channel, context):
    parts = value.split(";")
    if len(parts) != 5 or not all(re.fullmatch(r"[0-9]+", part) for part in parts):
        raise ValueError(f"{context}{channel} 窗口必须是 5 个分号分隔的原始 ADC 整数")
    return tuple(int(part) for part in parts)


def _sample_counts_from_row(row, header, cap, context, *, required):
    """Validate one optional sample payload and return counts used for conversion."""
    values = dict(zip(header, row))
    payload_fields = [
        "device_time_s", "elapsed_s",
        *[f"{channel}_raw" for channel in cap.channels],
    ]
    if cap.protocol_version == 2:
        payload_fields.extend(["A0_filtered", "A1_filtered", "A0_window", "A1_window"])
    present = [values[field] != "" for field in payload_fields]
    if not any(present):
        if required:
            raise ValueError(f"{context}sample 行缺少采样数值")
        return None
    if not all(present):
        raise ValueError(f"{context}采样证据不完整")
    if values["adc_bits"] == "":
        raise ValueError(f"{context}采样证据不完整")
    try:
        device_time = float(values["device_time_s"])
        elapsed = float(values["elapsed_s"])
        if not math.isfinite(device_time) or device_time < 0:
            raise ValueError
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError
        if not re.fullmatch(r"[0-9]+", values["adc_bits"]):
            raise ValueError
        if int(values["adc_bits"]) != cap.adc_bits:
            raise ValueError
        raw_cells = [values[f"{channel}_raw"] for channel in cap.channels]
        if not all(re.fullmatch(r"[0-9]+", value) for value in raw_cells):
            raise ValueError
        raw = tuple(int(value) for value in raw_cells)
        maximum = (1 << cap.adc_bits) - 1
        if any(value > maximum for value in raw):
            raise ValueError
        if cap.protocol_version == 1:
            return elapsed, raw

        filtered_cells = [values["A0_filtered"], values["A1_filtered"]]
        if not all(re.fullmatch(r"[0-9]+", value) for value in filtered_cells):
            raise ValueError
        filtered = tuple(int(value) for value in filtered_cells)
        windows = (
            _parse_v2_window(values["A0_window"], "A0", context),
            _parse_v2_window(values["A1_window"], "A1", context),
        )
        evidence_values = (*filtered, *(value for window in windows for value in window))
        if any(value > maximum for value in evidence_values):
            raise ValueError
        if raw[:2] != tuple(window[-1] for window in windows):
            raise ValueError(f"{context}A0/A1 原始值与窗口最新点不一致")
        expected = tuple(
            (sum(window) - min(window) - max(window) + 1) // 3
            for window in windows
        )
        if filtered != expected:
            raise ValueError(f"{context}固件滤值无法由 5 点原始窗口复算")
        return elapsed, (*filtered, *raw[2:])
    except ValueError as error:
        # Preserve the more useful evidence-specific messages above.
        if str(error).startswith(context):
            raise
        raise ValueError(f"{context}采样数值无效") from error


def _preview_versioned(path, text, byte_limited, limit, bytes_read):
    metadata_line, separator, body = text.partition("\n")
    if not separator:
        raise ValueError("CSV 缺少参数快照之后的表头和数据行")
    metadata = _parse_metadata_line(metadata_line.rstrip("\r"))
    version = metadata.get("format_version")
    if type(version) is not int:
        raise ValueError("参数快照 format_version 必须是整数")

    expected_result = None
    if version == 1:
        cap, _, _ = _capabilities_and_configs(metadata)
        if cap.protocol_version != 1:
            raise ValueError("旧版 CSV 格式仅支持协议 v1，不能缺省 v2 滤波证据")
        if metadata.get("source") not in ("SIMULATED", "SERIAL"):
            raise ValueError("参数快照缺少明确数据来源")
        required = list(COMMON_COLUMNS)
        for channel in cap.channels:
            required.extend(
                [
                    f"{channel}_raw",
                    f"{channel}_physical",
                    f"{channel}_offset",
                    f"{channel}_zeroed",
                    f"{channel}_status",
                ]
            )
        file_kind = "legacy"
    elif version == 2:
        cap, _, _, _, _, expected_result = _validate_v2_raw_metadata(metadata, path)
        required = raw_columns(cap)
        file_kind = "raw"
    else:
        raise ValueError(f"不支持的参数快照格式版本：{version}")

    reader = csv.reader(io.StringIO(body), strict=True)
    try:
        header = next(reader)
    except StopIteration as error:
        raise ValueError("CSV 缺少表头") from error
    if header != required:
        raise ValueError("CSV 列名、顺序或通道数与参数快照不一致")

    def validate_row(row, row_number):
        if row[0] not in RECORD_TYPES:
            raise ValueError("预览行的 record_type 无效")
        if row[6] != metadata["source"]:
            raise ValueError("预览行的数据来源与参数快照不一致")
        if version == 2:
            _sample_counts_from_row(
                row,
                header,
                cap,
                f"原始数据预览第 {row_number} 行：",
                required=row[0] == "sample",
            )

    rows, row_limited = _collect_rows(reader, header, limit, validate_row)
    result = {
        "path": str(path),
        "metadata": metadata,
        "header": header,
        "rows": rows,
        "limited": byte_limited or row_limited,
        "bytes_read": bytes_read,
        "file_kind": file_kind,
        "metadata_available": True,
        "paired_bytes_read": 0,
    }
    if expected_result is not None and expected_result.exists():
        result["pair_path"] = str(expected_result)
    return result


def _standalone_result_metadata(path, header, verification):
    return {
        "format_version": 2,
        "file_kind": "results",
        "software_version": "",
        "experiment_name": "",
        "source": "UNKNOWN",
        "created_at": "",
        "capabilities": None,
        "configs": [],
        "sensor_labels": [],
        "result_columns": list(header),
        "raw_file": None,
        "result_file": str(path),
        "pair_verification": verification,
    }


def _offsets_from_zero_message(message, cap):
    marker = "offsets_json="
    if marker not in message:
        raise ValueError("配对原始文件的 zero 事件缺少 offsets_json 快照")
    try:
        snapshot = json.loads(message.split(marker, 1)[1].strip())
    except json.JSONDecodeError as error:
        raise ValueError("配对原始文件的 zero 偏置快照不是有效 JSON") from error
    if not isinstance(snapshot, dict) or set(snapshot) != set(cap.channels):
        raise ValueError("配对原始文件的 zero 偏置快照通道不完整")
    offsets = [snapshot[channel] for channel in cap.channels]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in offsets
    ):
        raise ValueError("配对原始文件的 zero 偏置快照必须是有限数值")
    return offsets


def _verify_results_against_raw(raw_path, result_path, header, result_rows):
    metadata, body, bytes_read, byte_limited = _read_paired_raw(raw_path)
    cap, _, configs, offsets, _, expected_result = _validate_v2_raw_metadata(metadata, raw_path)
    if result_path != expected_result:
        raise ValueError(
            f"同名原始文件存在，但当前结果文件名应为 {expected_result.name}"
        )
    if metadata["result_columns"] != header:
        raise ValueError("测试结果表头与配对原始文件的 result_columns 不匹配")

    reader = csv.reader(io.StringIO(body), strict=True)
    try:
        raw_header = next(reader)
    except StopIteration as error:
        raise ValueError("配对原始文件缺少表头") from error
    expected_raw_header = raw_columns(cap)
    if raw_header != expected_raw_header:
        raise ValueError("配对原始文件的表头与设备能力不一致")

    enabled_indices = [index for index, config in enumerate(configs) if config.enabled]
    matched = 0
    for event_number, row in enumerate(reader, 1):
        if len(row) != len(raw_header):
            raise ValueError(
                f"配对原始文件第 {event_number} 行列数错误，应为 {len(raw_header)} 列"
            )
        if row[0] not in RECORD_TYPES:
            raise ValueError(f"配对原始文件第 {event_number} 行 record_type 无效")
        if row[6] != metadata["source"]:
            raise ValueError(f"配对原始文件第 {event_number} 行数据来源不一致")
        sample_data = _sample_counts_from_row(
            row,
            raw_header,
            cap,
            f"配对原始文件第 {event_number} 行：",
            required=row[0] == "sample",
        )
        if row[0] == "zero":
            offsets = _offsets_from_zero_message(row[5], cap)
            continue
        if row[0] != "sample":
            continue
        elapsed, conversion_counts = sample_data

        expected = [f"{elapsed:.3f}"]
        for index in enabled_indices:
            value = configs[index].convert(conversion_counts[index], cap.adc_bits) - offsets[index]
            expected.append(f"{value:.{configs[index].decimals}f}")
        actual = result_rows[matched]
        if actual != expected:
            differing = next(
                column
                for column, actual_value, expected_value in zip(header, actual, expected)
                if actual_value != expected_value
            )
            raise ValueError(
                f"测试结果与同名原始文件内容不匹配："
                f"第 {matched + 1} 行 {differing} 无法由原始 ADC、量程和清零偏置"
                f"重算得到（如含固件滤波，使用并核对经窗口复算的滤值）"
            )
        matched += 1
        if matched == len(result_rows):
            return metadata, bytes_read, True

    if byte_limited:
        return metadata, bytes_read, False
    raise ValueError("测试结果行数多于同名原始文件中的 sample 记录，无法确认配对")


def _preview_results(path, text, byte_limited, limit, bytes_read):
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        header = next(reader)
    except StopIteration as error:
        raise ValueError("测试结果 CSV 缺少表头") from error
    if (
        not header
        or header[0] != "记录时间(s)"
        or any(not column for column in header)
        or len(set(header)) != len(header)
    ):
        raise ValueError("测试结果 CSV 表头无效：首列必须为“记录时间(s)”且列名不能为空或重复")

    previous_time = None

    def validate_row(row, row_number):
        nonlocal previous_time
        try:
            numbers = [float(value) for value in row]
        except ValueError as error:
            raise ValueError(f"测试结果第 {row_number} 行含非数字值") from error
        if not all(math.isfinite(value) for value in numbers):
            raise ValueError(f"测试结果第 {row_number} 行含 NaN 或无穷大")
        elapsed = numbers[0]
        if elapsed < 0:
            raise ValueError(f"测试结果第 {row_number} 行的记录时间不能为负数")
        if previous_time is not None and elapsed < previous_time:
            raise ValueError(f"测试结果第 {row_number} 行的记录时间倒退")
        previous_time = elapsed

    rows, row_limited = _collect_rows(reader, header, limit, validate_row)
    expected_raw, _ = recording_paths(path)
    metadata_available = False
    pair_path = None
    paired_bytes_read = 0
    if expected_raw.exists():
        paired_metadata, paired_bytes_read, verified = _verify_results_against_raw(
            expected_raw, path, header, rows
        )
        if verified:
            metadata = dict(paired_metadata)
            metadata["file_kind"] = "results"
            metadata["pair_verification"] = (
                f"已用原始 ADC、量程和清零偏置核对当前预览的 {len(rows)} 行结果；"
                "如含固件滤波，同时核对了 5 点原始窗口与滤值"
            )
            metadata_available = True
            pair_path = str(expected_raw)
        else:
            verification = (
                f"配对未验证：原始文件前 {paired_bytes_read} 字节"
                f"不足以重算当前预览的 {len(rows)} 行结果"
            )
            metadata = _standalone_result_metadata(path, header, verification)
    else:
        verification = "配对未验证：同目录下未找到规范同名原始数据文件"
        metadata = _standalone_result_metadata(path, header, verification)

    result = {
        "path": str(path),
        "metadata": metadata,
        "header": header,
        "rows": rows,
        "limited": byte_limited or row_limited,
        "bytes_read": bytes_read,
        "file_kind": "results",
        "metadata_available": metadata_available,
        "paired_bytes_read": paired_bytes_read,
        "pair_verification": metadata["pair_verification"],
    }
    if pair_path is not None:
        result["pair_path"] = pair_path
    return result


def read_csv_preview(path, max_rows=MAX_PREVIEW_ROWS):
    """Read-only preview: at most 512 KiB per main and same-name raw file, and 40 rows."""
    if type(max_rows) is not int or max_rows <= 0:
        raise ValueError("max_rows 必须是正整数")
    limit = min(max_rows, MAX_PREVIEW_ROWS)
    path = Path(path).expanduser().resolve()
    raw, byte_limited = _read_bounded(path)
    bytes_read = len(raw)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("CSV 不是有效 UTF-8 文件") from error
    try:
        if text.startswith("# "):
            return _preview_versioned(path, text, byte_limited, limit, bytes_read)
        return _preview_results(path, text, byte_limited, limit, bytes_read)
    except csv.Error as error:
        raise ValueError(f"CSV 格式无效：{error}") from error
