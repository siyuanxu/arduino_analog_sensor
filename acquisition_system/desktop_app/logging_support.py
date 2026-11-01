"""Non-blocking, application-scoped runtime logging.

The producer side only formats a lightweight LogRecord and puts it on a bounded
queue.  Rotation and all normal disk writes happen on QueueListener's thread.
"""
from collections import deque
import json
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
from queue import Full, Queue
import sys
import threading
import time


NAMESPACE = "shear_acquisition"
LEVELS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_app_logger = logging.getLogger(NAMESPACE)
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
if not any(isinstance(handler, logging.NullHandler) for handler in _app_logger.handlers):
    _app_logger.addHandler(logging.NullHandler())


def _default_log_dir():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "ShearAcquisition" / "logs" / "runtime"
    base = Path(os.environ.get("ACQ_LOCAL_WORK") or (Path.home() / "Desktop" / "codex-space" / ".work" / "projects" / "直剪试验arduino"))
    # Refuse an accidentally cloud-backed override. Runtime logs are local work.
    if any(part.lower() in {"onedrive", "cloudstorage", "icloud", "dropbox"} or "onedrive" in part.lower() for part in base.parts):
        base = Path.home() / "Desktop" / "codex-space" / ".work" / "projects" / "直剪试验arduino"
    return base / "active" / "acquisition-system" / "logs" / "runtime"


class _SafeRotatingHandler(RotatingFileHandler):
    def __init__(self, *args, on_error, **kwargs):
        self._on_error = on_error
        super().__init__(*args, **kwargs)

    def handleError(self, record):
        error = sys.exc_info()[1]
        self._on_error(f"运行日志写入失败：{error or '未知错误'}")
        # Logging failure must never stop acquisition or write to the root logger.


class _RecentHandler(logging.Handler):
    def __init__(self, owner):
        super().__init__(logging.DEBUG)
        self.owner = owner

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception as error:
            self.owner._set_error(f"运行日志格式化失败：{error}")
            return
        with self.owner._state_lock:
            self.owner._recent_records.append((record.levelno, line))


class _BoundedQueueHandler(QueueHandler):
    def __init__(self, queue, owner):
        super().__init__(queue)
        self.owner = owner

    def emit(self, record):
        try:
            self.enqueue(self.prepare(record))
        except Full:
            self.owner._queue_overflow()
        except Exception as error:
            self.owner._set_error(f"运行日志入队失败：{error}")


class RuntimeLogging:
    """Owns one application handler without modifying the process root logger."""

    def __init__(self, log_dir=None, max_bytes=5 * 1024 * 1024, backup_count=5, queue_size=4096):
        self.log_dir = Path(log_dir) if log_dir is not None else _default_log_dir()
        self.path = self.log_dir / "acquisition.log"
        self.settings_path = self.log_dir / "settings.json"
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.queue_size = queue_size
        self.level = self._read_level()
        self.error = None
        self._state_lock = threading.RLock()
        self._started = False
        self._queue = None
        self._queue_handler = None
        self._file_handler = None
        self._recent_handler = None
        self._listener = None
        self._last_overflow_notice = 0.0
        self._dropped = 0
        self._recent_records = deque(maxlen=2000)
        self._settings_write_lock = threading.Lock()
        self._settings_threads = []

    def _read_level(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return "INFO"
            level = str(data.get("level", "INFO")).upper()
            return level if level in LEVELS else "INFO"
        except (OSError, ValueError, TypeError):
            return "INFO"

    def _set_error(self, message):
        with self._state_lock:
            self.error = str(message)

    def _queue_overflow(self):
        now = time.monotonic()
        with self._state_lock:
            self._dropped += 1
            # Keep the problem visible without recursively logging into the full queue.
            if now - self._last_overflow_notice >= 5:
                self.error = f"运行日志队列已满，已丢弃 {self._dropped} 条日志；采集数据未受影响"
                self._last_overflow_notice = now

    @property
    def started(self):
        with self._state_lock:
            return self._started

    def start(self):
        with self._state_lock:
            if self._started:
                return self
            file_handler = recent_handler = queue_handler = listener = None
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
                queue = Queue(maxsize=self.queue_size)
                file_handler = _SafeRotatingHandler(
                    self.path,
                    maxBytes=self.max_bytes,
                    backupCount=self.backup_count,
                    encoding="utf-8",
                    on_error=self._set_error,
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(LEVELS[self.level])
                recent_handler = _RecentHandler(self)
                recent_handler.setFormatter(formatter)
                queue_handler = _BoundedQueueHandler(queue, self)
                queue_handler.setLevel(LEVELS[self.level])
                listener = QueueListener(queue, file_handler, recent_handler, respect_handler_level=True)
                _app_logger.addHandler(queue_handler)
                listener.start()
            except Exception as error:
                self.error = f"无法初始化运行日志：{error}"
                if queue_handler is not None:
                    _app_logger.removeHandler(queue_handler)
                if file_handler is not None:
                    file_handler.close()
                if recent_handler is not None:
                    recent_handler.close()
                return self
            self._queue = queue
            self._queue_handler = queue_handler
            self._file_handler = file_handler
            self._recent_handler = recent_handler
            self._listener = listener
            self._started = True
            self.error = None
        get_logger("runtime").info("运行日志已启动：level=%s path=%s", self.level, self.path)
        return self

    def set_level(self, level):
        value = str(level).upper()
        if value not in LEVELS:
            raise ValueError("日志级别必须是 INFO、WARNING 或 ERROR")
        with self._state_lock:
            self.level = value
            if self._file_handler is not None:
                self._file_handler.setLevel(LEVELS[value])
            if self._queue_handler is not None:
                # Filter before enqueueing so a restrictive level also protects
                # the bounded queue during high-rate acquisition.
                self._queue_handler.setLevel(LEVELS[value])
        writer = threading.Thread(
            target=self._persist_level,
            args=(value,),
            daemon=True,
            name="Runtime log settings",
        )
        with self._state_lock:
            self._settings_threads = [thread for thread in self._settings_threads if thread.is_alive()]
            self._settings_threads.append(writer)
            writer.start()
        get_logger("runtime").warning("运行日志级别已设为 %s", value)

    def _persist_level(self, value):
        """Persist off the GUI thread; stale writers never overwrite a newer choice."""
        with self._settings_write_lock:
            with self._state_lock:
                if value != self.level:
                    return
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                temporary = self.settings_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"level": value}, ensure_ascii=False) + "\n", encoding="utf-8")
                temporary.replace(self.settings_path)
            except OSError as error:
                self._set_error(f"无法保存日志级别：{error}")

    def recent_lines(self, limit=200):
        if limit <= 0:
            return []
        threshold = LEVELS[self.level]
        with self._state_lock:
            records = list(self._recent_records)
        return [line for level, line in records if level >= threshold][-limit:]

    def close(self):
        with self._state_lock:
            if not self._started:
                settings_threads = list(self._settings_threads)
                listener = None
            else:
                queue_handler = self._queue_handler
                listener = self._listener
                file_handler = self._file_handler
                recent_handler = self._recent_handler
                self._started = False
                _app_logger.removeHandler(queue_handler)
        if listener is None:
            for thread in settings_threads:
                thread.join(timeout=2)
            return
        # No producer can enqueue through our handler now. Wait until every
        # accepted record has been processed, then stop the listener before
        # closing any handler it may use.
        try:
            self._queue.join()
            listener.stop()
        except Exception as error:
            # Do not conceal a shutdown fault, but do not close live handlers.
            self._set_error(f"运行日志后台线程关闭失败：{error}")
            thread = getattr(listener, "_thread", None)
            if thread is not None and thread.is_alive():
                thread.join()
        finally:
            file_handler.close()
            recent_handler.close()
            queue_handler.close()
            with self._state_lock:
                self._queue = None
                self._queue_handler = None
                self._file_handler = None
                self._recent_handler = None
                self._listener = None
        with self._state_lock:
            settings_threads = list(self._settings_threads)
        for thread in settings_threads:
            thread.join(timeout=2)


_singleton = None
_singleton_lock = threading.Lock()


def get_runtime_logging():
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = RuntimeLogging()
        return _singleton


def get_logger(component):
    return logging.getLogger(f"{NAMESPACE}.{component}")
