"""Start diagnostics before importing Qt, and flush logs after the UI closes."""
import faulthandler
import platform
import sys
import threading
from pathlib import Path

from . import __version__
from .logging_support import get_logger, get_runtime_logging

logger = get_logger('startup')


class ExceptionDiagnostics:
    """App-scoped Python hooks and a separate native-fault file descriptor."""

    def __init__(self, manager):
        self.manager = manager
        self.previous_sys = None
        self.previous_thread = None
        self.fault_file = None

    def install(self):
        self.previous_sys, self.previous_thread = sys.excepthook, threading.excepthook
        sys.excepthook = self.python_exception
        threading.excepthook = self.thread_exception
        # The native fault handler owns this descriptor until close. It cannot
        # share the rotating log handler's descriptor, which changes on rollover.
        if not faulthandler.is_enabled():
            try:
                path = Path(self.manager.path).with_name('native-crash.log')
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size >= 5 * 1024 * 1024:
                    path.replace(path.with_name('native-crash.previous.log'))
                self.fault_file = path.open('ab')
                faulthandler.enable(self.fault_file, all_threads=True)
                logger.info('原生崩溃诊断已启用：%s', path)
            except OSError:
                logger.exception('无法开启原生崩溃诊断')
                if self.fault_file:
                    self.fault_file.close()
                    self.fault_file = None

    def python_exception(self, exc_type, value, tb):
        logger.error('未捕获的 Python 异常', exc_info=(exc_type, value, tb))
        if self.previous_sys:
            self.previous_sys(exc_type, value, tb)

    def thread_exception(self, args):
        logger.error('未捕获的线程异常：%s', getattr(args.thread, 'name', 'unknown'),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        if self.previous_thread:
            self.previous_thread(args)

    def close(self):
        if self.previous_sys is not None:
            sys.excepthook = self.previous_sys
        if self.previous_thread is not None:
            threading.excepthook = self.previous_thread
        if self.fault_file:
            faulthandler.disable()
            self.fault_file.close()
            self.fault_file = None


def run():
    manager = get_runtime_logging().start()
    diagnostics = ExceptionDiagnostics(manager)
    diagnostics.install()
    previous_qt = None
    qt_core = None
    try:
        logger.info('软件启动：version=%s python=%s platform=%s level=%s',
                    __version__, platform.python_version(), platform.platform(), manager.level)
        from PySide6 import QtCore
        qt_core = QtCore

        def qt_message(kind, context, message):
            levels = {
                QtCore.QtMsgType.QtDebugMsg: 20,
                QtCore.QtMsgType.QtInfoMsg: 20,
                QtCore.QtMsgType.QtWarningMsg: 30,
                QtCore.QtMsgType.QtCriticalMsg: 40,
                QtCore.QtMsgType.QtFatalMsg: 40,
            }
            get_logger('qt').log(levels.get(kind, 30), '%s [%s:%s]', message,
                                 context.file or '', context.line)

        previous_qt = QtCore.qInstallMessageHandler(qt_message)
        logger.info('Qt 版本：%s', QtCore.qVersion())
        from .splash import main
        return main()
    except BaseException:
        logger.exception('软件入口异常退出')
        raise
    finally:
        if qt_core is not None:
            qt_core.qInstallMessageHandler(previous_qt)
        diagnostics.close()
        logger.info('软件已退出，正在刷新运行日志')
        manager.close()
