"""Paired raw/result CSV recording with exclusive creation and per-row flush."""
import csv
from dataclasses import asdict, replace
from datetime import datetime
import json
import os
from pathlib import Path
import time

from .models import labels
from . import __version__


RAW_SUFFIX = "_原始数据"
RESULT_SUFFIX = "_测试结果"
SYNC_INTERVAL_SECONDS = 10.0


def adc_processing_metadata(cap):
    """Return the exact acquisition semantics stored with the raw evidence."""
    if cap.protocol_version != 2:
        return {
            "wire_protocol_version": cap.protocol_version,
            "frame_interval_ms": cap.sample_interval_ms,
            "conversion_counts": "raw ADC count for every channel",
        }
    return {
        "wire_protocol_version": 2,
        "filter": "trim5_200ms_A0_A1",
        "frame_interval_ms": cap.sample_interval_ms,
        "filtered_channels": ["A0", "A1"],
        "window_samples": 5,
        "window_interval_ms": 200,
        "window_order": "oldest_to_newest",
        "algorithm": "discard one minimum and one maximum, then average the remaining three with nearest-integer rounding",
        "conversion_counts": "firmware A0_filtered/A1_filtered for A0/A1; raw ADC counts for A2-A5",
        "raw_evidence": "A0_raw/A1_raw are the newest window samples; A0_window/A1_window preserve all five unfiltered ADC counts",
    }


def raw_columns(cap):
    columns = [
        "record_type", "pc_receive_time", "device_time_s", "elapsed_s",
        "adc_bits", "message", "source",
        *[f"{channel}_raw" for channel in cap.channels],
    ]
    if cap.protocol_version == 2:
        columns.extend(["A0_filtered", "A1_filtered", "A0_window", "A1_window"])
    return columns


def _validate_v2_sample_evidence(sample, cap):
    if len(sample.raw) != cap.channel_count:
        raise ValueError("采样原始通道数与设备不一致")
    if len(sample.filtered) != 2 or len(sample.windows) != 2:
        raise ValueError("v2 采样缺少 A0/A1 固件滤值或 5 点窗口")
    maximum = (1 << cap.adc_bits) - 1
    values = [*sample.raw, *sample.filtered]
    for channel, window in zip(("A0", "A1"), sample.windows):
        if len(window) != 5:
            raise ValueError(f"{channel} 滤波窗口必须包含 5 个原始值")
        values.extend(window)
    if any(type(value) is not int or value < 0 or value > maximum for value in values):
        raise ValueError("v2 采样证据含非法 ADC 值")
    if sample.raw[:2] != tuple(window[-1] for window in sample.windows):
        raise ValueError("A0/A1 原始值与窗口末点不一致")
    expected = tuple(
        (sum(window) - min(window) - max(window) + 1) // 3
        for window in sample.windows
    )
    if sample.filtered != expected:
        raise ValueError("固件滤值无法由 5 点原始窗口复算")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def recording_paths(path):
    """Return absolute paired paths from a base name or either paired filename."""
    selected = Path(path).expanduser()
    stem = selected.name[:-len(selected.suffix)] if selected.suffix.lower() == ".csv" else selected.name
    for suffix in (RAW_SUFFIX, RESULT_SUFFIX):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    if not stem:
        raise ValueError("CSV文件基名不能为空")
    parent = selected.parent.resolve()
    return (
        (parent / f"{stem}{RAW_SUFFIX}.csv").resolve(),
        (parent / f"{stem}{RESULT_SUFFIX}.csv").resolve(),
    )


def result_columns(cap, configs):
    """Return the complete result header, including its first elapsed-time column."""
    if len(configs) != cap.channel_count:
        raise ValueError("配置通道数与设备不一致")
    sensor_labels = labels(configs)
    return ["记录时间(s)"] + [
        f"{sensor_labels[index]}({config.unit})"
        for index, config in enumerate(configs)
        if config.enabled
    ]


class Recorder:
    def __init__(self, path, cap, configs, offsets, simulated, start_time, experiment_name=""):
        raw_path, result_path = recording_paths(path)
        self.raw_path = str(raw_path)
        self.result_path = str(result_path)
        self.path = self.result_path
        self.cap = cap
        self.configs = [replace(config) for config in configs]
        self.start_time = start_time
        self.raw_columns = raw_columns(cap)
        self.columns = self.raw_columns
        self.result_headers = result_columns(cap, self.configs)
        self.enabled_indices = [index for index, config in enumerate(self.configs) if config.enabled]
        self.result_header_by_index = dict(zip(self.enabled_indices, self.result_headers[1:]))
        self.source = "SIMULATED" if simulated else "SERIAL"
        self.raw_file = None
        self.result_file = None
        created = []
        try:
            self.raw_file = open(raw_path, "x", encoding="utf-8-sig", newline="")
            created.append(raw_path)
            self.result_file = open(result_path, "x", encoding="utf-8-sig", newline="")
            created.append(result_path)
            meta = {
                "format_version": 2,
                "file_kind": "raw",
                "software_version": __version__,
                "experiment_name": experiment_name,
                "file_path": self.result_path,
                "raw_file": self.raw_path,
                "result_file": self.result_path,
                "result_columns": self.result_headers,
                "sensor_labels": labels(self.configs),
                "source": self.source,
                "created_at": now_iso(),
                "capabilities": asdict(cap),
                "configs": [
                    asdict(config) | {
                        "unit": config.unit,
                        "calibration_status": "未实测标定" if config.mode == "名义换算" else "用户两点标定",
                    }
                    for config in self.configs
                ],
                "initial_offsets": list(offsets),
                "adc_processing": adc_processing_metadata(cap),
                "nominal_formula": "min + conversion_count/(2^adc_bits-1)*(max-min)",
                "physical_values_not_clipped": True,
                "channel_status_definition": "ok: minimum <= physical <= maximum; over_range: unzeroed physical outside configured range; disabled: empty. Result CSV omits status columns.",
                "result_values": "engineering value converted from adc_processing.conversion_counts, minus current offset; zero events and offset snapshots are stored in the raw file",
            }
            self.raw_file.write("# " + json.dumps(meta, ensure_ascii=False) + "\n")
            self.raw_writer = csv.DictWriter(self.raw_file, fieldnames=self.raw_columns)
            self.raw_writer.writeheader()
            self.raw_file.flush()
            self.result_writer = csv.DictWriter(self.result_file, fieldnames=self.result_headers)
            self.result_writer.writeheader()
            self.result_file.flush()
            self.sync()
        except Exception:
            for stream in (self.raw_file, self.result_file):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            for created_path in created:
                try:
                    created_path.unlink()
                except OSError:
                    pass
            raise

    def row(self, kind, sample=None, physical=None, offsets=None, message="", received=None):
        raw_message = message
        if kind == "zero" and offsets is not None:
            snapshot = {channel: offsets[index] for index, channel in enumerate(self.cap.channels)}
            suffix = "offsets_json=" + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            raw_message = f"{message} | {suffix}" if message else suffix
        raw_row = {
            "record_type": kind,
            "pc_receive_time": received or now_iso(),
            "adc_bits": self.cap.adc_bits,
            "message": raw_message,
            "source": self.source,
        }
        if sample is not None:
            if self.cap.protocol_version == 2:
                _validate_v2_sample_evidence(sample, self.cap)
            raw_row.update(device_time_s=sample.device_time_s, elapsed_s=sample.device_time_s - self.start_time)
            for index, channel in enumerate(self.cap.channels):
                raw_row[f"{channel}_raw"] = sample.raw[index]
            if self.cap.protocol_version == 2:
                raw_row.update(
                    A0_filtered=sample.filtered[0],
                    A1_filtered=sample.filtered[1],
                    A0_window=";".join(str(value) for value in sample.windows[0]),
                    A1_window=";".join(str(value) for value in sample.windows[1]),
                )
        try:
            self.raw_writer.writerow(raw_row)
            self.raw_file.flush()
            if kind == "sample":
                elapsed = sample.device_time_s - self.start_time
                result_row = {"记录时间(s)": f"{elapsed:.3f}"}
                for index in self.enabled_indices:
                    config = self.configs[index]
                    value = physical[index] - offsets[index]
                    result_row[self.result_header_by_index[index]] = f"{value:.{config.decimals}f}"
                self.result_writer.writerow(result_row)
                self.result_file.flush()
            if time.monotonic() - self.last_sync >= SYNC_INTERVAL_SECONDS:
                self.sync()
        except (OSError, csv.Error) as error:
            if isinstance(error, OSError):
                raise
            raise OSError(f"CSV写入失败：{error}") from error

    def sync(self):
        """Ask the OS to persist both files without changing the sampling rate."""
        for stream in (self.raw_file, self.result_file):
            stream.flush()
            os.fsync(stream.fileno())
        self.last_sync = time.monotonic()

    def close(self):
        first_error = None
        for stream in (self.raw_file, self.result_file):
            if stream is None:
                continue
            try:
                if not stream.closed:
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception as error:
                if first_error is None:
                    first_error = error
            try:
                stream.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            if isinstance(first_error, OSError):
                raise first_error
            raise OSError(f"关闭CSV失败：{first_error}") from first_error
