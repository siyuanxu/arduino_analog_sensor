import json
import math
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6 import QtCore, QtWidgets as W
from desktop_app.models import ChannelConfig
from desktop_app.ui import MainWindow, UI_REFRESH_INTERVAL_MS


@pytest.mark.parametrize('selected', [None, [0]])
def test_zero_redraws_entire_plot_before_normal_throttle(selected):
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    engine = window.engine
    info = dict(protocol_version=1, firmware_version='SIMULATED-1.0.0',
                board_model='UNO_R4_MINIMA', channel_count=6,
                channels=[f'A{i}' for i in range(6)], adc_bits=14, sample_interval_ms=1000)
    def frame(t):
        return (f'{t}.000,' + ','.join(['500']*6) + ',14\n').encode()
    try:
        engine.connected = engine.simulated = True
        with engine.lock:
            engine.ingest(('#INFO '+json.dumps(info)+'\n#HEADER device_time_s,'+
                           ','.join(f'A{i}_raw' for i in range(6))+',adc_bits\n').encode()+frame(500))
        engine.configure([ChannelConfig(True, '水平位移'), ChannelConfig(True, '拉力')]+
                         [ChannelConfig() for _ in range(4)])
        window.tabs.setCurrentIndex(1)
        with engine.lock:
            engine.ingest(frame(501)+frame(502))
            window.refresh()
        assert all(len(curve.getData()[0]) == 2 for curve, _ in window.curves)
        window.curve_checks[1].setChecked(False)
        # Reproduce a zero command immediately after a normal plot update.
        window.last_plot = time.monotonic()
        epoch = window.seen_plot_epoch
        engine.zero(selected)
        with engine.lock:
            window.refresh()
        assert window.seen_plot_epoch > epoch
        for index, (curve, _) in enumerate(window.curves):
            x, y = curve.getData()
            assert list(x) == [0.0]
            assert y[0] == (0 if selected is None or index in selected else engine.physical[index])
        assert not window.curve_checks[1].isChecked()
        assert not window.curves[1][0].isVisible()
        with engine.lock:
            engine.ingest(frame(503))
            window.last_plot = 0
            window.refresh()
        assert list(window.curves[0][0].getData()[0]) == [0.0, 1.0]
    finally:
        window.close()
        deadline = time.monotonic()+4
        while not window.shutdown_done and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.005)
        assert window.shutdown_done
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_quarter_second_ui_poll_and_sample_driven_plot_redraw():
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    engine = window.engine
    info = dict(protocol_version=1, firmware_version='1.1.0',
                board_model='UNO_R4_MINIMA', channel_count=6,
                channels=[f'A{i}' for i in range(6)], adc_bits=14,
                sample_interval_ms=500)
    header = '#HEADER device_time_s,' + ','.join(f'A{i}_raw' for i in range(6)) + ',adc_bits\n'
    frame = lambda t: (f'{t:.3f},' + ','.join(['500'] * 6) + ',14\n').encode()
    calls = []
    original = window.update_plot
    window.update_plot = lambda history: (calls.append(list(history)), original(history))[1]
    try:
        assert UI_REFRESH_INTERVAL_MS == 250
        assert window.timer.interval() == 250
        assert window.timer.timerType() == QtCore.Qt.TimerType.PreciseTimer
        engine.connected = engine.simulated = True
        with engine.lock:
            engine.ingest(('#INFO ' + json.dumps(info) + '\n' + header).encode() + frame(1.0))
        engine.configure([ChannelConfig(True, '拉力')] + [ChannelConfig() for _ in range(5)])
        window.tabs.setCurrentIndex(1)
        window.refresh()
        first_count = len(calls)
        window.refresh()
        assert len(calls) == first_count
        with engine.lock:
            engine.ingest(frame(1.5))
        window.refresh()
        assert len(calls) == first_count + 1
        assert calls[-1][-1][0] == pytest.approx(0.5)
    finally:
        window.close()
        deadline = time.monotonic() + 4
        while not window.shutdown_done and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert window.shutdown_done
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_full_segment_keeps_every_point_peak_and_gap_with_full_range_default():
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    try:
        assert window.range_mode.currentText() == '本段全程'
        assert window.window_sec.value() == 120
        assert window.window_sec.minimum() == 5
        assert window.window_sec.maximum() == 7200
        assert not window.window_sec.isEnabled()
        rows = [(float(i), [0.0, None, None, None, None, None])
                for i in range(12002)]
        rows[1][1][0] = 9000.0
        rows[2][1][0] = None
        rows[3][1][0] = -8000.0
        rows[5][1][0] = None
        window.engine.configs = [ChannelConfig(True, '拉力')] + [ChannelConfig() for _ in range(5)]
        window.rebuild_display()
        window.update_plot(rows)
        x, y = window.curves[0][0].getData()
        assert len(x) == len(rows)
        assert list(x) == [row[0] for row in rows]
        assert x[0] == 0.0
        assert x[-1] == rows[-1][0]
        assert max(value for value in y if math.isfinite(value)) == 9000.0
        assert min(value for value in y if math.isfinite(value)) == -8000.0
        gaps = [index for index, value in enumerate(y) if not math.isfinite(value)]
        assert len(gaps) >= 2
        assert x[gaps[0]] == 2.0
        assert x[gaps[1]] == 5.0
        assert any(math.isfinite(y[index]) for index in range(gaps[0] + 1, gaps[1]))
        assert window.plot.getViewBox().viewRange()[0] == pytest.approx([0, rows[-1][0]])
    finally:
        window.close()
        deadline = time.monotonic() + 4
        while not window.shutdown_done and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert window.shutdown_done
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()
