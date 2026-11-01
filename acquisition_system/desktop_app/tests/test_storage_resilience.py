import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from desktop_app import recording
from desktop_app.protocol import Sample
from desktop_app.tests.test_recording_pair import capability, configs
from desktop_app.ui import MainWindow


def test_periodic_sync_and_close_preserve_per_sample_flush(tmp_path, monkeypatch):
    clock = [100.0]
    synced = []
    real_fsync = os.fsync

    def fsync(fd):
        real_fsync(fd)
        synced.append(fd)

    monkeypatch.setattr(recording.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(recording.os, "fsync", fsync)
    rec = recording.Recorder(tmp_path / "periodic.csv", capability(), configs(),
                             [0] * 6, True, 0)
    assert len(synced) == 2
    for i in range(21):
        clock[0] = 100 + i * .5
        rec.row("sample", Sample(i * .5, (1, 2, 3, 4, 5, 6), 14),
                [1, 2, None, None, None, None], [0] * 6)
        # A separately opened file sees each completed row before a disk sync.
        with open(rec.result_path, encoding="utf-8-sig") as stream:
            assert len(stream.readlines()) == i + 2
    assert len(synced) == 4
    rec.close()
    assert len(synced) == 6


def test_sync_failure_is_reported_and_both_files_are_closed(tmp_path, monkeypatch):
    rec = recording.Recorder(tmp_path / "failure.csv", capability(), configs(),
                             [0] * 6, True, 0)
    def failed_sync(fd):
        raise OSError("simulated storage failure")
    monkeypatch.setattr(recording.os, "fsync", failed_sync)
    rec.last_sync -= 11
    with pytest.raises(OSError, match="storage failure"):
        rec.row("sample", Sample(1, (1, 2, 3, 4, 5, 6), 14),
                [1, 2, None, None, None, None], [0] * 6)
    with pytest.raises(OSError, match="storage failure"):
        rec.close()
    assert rec.raw_file.closed and rec.result_file.closed


def test_lock_stall_does_not_leave_healthy_banner_visible():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.timer.stop()
    window.driver_timer.stop()
    window.refresh()
    locked, release = threading.Event(), threading.Event()

    def hold_processing_lock():
        with window.engine.lock:
            locked.set()
            release.wait(5)

    thread = threading.Thread(target=hold_processing_lock)
    thread.start()
    try:
        assert locked.wait(2)
        window.was_recording = True
        window.banner.setText("数据有效")
        window.record_label.setText("● 正在记录")
        window.refresh()
        window._lock_wait_started -= 4
        window.refresh()
        assert "可能已过期" in window.banner.text()
        assert "进度尚未确认" in window.record_label.text()
        assert not window.start_btn.isEnabled()
        release.set()
        thread.join(2)
        window.refresh()
        assert "可能已过期" not in window.banner.text()
        assert not window._lock_warning_shown
    finally:
        release.set()
        thread.join(2)
        window.engine.shutdown()
        window.executor.shutdown()
        window.delivery_executor.shutdown()
        window.shutdown_done = True
        window.close()
        app.processEvents()
