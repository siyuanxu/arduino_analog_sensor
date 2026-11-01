import json
import os
from pathlib import Path
import time
import math
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6 import QtCore, QtGui, QtWidgets as W
from desktop_app.models import ChannelConfig
from desktop_app.ui import MainWindow


@pytest.fixture
def window():
    app = W.QApplication.instance() or W.QApplication([])
    app.setFont(QtGui.QFont('Arial', 10))
    w = MainWindow()
    w.timer.stop()
    w.resize(1280, 720)
    w.show()
    yield w, app
    w.close()
    deadline = time.monotonic()+4
    while not w.shutdown_done and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert w.shutdown_done
    w.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def prepare(window, enabled=2, channels=16):
    engine = window.engine
    info = dict(protocol_version=1, firmware_version='SIMULATED-1.0.0',
                board_model='MEGA_2560' if channels == 16 else 'UNO_R4_MINIMA',
                channel_count=channels, channels=[f'A{i}' for i in range(channels)],
                adc_bits=10 if channels == 16 else 14, sample_interval_ms=1000)
    bits = info['adc_bits']
    with engine.lock:
        engine.connected = engine.simulated = True
        engine.ingest(('#INFO '+json.dumps(info)+'\n#HEADER device_time_s,'+
                       ','.join(f'A{i}_raw' for i in range(channels))+',adc_bits\n'+
                       '1.000,'+','.join(['123']*channels)+f',{bits}\n').encode())
    engine.configure([ChannelConfig(i < enabled, '拉力' if i % 2 == 0 else '水平位移', raw1=(1<<bits)-1)
                      for i in range(channels)])
    with engine.lock:
        window.refresh()


@pytest.mark.parametrize('force,displacement', [
    ([1000, 20000], [.1, 10]), ([-20000, -1000], [-10, -.1]),
    ([1000, 20000], [-10, -.1]), ([-3, 20], [-100, 2]),
    ([123, 123], [7, 7]), ([0, 0], [0, 0]),
])
def test_zero_is_aligned_in_scene_pixels_after_range_change_and_resize(window, force, displacement):
    w, app = window
    prepare(w)
    w.tabs.setCurrentIndex(1)
    history = [(float(i), [f, d]+[None]*14) for i, (f, d) in enumerate(zip(force, displacement))]
    for size in [(1280,720), (1100,680), (1440,900), (1280,720)]:
        w.resize(*size)
        w.update_plot(history)
        app.processEvents()
        w.resize_plot()
        left, right = w.plot.getViewBox(), w.right
        left_zero = left.mapViewToScene(QtCore.QPointF(0,0)).y()
        right_zero = right.mapViewToScene(QtCore.QPointF(0,0)).y()
        assert abs(left_zero-right_zero) <= 1
        assert not left.state['mouseEnabled'][1] and not right.state['mouseEnabled'][1]
        assert not left.menuEnabled() and not right.menuEnabled()
        assert not left.state['autoRange'][1] and not right.state['autoRange'][1]
        for values, view in ((force,left), (displacement,right)):
            low, high = view.viewRange()[1]
            assert low <= min(values) <= max(values) <= high
    w.curve_checks[1].setChecked(False)
    w.plot_history = history
    w.update_plot(history)
    app.processEvents()
    assert abs(left.mapViewToScene(QtCore.QPointF(0,0)).y()-right.mapViewToScene(QtCore.QPointF(0,0)).y()) <= 1
    w.update_plot([])
    assert left.viewRange()[1] == pytest.approx(right.viewRange()[1])


@pytest.mark.parametrize('enabled,channels', [(2,16),(6,6),(16,16)])
def test_all_pages_fit_720p_and_readouts_are_visible_without_scrolling(window, tmp_path, enabled, channels):
    w, app = window
    prepare(w, enabled, channels)
    w.experiment_name.setText('SIMULATED_低分辨率布局验证')
    w.set_record_target(tmp_path / ('SIMULATED_'+'长试验文件名'*10+'.csv'))
    deadline = time.monotonic()+3
    while not w.target_ready and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert w.target_ready
    # 640px client height also leaves room for Windows title/task bars on 720p.
    for height in (720, 640):
        for page in range(3):
            w.tabs.setCurrentIndex(page)
            w.resize(1280,height)
            app.processEvents()
            assert w.width() <= 1280 and w.height() <= height
            assert w.minimumSizeHint().height() <= height
    w.resize(1280,720)
    w.tabs.setCurrentIndex(0)
    w.diagnostic_toggle.setChecked(True)
    app.processEvents()
    assert w.height() <= 720 and w.config_scroll.viewport().height() > 300
    w.diagnostic_toggle.setChecked(False)
    w.tabs.setCurrentIndex(2)
    w.delivery_details_toggle.setChecked(True)
    app.processEvents()
    assert w.height() <= 720
    w.delivery_details_toggle.setChecked(False)
    w.tabs.setCurrentIndex(1)
    app.processEvents()
    w.relayout_readouts()
    app.processEvents()
    assert w.card_area.height() <= (78 if enabled == 16 else 40)
    assert not w.card_area.verticalScrollBar().isVisible()
    viewport = w.card_area.viewport()
    for _, card in w.cards:
        position = card.mapTo(viewport, QtCore.QPoint(0,0))
        bounds = QtCore.QRect(position, card.size())
        assert viewport.rect().adjusted(-1,-1,1,1).contains(bounds)
        assert card.height() == 34 and card.width() <= 220
        assert 'raw' not in card.text() and card.toolTip()
    assert w.plot.height() >= 300
    assert w.start_btn.geometry().bottom() <= w.start_btn.parentWidget().height()
    qa = Path(os.environ.get('ACQ_QA_DIR', str(tmp_path)))
    qa.mkdir(parents=True, exist_ok=True)
    w.grab().save(str(qa / f'layout_720p_{enabled}channels_dpr{app.devicePixelRatio():g}.png'))


def test_extreme_plot_range_is_reported_without_propagating_exception(window):
    w, _ = window
    prepare(w)
    w.apply_aligned_y_ranges({'N': [-1e308,1e308], 'mm': []})
    assert '绘图范围异常' in w.status_label.text()
    assert w._last_plot_error
    w.refresh()
    assert '绘图范围异常' in w.status_label.text()
    w.apply_aligned_y_ranges({'N': [1,2], 'mm': [3,4]})
    assert w._last_plot_error is None


def test_range_switch_is_immediate_lossless_and_uses_visible_y_ranges(window):
    w, app = window
    prepare(w)
    w.tabs.setCurrentIndex(1)
    # Old extremes must return in full mode but must not control the recent view.
    history = []
    for second in range(301):
        force = 10000.0 if second == 10 else float((second % 7) - 3)
        displacement = -500.0 if second == 20 else float((second % 5) - 2) / 10
        history.append((float(second), [force, displacement] + [None] * 14))
    history[250][1][0] = None
    history[260][1][1] = None
    history[290][1][0] = 25.0

    w.plot_history = history
    w.update_plot(history)
    full_x, full_force = w.curves[0][0].getData()
    assert len(full_x) == len(history)
    assert list(full_x) == [row[0] for row in history]
    assert math.isnan(full_force[250])
    assert w.plot.getViewBox().viewRange()[0] == pytest.approx([0, 300])

    w.range_mode.setCurrentIndex(1)
    app.processEvents()
    recent_x, recent_force = w.curves[0][0].getData()
    recent_displacement = w.curves[1][0].getData()[1]
    assert w.window_sec.isEnabled()
    assert list(recent_x) == [row[0] for row in history[180:]]
    assert list(recent_force[np.isfinite(recent_force)]) == [
        row[1][0] for row in history[180:] if row[1][0] is not None
    ]
    assert math.isnan(recent_force[70])
    assert math.isnan(recent_displacement[80])
    assert w.plot.getViewBox().viewRange()[0] == pytest.approx([180, 300])
    # The old full-history outliers are outside the current automatic ranges.
    assert w.plot.getViewBox().viewRange()[1][1] < 100
    assert w.right.viewRange()[1][0] > -10
    left_zero = w.plot.getViewBox().mapViewToScene(QtCore.QPointF(0, 0)).y()
    right_zero = w.right.mapViewToScene(QtCore.QPointF(0, 0)).y()
    assert abs(left_zero - right_zero) <= 1

    # Changing seconds redraws immediately even though no sample was appended.
    w.window_sec.setValue(30)
    app.processEvents()
    assert list(w.curves[0][0].getData()[0]) == [row[0] for row in history[270:]]

    # Switching back recovers every original point, spike and gap.
    w.range_mode.setCurrentIndex(0)
    app.processEvents()
    restored_x, restored_force = w.curves[0][0].getData()
    assert not w.window_sec.isEnabled()
    assert list(restored_x) == list(full_x)
    assert np.array_equal(restored_force, full_force, equal_nan=True)
    assert max(restored_force[np.isfinite(restored_force)]) == 10000.0


def test_recent_window_passes_only_tail_while_full_mode_remains_all_points(window):
    w, app = window
    prepare(w)
    history = [(index * 0.5, [float(index), float(index) / 10] + [None] * 14)
               for index in range(100001)]
    w.plot_history = history
    w.update_plot(history)
    assert len(w.curves[0][0].getData()[0]) == 100001
    assert w.plot_buffer_length == 100001

    w.range_mode.setCurrentIndex(1)
    app.processEvents()
    x = w.curves[0][0].getData()[0]
    assert len(x) == 241
    assert x[0] == pytest.approx(history[-1][0] - 120)
    assert x[-1] == pytest.approx(history[-1][0])
    # View changes never truncate the synchronized full-history buffer.
    assert w.plot_buffer_length == len(history)
    assert w.plot_buffer_source is history

    w.range_mode.setCurrentIndex(0)
    app.processEvents()
    assert len(w.curves[0][0].getData()[0]) == len(history)
