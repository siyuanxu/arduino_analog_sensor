import csv
import json
import time

import pytest

from desktop_app.controller import AcquisitionEngine
from desktop_app.models import ChannelConfig
from desktop_app.recording import recording_paths


def handshake(n, bits):
    info = {
        "protocol_version": 1,
        "firmware_version": "ZERO-TEST-1.0",
        "board_model": "MEGA_2560" if n == 16 else "UNO_R4_MINIMA",
        "channel_count": n,
        "channels": [f"A{i}" for i in range(n)],
        "adc_bits": bits,
        "sample_interval_ms": 1000,
    }
    header = "device_time_s," + ",".join(f"A{i}_raw" for i in range(n)) + ",adc_bits"
    return ("#INFO " + json.dumps(info) + "\n#HEADER " + header + "\n").encode()


def frame(device_time, raw, bits):
    return (f"{device_time:.3f}," + ",".join(map(str, raw)) + f",{bits}\n").encode()


def make_engine(n, bits, first_time=100.0):
    engine = AcquisitionEngine()
    engine.connected = True
    engine.simulated = True
    engine.connection_started = time.monotonic()
    first_raw = [10 + i for i in range(n)]
    with engine.lock:
        engine.ingest(handshake(n, bits) + frame(first_time, first_raw, bits))
    configs = [
        ChannelConfig(
            enabled=i < 2,
            kind="拉力" if i == 0 else "水平位移" if i == 1 else "未配置",
            minimum=0,
            maximum=100,
            raw1=(1 << bits) - 1,
        )
        for i in range(n)
    ]
    engine.configure(configs)
    return engine


@pytest.mark.parametrize("n,bits", [(6, 14), (16, 10)])
def test_connect_configure_and_successful_recording_advance_plot_epoch(tmp_path, n, bits):
    engine = AcquisitionEngine()
    try:
        assert engine.plot_epoch == 0
        engine.connect(simulated="MEGA_2560" if n == 16 else "UNO_R4_MINIMA", interval=10)
        assert engine.plot_epoch == 1
    finally:
        engine.shutdown()

    engine = make_engine(n, bits)
    try:
        # Direct fixture did not call connect; configure is its first reset.
        assert engine.plot_epoch == 1
        with engine.lock:
            engine.ingest(frame(101, [20 + i for i in range(n)], bits))
        offsets = list(engine.offsets)
        path = tmp_path / f"SIMULATED_epoch_{n}.csv"
        engine.start_recording(path)
        assert engine.plot_epoch == 2
        assert [point[0] for point in engine.history] == [0.0]
        assert engine.graph_origin == 101
        assert engine.offsets == offsets
    finally:
        engine.shutdown()


@pytest.mark.parametrize("n,bits", [(6, 14), (16, 10)])
@pytest.mark.parametrize("selection", ["selected", "all"])
def test_successful_zero_discards_plot_cache_and_restarts_visual_time(n, bits, selection):
    engine = make_engine(n, bits)
    maximum = (1 << bits) - 1
    try:
        with engine.lock:
            engine.ingest(frame(101, [maximum // 4 + i for i in range(n)], bits))
            engine.ingest(frame(102, [maximum // 2 + i for i in range(n)], bits))
            assert [point[0] for point in engine.history] == [1.0, 2.0]
        old_epoch = engine.plot_epoch
        old_second_offset = engine.offsets[1]

        engine.zero([0] if selection == "selected" else None)

        assert engine.plot_epoch == old_epoch + 1
        assert engine.graph_origin == 102
        assert len(engine.history) == 1
        x, values = engine.history[0]
        assert x == 0.0
        assert values[0] == pytest.approx(0.0)
        if selection == "selected":
            assert engine.offsets[1] == old_second_offset
            assert values[1] == pytest.approx(engine.physical[1] - old_second_offset)
        else:
            assert values[1] == pytest.approx(0.0)

        with engine.lock:
            engine.ingest(frame(103, [maximum // 2 + 10 + i for i in range(n)], bits))
        assert [point[0] for point in engine.history] == [0.0, 1.0]

        previous_epoch = engine.plot_epoch
        engine.zero([0] if selection == "selected" else None)
        assert engine.plot_epoch == previous_epoch + 1
        assert len(engine.history) == 1
        assert engine.history[0][0] == 0.0
        with engine.lock:
            engine.ingest(frame(104, [maximum // 2 + 20 + i for i in range(n)], bits))
        assert [point[0] for point in engine.history] == [0.0, 1.0]
    finally:
        engine.shutdown()


@pytest.mark.parametrize("n,bits", [(6, 14), (16, 10)])
def test_zero_keeps_csv_history_and_recording_elapsed_time(tmp_path, n, bits):
    engine = make_engine(n, bits, first_time=500)
    path = tmp_path / f"SIMULATED_zero_csv_{n}.csv"
    try:
        engine.start_recording(path)
        with engine.lock:
            engine.ingest(frame(501, [20 + i for i in range(n)], bits))
        engine.zero([0])
        with engine.lock:
            engine.ingest(frame(502, [30 + i for i in range(n)], bits))
        engine.stop_recording()

        raw_path, result_path = recording_paths(path)
        assert engine.last_raw_path == str(raw_path)
        assert engine.last_path == str(result_path)
        assert engine.record_outcomes[str(raw_path)].startswith("已结束")
        assert engine.record_outcomes[str(result_path)].startswith("已结束")
        with raw_path.open(encoding="utf-8-sig") as stream:
            stream.readline()
            rows = list(csv.DictReader(stream))
        samples = [row for row in rows if row["record_type"] == "sample"]
        zero = next(row for row in rows if row["record_type"] == "zero")
        assert [float(row["device_time_s"]) for row in samples] == [500, 501, 502]
        assert [float(row["elapsed_s"]) for row in samples] == [0, 1, 2]
        assert float(zero["device_time_s"]) == 501
        assert float(zero["elapsed_s"]) == 1
        assert json.loads(zero["message"].split("offsets_json=", 1)[1])["A0"] == engine.offsets[0]
        with result_path.open(encoding="utf-8-sig") as stream:
            result_rows = list(csv.DictReader(stream))
        assert [float(row["记录时间(s)"]) for row in result_rows] == [0, 1, 2]
        assert [point[0] for point in engine.history] == [0.0, 1.0]
    finally:
        engine.shutdown()


@pytest.mark.parametrize("n,bits", [(6, 14), (16, 10)])
def test_start_recording_restarts_plot_but_stop_keeps_current_segment(tmp_path, n, bits):
    engine = make_engine(n, bits)
    try:
        maximum = (1 << bits) - 1
        with engine.lock:
            engine.ingest(frame(101, [maximum // 4 + i for i in range(n)], bits))
            engine.ingest(frame(102, [maximum // 2 + i for i in range(n)], bits))
        epoch = engine.plot_epoch
        offsets = list(engine.offsets)

        engine.start_recording(tmp_path / f"SIMULATED_preview_{n}.csv")
        assert [point[0] for point in engine.history] == [0.0]
        assert engine.plot_epoch == epoch + 1
        assert engine.graph_origin == 102
        assert engine.offsets == offsets
        with engine.lock:
            engine.ingest(frame(103, [maximum // 2 + 10 + i for i in range(n)], bits))
        engine.stop_recording()

        assert [point[0] for point in engine.history] == [0.0, 1.0]
        assert engine.plot_epoch == epoch + 1
        assert engine.graph_origin == 102
    finally:
        engine.shutdown()


def test_failed_recording_start_preserves_preview_and_visual_epoch(tmp_path, monkeypatch):
    engine = make_engine(6, 14)
    try:
        with engine.lock:
            engine.ingest(frame(101, [100 + i for i in range(6)], 14))
        preview = list(engine.history)
        origin = engine.graph_origin
        epoch = engine.plot_epoch
        offsets = list(engine.offsets)

        def fail(*_args, **_kwargs):
            raise OSError("start disk failure")

        monkeypatch.setattr("desktop_app.controller.Recorder.row", fail)
        with pytest.raises(OSError, match="start disk failure"):
            engine.start_recording(tmp_path / "SIMULATED_failed_start.csv")

        assert list(engine.history) == preview
        assert engine.graph_origin == origin
        assert engine.plot_epoch == epoch
        assert engine.offsets == offsets
        assert engine.recorder is None
    finally:
        engine.shutdown()


def test_failed_zero_preserves_history_offsets_origin_and_epoch():
    engine = make_engine(6, 14)
    try:
        with engine.lock:
            engine.ingest(frame(101, [100 + i for i in range(6)], 14))
        snapshot = list(engine.history)
        offsets = list(engine.offsets)
        origin = engine.graph_origin
        epoch = engine.plot_epoch

        with pytest.raises(ValueError, match="没有启用"):
            engine.zero([2])
        assert list(engine.history) == snapshot
        assert engine.offsets == offsets
        assert engine.graph_origin == origin
        assert engine.plot_epoch == epoch

        with pytest.raises(ValueError, match="索引无效"):
            engine.zero([0, 6])
        assert list(engine.history) == snapshot
        assert engine.offsets == offsets
        assert engine.graph_origin == origin
        assert engine.plot_epoch == epoch
    finally:
        engine.shutdown()
