import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets as W

from desktop_app.models import ChannelConfig
from desktop_app.ui import MainWindow


def prepare_mega(window):
    engine = window.engine
    info = dict(
        protocol_version=1,
        firmware_version='RESPONSIVENESS-TEST',
        board_model='MEGA_2560',
        channel_count=16,
        channels=[f'A{i}' for i in range(16)],
        adc_bits=10,
        sample_interval_ms=1000,
    )
    header = '#HEADER device_time_s,' + ','.join(f'A{i}_raw' for i in range(16)) + ',adc_bits\n'
    engine.connected = engine.simulated = True
    with engine.lock:
        engine.ingest(('#INFO ' + json.dumps(info) + '\n' + header +
                       '1.000,' + ','.join(['123'] * 16) + ',10\n').encode())
    engine.configure([
        ChannelConfig(True, '拉力' if i % 2 == 0 else '水平位移', raw1=1023)
        for i in range(16)
    ])
    window.refresh()


def close_window(window, app):
    window.close()
    deadline = time.monotonic() + 4
    while not window.shutdown_done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert window.shutdown_done
    window.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_hidden_acquisition_page_defers_plot_and_readout_work_until_shown():
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    try:
        prepare_mega(window)
        assert window.tabs.currentIndex() == 0
        calls = {'read_config': 0, 'plot': 0, 'card': 0}

        original_read = window.read_config
        original_plot = window.update_plot

        def read_config():
            calls['read_config'] += 1
            return original_read()

        def update_plot(history):
            calls['plot'] += 1
            return original_plot(history)

        window.read_config = read_config
        window.update_plot = update_plot
        for _, card in window.cards:
            original = card.set_reading

            def set_reading(*args, _original=original, **kwargs):
                calls['card'] += 1
                return _original(*args, **kwargs)

            card.set_reading = set_reading

        for _ in range(20):
            window.refresh()
        assert calls == {'read_config': 0, 'plot': 0, 'card': 0}

        # A real incoming sample still updates the retained history, but does
        # not build a hidden QPainter path or restyle sixteen hidden cards.
        with window.engine.lock:
            window.engine.ingest(('2.000,' + ','.join(['456'] * 16) + ',10\n').encode())
        window.refresh()
        assert calls == {'read_config': 0, 'plot': 0, 'card': 0}
        assert window.plot_refresh_requested
        assert window.plot_history[-1][0] == 1.0

        window.tabs.setCurrentIndex(1)
        app.processEvents()
        assert calls['plot'] == 1
        assert calls['card'] == 16
        assert len(window.curves[0][0].getData()[0]) == len(window.plot_history)
        snapshot = dict(calls)
        for _ in range(20):
            window.refresh()
        assert calls == snapshot
    finally:
        close_window(window, app)


def test_configuration_is_revalidated_only_after_an_editor_changes():
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    try:
        prepare_mega(window)
        calls = 0
        original = window.read_config

        def read_config():
            nonlocal calls
            calls += 1
            return original()

        window.read_config = read_config
        for _ in range(20):
            window.refresh()
        assert calls == 0

        minimum = window.table.cellWidget(0, 4)
        minimum.setText('not-a-number')
        window.refresh()
        assert calls == 1
        assert not window._valid_channels
        window.refresh()
        assert calls == 1

        minimum.setText('0')
        window.refresh()
        assert calls == 2
        assert window._valid_channels
    finally:
        close_window(window, app)


def test_unchanged_250ms_polls_leave_event_loop_headroom():
    app = W.QApplication.instance() or W.QApplication([])
    window = MainWindow()
    window.timer.stop()
    try:
        prepare_mega(window)
        started = time.perf_counter()
        for _ in range(200):
            window.refresh()
            app.processEvents()
        elapsed = time.perf_counter() - started
        # This is deliberately loose for shared CI runners. The pre-fix path
        # repeatedly parsed the full table and restyled all 16 cards and took
        # roughly 0.4 s for the same 200 idle polls on the reference Mac.
        assert elapsed < 0.25
    finally:
        close_window(window, app)
