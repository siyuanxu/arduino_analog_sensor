import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets as W

import desktop_app.ui as ui


def device(instance='USB\\VID_2341&PID_0069', label='Arduino UNO R4 Minima'):
    return SimpleNamespace(board_model='UNO_R4_MINIMA', label=label,
                           instance_id=instance, name=label, problem_code=28,
                           role='runtime', repairable=True)


def spin(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


def close_window(app, window):
    window.close()
    spin(app, lambda: window.shutdown_done)
    window.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_one_code28_device_prompts_once_and_cancel_can_be_retried(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    target = device()
    installs = []
    scan = SimpleNamespace(available=True, devices=(target,), error='')
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(ui.windows_drivers, 'scan_windows_drivers', lambda: scan)

    def repair(selected):
        installs.append(selected.instance_id)
        return SimpleNamespace(success=False, cancelled=True,
                               message='用户取消了 Windows 授权', device=selected)

    monkeypatch.setattr(ui.windows_drivers, 'repair_windows_driver', repair)
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: len(installs) == 1 and not window.driver_install_pending)
        assert '已取消' in window.driver_status.text()
        assert not window.driver_install_btn.isHidden()
        window.check_windows_drivers()
        spin(app, lambda: not window.driver_check_pending)
        assert installs == [target.instance_id]
        window.driver_install_btn.click()
        spin(app, lambda: len(installs) == 2 and not window.driver_install_pending)
    finally:
        close_window(app, window)


def test_multiple_code28_devices_require_selection(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    first = device('USB\\VID_2341&PID_0069\\ONE', 'R4 一')
    second = device('USB\\VID_2341&PID_0069\\TWO', 'R4 二')
    scan = SimpleNamespace(available=True, devices=(first, second), error='')
    installs = []
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(ui.windows_drivers, 'scan_windows_drivers', lambda: scan)
    monkeypatch.setattr(
        ui.windows_drivers, 'repair_windows_driver',
        lambda selected: installs.append(selected.instance_id) or
        SimpleNamespace(success=False, cancelled=True, message='取消', device=selected))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_check_pending and window.driver_device.count() == 2)
        assert not installs
        assert '多个' in window.driver_status.text()
        assert not window.driver_device.isHidden()
        window.driver_device.setCurrentIndex(1)
        window.driver_install_btn.click()
        spin(app, lambda: bool(installs) and not window.driver_install_pending)
        assert installs == [second.instance_id]
    finally:
        close_window(app, window)


def test_driver_poll_does_not_run_during_connection(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    calls = []
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: calls.append(True) or SimpleNamespace(available=True, devices=(), error=''))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_check_pending)
        initial = len(calls)
        with window.engine.lock:
            window.engine.connected = True
        window.check_windows_drivers(force=True)
        app.processEvents()
        assert len(calls) == initial
    finally:
        with window.engine.lock:
            window.engine.connected = False
        close_window(app, window)


def test_device_change_events_are_debounced_without_periodic_poll(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    calls = []
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: calls.append(True) or SimpleNamespace(available=True, devices=(), error=''))
    window = ui.MainWindow()
    window.driver_timer.stop()
    window.driver_timer.setInterval(10)
    try:
        spin(app, lambda: not window.driver_check_pending)
        initial = len(calls)
        window.windows_device_changed()
        window.windows_device_changed()
        spin(app, lambda: len(calls) == initial + 1 and not window.driver_check_pending)
        deadline = time.monotonic() + .05
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert len(calls) == initial + 1
    finally:
        close_window(app, window)


def test_healthy_hotplug_refreshes_com_port(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    ports = []
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: list(ports))
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: SimpleNamespace(available=True, devices=(), error=''))
    window = ui.MainWindow()
    window.driver_timer.stop()
    window.driver_timer.setInterval(10)
    try:
        spin(app, lambda: not window.driver_check_pending)
        ports.append(dict(device='COM7', description='Arduino', manufacturer='Arduino',
                          vid=0x2341, pid=0x0069, serial_number='abc', hwid='USB'))
        window.windows_device_changed()
        spin(app, lambda: window.port.findData('COM7') >= 0)
        assert window.port.currentData() == 'COM7'
    finally:
        close_window(app, window)


def test_device_event_during_scan_requests_fresh_scan(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])

    def scan():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            release.wait(2)
        return SimpleNamespace(available=True, devices=(), error='')

    monkeypatch.setattr(ui.windows_drivers, 'scan_windows_drivers', scan)
    window = ui.MainWindow()
    window.driver_timer.setInterval(10)
    try:
        assert entered.wait(1)
        window.windows_device_changed()
        deadline = time.monotonic() + .1
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert window.driver_rescan_requested
        release.set()
        spin(app, lambda: len(calls) == 2 and not window.driver_check_pending)
    finally:
        release.set()
        close_window(app, window)


def test_staged_mega_result_is_not_reported_as_uninstalled(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    mega = SimpleNamespace(board_model='MEGA_2560', label='Arduino Mega 2560',
                           instance_id='USB\\VID_2341&PID_0042\\ABC', name='Mega',
                           problem_code=28, role='runtime', repairable=True)
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: SimpleNamespace(available=True, devices=(mega,), error=''))
    monkeypatch.setattr(
        ui.windows_drivers, 'repair_windows_driver',
        lambda selected: SimpleNamespace(success=False, cancelled=True,
                                         message='官方驱动已加入，刷新授权取消',
                                         device=selected, staged=True,
                                         restart_required=True))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_install_pending and
             '驱动已加入 Windows' in window.driver_status.text())
        assert '未安装' not in window.driver_status.text()
        assert '重启' in window.driver_status.text()
        assert window.driver_install_btn.isHidden()
    finally:
        close_window(app, window)


def test_replug_result_does_not_offer_repeated_install(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    mega = SimpleNamespace(board_model='MEGA_2560', label='Arduino Mega 2560',
                           instance_id='USB\\VID_2341&PID_0042\\ABC', name='Mega',
                           problem_code=28, role='runtime', repairable=True)
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: SimpleNamespace(available=True, devices=(mega,), error=''))
    monkeypatch.setattr(
        ui.windows_drivers, 'repair_windows_driver',
        lambda selected: SimpleNamespace(success=False, cancelled=False,
                                         message='驱动已加入，请重新插拔',
                                         device=selected, staged=True,
                                         restart_required=False,
                                         replug_required=True))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_install_pending and
             '重新插拔' in window.driver_status.text())
        assert window.driver_install_btn.isHidden()
    finally:
        close_window(app, window)


def test_changed_problem_code_uses_current_device_and_hides_install(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    missing = SimpleNamespace(board_model='MEGA_2560', label='Arduino Mega 2560',
                              instance_id='USB\\VID_2341&PID_0042\\ABC', name='Mega',
                              problem_code=28, role='runtime', repairable=True)
    fault = SimpleNamespace(**{**vars(missing), 'problem_code': 10, 'repairable': False})
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: SimpleNamespace(available=True, devices=(missing,), error=''))
    monkeypatch.setattr(
        ui.windows_drivers, 'repair_windows_driver',
        lambda selected: SimpleNamespace(success=False, cancelled=False,
                                         message='处理后设备报告错误代码 10',
                                         device=fault, staged=True,
                                         restart_required=False,
                                         replug_required=False))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_install_pending and
             '设备异常' in window.driver_status.text())
        assert window.driver_install_btn.isHidden()
        assert not window.driver_devices
    finally:
        close_window(app, window)


def test_scan_reports_non_driver_runtime_fault_without_install(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    fault = SimpleNamespace(board_model='UNO_R4_MINIMA', label='Arduino UNO R4 Minima',
                            instance_id='USB\\VID_2341&PID_0069&MI_00\\ABC', name='R4',
                            problem_code=31, role='runtime', repairable=False)
    monkeypatch.setattr(ui, 'IS_WINDOWS', True)
    monkeypatch.setattr(ui, 'enumerate_ports', lambda: [])
    monkeypatch.setattr(
        ui.windows_drivers, 'scan_windows_drivers',
        lambda: SimpleNamespace(available=True, devices=(fault,), error=''))
    window = ui.MainWindow()
    window.driver_timer.stop()
    try:
        spin(app, lambda: not window.driver_check_pending and
             '设备异常' in window.driver_status.text())
        assert '错误代码 31' in window.driver_status.toolTip()
        assert window.driver_install_btn.isHidden()
    finally:
        close_window(app, window)
