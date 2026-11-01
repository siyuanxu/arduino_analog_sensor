import json
from queue import Empty, Queue
import threading
import time

from desktop_app.controller import AcquisitionEngine
from desktop_app import transport


def frame(device_time):
    info = {
        "protocol_version": 1,
        "firmware_version": "SESSION-QA",
        "board_model": "UNO_R4_MINIMA",
        "channel_count": 6,
        "channels": [f"A{i}" for i in range(6)],
        "adc_bits": 14,
        "sample_interval_ms": 500,
    }
    header = "device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,adc_bits"
    return (
        "#INFO " + json.dumps(info) + "\n#HEADER " + header + "\n" +
        f"{device_time:.3f},10,20,30,40,50,60,14\n"
    ).encode()


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("condition did not become true")


def test_old_session_data_and_disconnect_are_ignored():
    engine = AcquisitionEngine()
    try:
        with engine.lock:
            engine.connected = True
            engine.connection_session = 8
        engine.queue.put(("bytes", frame(.5), None, time.monotonic(), 8))
        wait_until(lambda: engine.sample_number == 1)
        engine.queue.put(("bytes", b"100.100,1,2,3,4,5,6,14\n",
                          None, time.monotonic(), 7))
        engine.queue.put(("disconnect", "old connection failed", None,
                          time.monotonic(), 7))
        # A queue-overflow or serial error callback can also arrive directly
        # after the new transport is already active.
        engine.fatal("old connection fatal", session_id=7)
        time.sleep(.1)
        assert engine.connected
        assert engine.sample_number == 1
        assert engine.latest.device_time_s == .5
        assert engine.latest.raw == (10, 20, 30, 40, 50, 60)
    finally:
        engine.shutdown()


def test_blocked_read_returning_after_stop_is_not_queued(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class DelayedSerial:
        in_waiting = 64

        def __init__(self, *_args, **_kwargs):
            pass

        def write(self, _data):
            pass

        def flush(self):
            pass

        def read(self, _size):
            entered.set()
            release.wait(2)
            return b"0.500,1,2,3,4,5,6,14\n"

        def close(self):
            pass

    monkeypatch.setattr(transport.serial, "Serial", DelayedSerial)
    queue = Queue()
    worker = transport.Transport(queue, lambda *_args: None, port="QA", session_id=3)
    worker.start()
    assert entered.wait(1)
    worker.stop()
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    try:
        late = queue.get_nowait()
    except Empty:
        late = None
    assert late is None


def test_queue_overflow_fatal_carries_transport_session():
    queue = Queue(maxsize=1)
    queue.put(("occupied", None, None, None))
    fatal_calls = []
    worker = transport.Transport(
        queue, lambda *args: fatal_calls.append(args), port="QA", session_id=12,
    )

    assert not worker.put("bytes", b"late")
    assert len(fatal_calls) == 1
    assert fatal_calls[0][1] == 12


def test_legacy_four_field_queue_items_remain_supported():
    engine = AcquisitionEngine()
    try:
        with engine.lock:
            engine.connected = True
        engine.queue.put(("bytes", frame(.5), None, time.monotonic()))
        wait_until(lambda: engine.sample_number == 1)
        assert engine.latest.device_time_s == .5
    finally:
        engine.shutdown()
