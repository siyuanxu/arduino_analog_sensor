"""Acquisition consumer and recorder run off the GUI thread, guarded by one lock."""
from collections import deque
from dataclasses import replace
from pathlib import Path
from queue import Queue, Empty, Full
import threading
import time
from .models import ChannelConfig
from .logging_support import get_logger
from .protocol import Parser, LineFramer, Sample, Capabilities, ProtocolError
from .recording import Recorder, now_iso
from .transport import Transport

logger = get_logger("controller")


class AcquisitionEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.queue = Queue(maxsize=256)
        self.closed = threading.Event()
        self.pending_fatal = None
        self.transport = None
        self.parser = Parser()
        self.framer = LineFramer()
        self.cap = None
        self.configs = []
        self.saved = {}
        self.offsets = []
        self.latest = None
        self.latest_received = None
        self.physical = []
        self.connected = False
        self.simulated = False
        self.last_received = None
        self.connection_started = None
        self.recorder = None
        self.last_path = None
        self.last_raw_path = None
        self.record_outcomes = {}
        self.status = "未连接"
        self.messages = deque(maxlen=100)
        # The current visual segment is exact and unbounded. A successful zero
        # or start-record command begins a new segment at t=0.
        self.history = []
        self.plot_epoch = 0
        self.revision = 0
        self.sample_number = 0
        self.bad_frame_count = 0
        self.graph_origin = None
        self._health_warning = None
        self._last_health_check = 0.0
        self.worker = threading.Thread(target=self._run, daemon=True, name="ADC processing and CSV")
        self.worker.start()

    def message(self, text, level="INFO"):
        self.status = text
        self.messages.append(f"{now_iso()} {text}")
        getattr(logger, level.lower())(text)

    def connect(self, port=None, simulated=None, interval=None):
        if interval is None:
            interval = 0.5 if simulated in ("UNO_R4_MINIMA", "UNO_R4_WIFI") else 1.0
        logger.info("连接请求：source=%s port=%s interval=%.3fs", simulated or "SERIAL", port or "-", interval)
        self.disconnect("切换连接")
        with self.lock:
            if self.cap:
                self.saved[self.cap.board_model] = [replace(c) for c in self.configs]
            self.parser, self.framer = Parser(), LineFramer()
            self.cap, self.latest, self.last_received = None, None, None
            self.configs, self.offsets, self.physical = [], [], []
            self.sample_number = 0
            self.bad_frame_count = 0
            self.revision += 1
            self.history.clear()
            self.plot_epoch += 1
            self.graph_origin = None
            self.connected, self.simulated = True, bool(simulated)
            self.connection_started = time.monotonic()
            self._health_warning = None
            self._last_health_check = 0.0
            self.message("SIMULATED 模拟设备连接中" if simulated else "正在连接并等待 INFO / HEADER")
            self.transport = Transport(self.queue, self.fatal, port, simulated, interval)
            self.transport.start()

    def disconnect(self, reason="用户断开连接"):
        logger.info("断开连接：reason=%s", reason)
        with self.lock:
            tr = self.transport
            self.transport = None
            if tr:
                tr.stop()
        if tr and tr is not threading.current_thread():
            tr.join(timeout=2)
        recording_closed = self.stop_recording(reason)
        with self.lock:
            self.connected = False
            self._health_warning = None
            while True:
                try:
                    self.queue.get_nowait()
                except Empty:
                    break
            if recording_closed:
                self.message(reason)

    def fatal(self, text):
        if threading.current_thread() is not self.worker:
            self.pending_fatal = text
            if self.transport:
                self.transport.stop()
            return
        with self.lock:
            self._error(text, stop=True, level="ERROR")
            self.connected = False
            if self.transport:
                self.transport.stop()

    def _error(self, text, stop=False, level="WARNING"):
        if self.recorder:
            try:
                self.recorder.row("error", self.latest, self.physical, self.offsets, text)
            except OSError as e:
                self._disk_failure(e)
                return
            if stop:
                if not self.stop_recording(text):
                    return
        self.message(text, level)

    def _run(self):
        while not self.closed.is_set():
            if self.pending_fatal:
                text, self.pending_fatal = self.pending_fatal, None
                self.fatal(text)
            try:
                kind, payload, received, mono = self.queue.get(timeout=.1)
            except Empty:
                self._check_connection_health()
                continue
            if kind == "command":
                fn, args, kwargs, done, result = payload
                try:
                    result.append((True, fn(self, *args, **kwargs)))
                except Exception as e:
                    logger.exception("采集命令失败：%s", getattr(fn, "__name__", repr(fn)))
                    result.append((False, e))
                finally:
                    done.set()
                continue
            with self.lock:
                if not self.connected:
                    continue
                if kind == "disconnect":
                    self.fatal(payload)
                else:
                    self.ingest(payload, received, mono)

    def ingest(self, payload, received=None, mono=None):
        """Caller holds lock; public for deterministic device fixtures."""
        received, mono = received or now_iso(), mono or time.monotonic()
        for line in self.framer.feed(payload):
            try:
                if isinstance(line, ProtocolError):
                    raise line
                item = self.parser.parse(line)
                if isinstance(item, Capabilities) and self.cap is None:
                    self.cap = item
                    self.configs = self.saved.get(item.board_model, [ChannelConfig(raw1=(1 << item.adc_bits)-1) for _ in item.channels])
                    self.offsets = [0.0] * item.channel_count
                    self.physical = [None] * item.channel_count
                    self.revision += 1
                    logger.info(
                        "设备握手完成：board=%s firmware=%s channels=%d adc_bits=%d interval_ms=%d",
                        item.board_model, item.firmware_version, item.channel_count,
                        item.adc_bits, item.sample_interval_ms,
                    )
                    self.message(f"{'SIMULATED · ' if self.simulated else ''}{item.board_model} · {item.channel_count} 路 · {item.adc_bits} bit；等待有效数据")
                if isinstance(item, Sample):
                    if self.graph_origin is None:
                        self.graph_origin = item.device_time_s
                    if self.latest and item.device_time_s-self.latest.device_time_s > self.expected_interval_seconds * 1.5:
                        self.history.append((self.latest.device_time_s-self.graph_origin+.001, [None]*self.cap.channel_count))
                        self._error(f"设备时间间隔 {item.device_time_s-self.latest.device_time_s:.3f} s：疑似缺帧，不能据此精确统计")
                    self.latest = item
                    self.latest_received = received
                    self.last_received = mono
                    measurement_counts = item.measurement_counts
                    self.physical = [c.convert(raw, item.adc_bits) if c.enabled else None for c, raw in zip(self.configs, measurement_counts)]
                    zeroed = [p-o if p is not None else None for p, o in zip(self.physical, self.offsets)]
                    self.sample_number += 1
                    if self._health_warning is not None:
                        logger.info("数据接收恢复：此前状态=%s device_time=%.3f", self._health_warning, item.device_time_s)
                        self._health_warning = None
                    if self.sample_number == 1:
                        logger.info("收到首帧有效数据：device_time=%.3f raw=%s", item.device_time_s, item.raw)
                    elif self.sample_number % 60 == 0:
                        logger.info(
                            "采集周期状态：frames=%d bad_frames=%d device_time=%.3f raw=%s",
                            self.sample_number, self.bad_frame_count, item.device_time_s, item.raw,
                        )
                    self.history.append((item.device_time_s-self.graph_origin, zeroed))
                    if self.recorder:
                        try:
                            self.recorder.row("sample", item, self.physical, self.offsets, received=received)
                        except OSError as e:
                            self._disk_failure(e)
            except ProtocolError as e:
                self.bad_frame_count += 1
                if e.fatal:
                    logger.error("致命协议错误：%s", e)
                else:
                    logger.warning("协议坏帧：%s", e)
                if e.fatal:
                    self.fatal(str(e))
                    break
                self._error(str(e), level="WARNING")

    def _check_connection_health(self):
        """Log state transitions only; UI/recording behavior remains unchanged."""
        now = time.monotonic()
        if now - self._last_health_check < min(1.0, self.expected_interval_seconds):
            return
        self._last_health_check = now
        with self.lock:
            if not self.connected:
                self._health_warning = None
                return
            warning = None
            if self.connection_started is not None and now - self.connection_started > 8 and (not self.parser.ready or self.latest is None):
                warning = "握手超时"
            elif self.latest is not None and self.last_received is not None and now - self.last_received > self.stale_timeout_seconds:
                warning = "有效帧接收超时"
            if warning and warning != self._health_warning:
                if warning == "握手超时":
                    logger.warning("连接超过8秒仍未完成INFO/HEADER及有效首帧握手")
                else:
                    logger.warning(
                        "超过%g秒未收到有效帧：last_device_time=%.3f frames=%d bad_frames=%d",
                        self.stale_timeout_seconds, self.latest.device_time_s,
                        self.sample_number, self.bad_frame_count,
                    )
                self._health_warning = warning

    def _set_record_outcome(self, path, state):
        self.record_outcomes[str(Path(path).resolve())] = state
        while len(self.record_outcomes) > 20:
            self.record_outcomes.pop(next(iter(self.record_outcomes)))

    def _disk_failure(self, error):
        logger.error("CSV磁盘写入失败", exc_info=(type(error), error, error.__traceback__))
        rec, self.recorder = self.recorder, None
        if rec:
            for path in (rec.raw_path, rec.path):
                self._set_record_outcome(path, "写入失败；文件可能只有已成功写入部分")
            try:
                rec.close()
            except OSError as close_error:
                logger.error("写入失败后关闭配对CSV仍失败：raw=%s result=%s error=%s", rec.raw_path, rec.path, close_error)
            logger.error("配对CSV可能不完整：raw=%s result=%s", rec.raw_path, rec.path)
        self.message(f"磁盘写入失败，本次记录已停止：{error}；原始与成果文件可能只有已成功写入部分", "ERROR")

    def configure(self, configs):
        with self.lock:
            if self.recorder:
                raise ValueError("记录期间配置已锁定")
            if not self.cap or len(configs) != self.cap.channel_count:
                raise ValueError("尚未识别设备")
            for c in configs:
                c.validate(self.cap.adc_bits)
            self.configs = [replace(c) for c in configs]
            self.offsets = [0.] * len(configs)
            self.history.clear()
            self.plot_epoch += 1
            self.graph_origin = self.latest.device_time_s if self.latest else None
            self.physical = [c.convert(r, self.cap.adc_bits) if c.enabled else None for c, r in zip(configs, self.latest.measurement_counts)] if self.latest else [None]*len(configs)
            self.saved[self.cap.board_model] = [replace(c) for c in configs]
            self.revision += 1
            logger.info("配置应用：board=%s enabled=%s", self.cap.board_model, [f"A{i}" for i, c in enumerate(configs) if c.enabled])
            self.message("配置已应用；清零偏置和图形历史已重置")

    def start_recording(self, path, experiment_name=""):
        with self.lock:
            if self.recorder:
                raise ValueError("正在记录")
            if not self.connected or not self.latest or not self.parser.ready or self.stale:
                raise ValueError("需要已连接设备及新鲜有效数据")
            if not any(c.enabled for c in self.configs):
                raise ValueError("至少启用并配置一个传感器")
            for c in self.configs:
                c.validate(self.cap.adc_bits)
            if self.simulated and "SIMULATED" not in Path(path).name:
                raise ValueError("模拟文件名必须包含 SIMULATED")
            self.recorder = Recorder(path, self.cap, self.configs, self.offsets, self.simulated, self.latest.device_time_s, experiment_name)
            self.last_path = self.recorder.path
            self.last_raw_path = self.recorder.raw_path
            try:
                self.recorder.row("start", self.latest, self.physical, self.offsets, "开始记录", self.latest_received)
                self.recorder.row("sample", self.latest, self.physical, self.offsets, received=self.latest_received)
            except OSError as e:
                self._disk_failure(e)
                raise
            # Only a successfully opened and initialized recording is a new
            # visual experiment segment. Disk failure preserves the preview.
            # Existing zero offsets remain active; only time/history restart.
            self.graph_origin = self.latest.device_time_s
            self.history.clear()
            self.history.append((0.0, [p-o if p is not None else None for p,o in zip(self.physical,self.offsets)]))
            self.plot_epoch += 1
            self._set_record_outcome(self.recorder.raw_path, "正在记录")
            self._set_record_outcome(self.recorder.path, "正在记录")
            logger.info(
                "配对CSV记录开始：raw=%s result=%s experiment=%s source=%s",
                self.recorder.raw_path, self.recorder.path, experiment_name or "-",
                "SIMULATED" if self.simulated else "SERIAL",
            )
            self.message(f"正在记录：成果 {self.recorder.path}；原始 {self.recorder.raw_path}")

    @property
    def expected_interval_seconds(self):
        return self.cap.sample_interval_ms / 1000.0 if self.cap else 1.0

    @property
    def stale_timeout_seconds(self):
        return max(1.0, self.expected_interval_seconds * 3.5)

    @property
    def stale(self):
        return self.last_received is None or time.monotonic()-self.last_received > self.stale_timeout_seconds

    def zero(self, indices=None):
        with self.lock:
            if not self.connected or not self.latest or self.stale:
                raise ValueError("无新鲜有效数据，无法清零")
            indices = list(range(len(self.configs))) if indices is None else list(indices)
            if any(type(i) is not int or not 0 <= i < len(self.configs) for i in indices):
                raise ValueError("所选通道索引无效")
            affected_indices = list(dict.fromkeys(
                i for i in indices
                if self.configs[i].enabled and self.physical[i] is not None
            ))
            if not affected_indices:
                raise ValueError("所选通道没有启用的传感器")
            for i in affected_indices:
                self.offsets[i] = self.physical[i]
            affected = [f"A{i}" for i in affected_indices]
            zeroed = [p-o if p is not None else None for p,o in zip(self.physical,self.offsets)]
            # A successful zero starts a new visual epoch. CSV/device time and
            # Recorder.start_time remain continuous and are intentionally untouched.
            self.graph_origin = self.latest.device_time_s
            self.history.clear()
            self.history.append((0.0, zeroed))
            self.plot_epoch += 1
            text = "清零：" + ", ".join(affected)
            if self.recorder:
                try:
                    self.recorder.row("zero", self.latest, self.physical, self.offsets, text)
                except OSError as e:
                    self._disk_failure(e)
                    logger.warning('清零已应用，事件保存失败，记录已停止：channels=%s', affected)
                    return
            logger.info("通道清零：channels=%s offsets=%s", affected, [self.offsets[int(name[1:])] for name in affected])
            self.message(text)

    def stop_recording(self, reason="用户结束记录"):
        """Close both files; false preserves the failure status for callers."""
        with self.lock:
            rec, self.recorder = self.recorder, None
            if rec:
                try:
                    rec.row("stop", self.latest, self.physical, self.offsets, reason)
                    rec.close()
                    for path in (rec.raw_path, rec.path):
                        self._set_record_outcome(path, "已结束："+reason)
                    logger.info("配对CSV记录结束：raw=%s result=%s reason=%s", rec.raw_path, rec.path, reason)
                    self.message(f"记录已结束：{reason}；成果 {rec.path}；原始 {rec.raw_path}")
                except OSError as e:
                    logger.error("配对CSV结束写入失败：raw=%s result=%s", rec.raw_path, rec.path, exc_info=(type(e), e, e.__traceback__))
                    try:
                        rec.close()
                    except OSError:
                        pass
                    for path in (rec.raw_path, rec.path):
                        self._set_record_outcome(path, "结束写入失败；文件完整性未验证")
                    self.message(f"结束写入失败：{e}；请检查成果 {rec.path} 与原始 {rec.raw_path}", "ERROR")
                    return False
            return True

    def shutdown(self):
        self.disconnect("软件关闭")
        self.closed.set()
        self.worker.join(timeout=2)


def _on_consumer(fn):
    """Order UI commands with queued samples; all CSV I/O stays on consumer."""
    def call(self, *args, **kwargs):
        if threading.current_thread() is self.worker:
            return fn(self, *args, **kwargs)
        if self.closed.is_set() or not self.worker.is_alive():
            raise RuntimeError("采集处理线程已停止，命令未提交")
        done, result = threading.Event(), []
        try:
            self.queue.put(("command", (fn, args, kwargs, done, result), now_iso(), time.monotonic()), timeout=2)
        except Full as e:
            logger.error("采集命令队列已满：command=%s", getattr(fn, "__name__", repr(fn)))
            raise RuntimeError("命令队列已满，命令未提交；请稍后重试") from e
        # Once accepted, wait for the actual result; never report timeout and execute later.
        while not done.wait(.2):
            if not self.worker.is_alive():
                raise RuntimeError("采集处理线程异常停止，未能确认命令完成")
        ok, value = result[0]
        if not ok:
            raise value
        return value
    return call


for _name in ("configure", "start_recording", "zero", "stop_recording"):
    setattr(AcquisitionEngine, _name, _on_consumer(getattr(AcquisitionEngine, _name)))
