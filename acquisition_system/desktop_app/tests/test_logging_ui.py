import json
import os
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtGui, QtWidgets as W

from desktop_app.logging_support import RuntimeLogging, get_logger
from desktop_app.startup import ExceptionDiagnostics
from desktop_app.ui import MainWindow


def until(app, predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


def test_logging_controls_filter_file_level_and_visible_failure(tmp_path, monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    manager = RuntimeLogging(tmp_path).start()
    window = MainWindow(logging_manager=manager)
    urls = []
    monkeypatch.setattr(QtGui.QDesktopServices, 'openUrl', lambda url: urls.append(url.toLocalFile()) or True)
    try:
        window.show()
        assert [window.log_level.itemText(i) for i in range(3)] == ['INFO', 'WARNING', 'ERROR']
        window.diagnostic_toggle.setChecked(True)
        logger = get_logger('test.gui')
        logger.info('GUI_INFO_TEST')
        logger.warning('GUI_WARNING_TEST')
        logger.error('GUI_ERROR_TEST')
        until(app, lambda: any('GUI_ERROR_TEST' in line for line in manager.recent_lines()))
        window.refresh()
        assert 'GUI_INFO_TEST' in window.log.toPlainText()
        window.log_level.setCurrentText('WARNING')
        window.refresh()
        assert 'GUI_INFO_TEST' not in window.log.toPlainText()
        assert 'GUI_WARNING_TEST' in window.log.toPlainText()
        window.log_level.setCurrentText('ERROR')
        window.refresh()
        assert 'GUI_WARNING_TEST' not in window.log.toPlainText()
        assert 'GUI_ERROR_TEST' in window.log.toPlainText()
        window.open_log_directory()
        assert urls == [str(tmp_path)]
        assert str(manager.path) in window.log_path_label.toolTip()
        manager._set_error('模拟磁盘已满')
        window.refresh()
        assert window.log_failure_banner.isVisible()
        assert '模拟磁盘已满' in window.log_failure_banner.text()
        assert window.timer.isActive() and not window.engine.recorder
    finally:
        window.close()
        until(app, lambda: window.shutdown_done)
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()
        manager.close()
    assert json.loads(manager.settings_path.read_text())['level'] == 'ERROR'
    assert RuntimeLogging(tmp_path).level == 'ERROR'


def test_python_and_thread_exception_hooks_log_tracebacks_and_restore(tmp_path, monkeypatch):
    manager = RuntimeLogging(tmp_path).start()
    forwarded = []
    prior_sys = lambda *args: forwarded.append('sys')
    prior_thread = lambda args: forwarded.append('thread')
    monkeypatch.setattr(sys, 'excepthook', prior_sys)
    monkeypatch.setattr(threading, 'excepthook', prior_thread)
    # This test checks Python hooks without changing the test runner's native handler.
    monkeypatch.setattr('desktop_app.startup.faulthandler.is_enabled', lambda: True)
    hooks = ExceptionDiagnostics(manager)
    try:
        hooks.install()
        try:
            raise RuntimeError('PYTHON_HOOK_TEST')
        except RuntimeError:
            kind, value, tb = sys.exc_info()
            sys.excepthook(kind, value, tb)
            threading.excepthook(SimpleNamespace(thread=threading.current_thread(),
                                                exc_type=kind, exc_value=value, exc_traceback=tb))
    finally:
        hooks.close()
        manager.close()
    content = manager.path.read_text(encoding='utf-8')
    assert '未捕获的 Python 异常' in content and '未捕获的线程异常' in content
    assert 'Traceback' in content and 'RuntimeError: PYTHON_HOOK_TEST' in content
    assert forwarded == ['sys', 'thread']
    assert sys.excepthook is prior_sys and threading.excepthook is prior_thread
