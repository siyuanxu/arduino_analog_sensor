import json
import logging
from pathlib import Path
import threading
import time
import pytest

from desktop_app.controller import AcquisitionEngine
from desktop_app.logging_support import RuntimeLogging, get_logger
from desktop_app.models import ChannelConfig


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


@pytest.mark.parametrize("payload", ["[]", "null", '"ERROR"', "123", "{invalid json"])
def test_malformed_or_non_object_settings_fall_back_to_info(tmp_path, payload):
    (tmp_path / "settings.json").write_text(payload, encoding="utf-8")
    manager = RuntimeLogging(tmp_path)
    assert manager.level == "INFO"
    assert not manager.started


def test_level_unicode_persistence_and_recent_filter(tmp_path):
    root_handlers = tuple(logging.getLogger().handlers)
    manager = RuntimeLogging(tmp_path).start()
    logger = get_logger("test.level")
    try:
        manager.set_level("WARNING")
        logger.info("不应写入的中文信息")
        logger.warning("警告中文 Ω")
        logger.error("错误中文")
    finally:
        manager.close()

    content = manager.path.read_text(encoding="utf-8")
    assert "不应写入的中文信息" not in content
    assert "警告中文 Ω" in content
    assert "ERROR" in content and "错误中文" in content
    assert "MainThread" in content and "shear_acquisition.test.level" in content
    assert all("不应写入" not in line for line in manager.recent_lines())
    assert json.loads(manager.settings_path.read_text(encoding="utf-8"))["level"] == "WARNING"
    assert RuntimeLogging(tmp_path).level == "WARNING"
    assert tuple(logging.getLogger().handlers) == root_handlers


def test_logging_call_does_not_wait_for_slow_disk_handler(tmp_path):
    manager = RuntimeLogging(tmp_path).start()
    logger = get_logger("test.nonblocking")
    entered, release = threading.Event(), threading.Event()
    original_emit = manager._file_handler.emit

    def slow_emit(record):
        entered.set()
        release.wait(1)
        original_emit(record)

    manager._file_handler.emit = slow_emit
    started = time.monotonic()
    logger.info("慢磁盘模拟")
    elapsed = time.monotonic() - started
    try:
        assert elapsed < .1
        assert entered.wait(1)
    finally:
        release.set()
        manager.close()
    assert "慢磁盘模拟" in manager.path.read_text(encoding="utf-8")


def test_rotation_and_exception_traceback(tmp_path):
    manager = RuntimeLogging(tmp_path, max_bytes=480, backup_count=2).start()
    logger = get_logger("test.rotation")
    try:
        try:
            raise RuntimeError("有意测试异常")
        except RuntimeError:
            logger.exception("带堆栈的异常")
        for index in range(80):
            logger.error("轮转记录 %03d %s", index, "x" * 80)
    finally:
        manager.close()
    files = list(tmp_path.glob("acquisition.log*"))
    assert len(files) >= 2
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "轮转记录 079" in combined
    assert any("带堆栈的异常" in line and "ERROR" in line for line in manager.recent_lines())
    assert any("RuntimeError: 有意测试异常" in line for line in manager.recent_lines())


def test_initialization_failure_is_reported_without_raising(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    manager = RuntimeLogging(blocked).start()
    assert not manager.started
    assert manager.error and "无法初始化运行日志" in manager.error
    get_logger("test.failure").error("日志未启动时也不能影响程序")
    manager.set_level("ERROR")
    manager.close()


def test_runtime_write_and_level_persistence_failures_are_reported(tmp_path):
    manager = RuntimeLogging(tmp_path).start()
    logger = get_logger("test.runtime_failure")
    original_stream = manager._file_handler.stream

    class FailingStream:
        def write(self, _text):
            raise OSError("simulated full disk")

        def flush(self):
            pass

        def close(self):
            pass

    try:
        manager._file_handler.stream = FailingStream()
        logger.error("触发运行中写盘失败")
        assert wait_for(lambda: manager.error and "运行日志写入失败" in manager.error)
        manager._file_handler.stream = original_stream

        manager.error = None
        blocked = tmp_path / "settings-parent"
        blocked.write_text("file", encoding="utf-8")
        manager.settings_path = blocked / "settings.json"
        manager.set_level("WARNING")
        assert wait_for(lambda: manager.error and "无法保存日志级别" in manager.error)
    finally:
        manager._file_handler.stream = original_stream
        manager.close()


def test_engine_emits_handshake_frame_configuration_and_protocol_warning(tmp_path):
    manager = RuntimeLogging(tmp_path).start()
    engine = AcquisitionEngine()
    info = {
        "protocol_version": 1,
        "firmware_version": "TEST-1.0",
        "board_model": "MEGA_2560",
        "channel_count": 16,
        "channels": [f"A{i}" for i in range(16)],
        "adc_bits": 10,
        "sample_interval_ms": 1000,
    }
    header = "device_time_s," + ",".join(f"A{i}_raw" for i in range(16)) + ",adc_bits"
    first = "1.000," + ",".join(str(i) for i in range(16)) + ",10"
    try:
        with engine.lock:
            engine.connected = True
            engine.connection_started = time.monotonic()
            engine.ingest(("#INFO " + json.dumps(info) + "\n#HEADER " + header + "\n" + first + "\n").encode())
        configs = [ChannelConfig(enabled=(i == 0), kind="拉力" if i == 0 else "未配置", raw1=1023) for i in range(16)]
        engine.configure(configs)
        with engine.lock:
            engine.ingest(b"bad,line\n")
            engine.last_received = time.monotonic() - 4
            engine._last_health_check = 0
        engine._check_connection_health()
        second = "2.000," + ",".join(str(i + 1) for i in range(16)) + ",10\n"
        with engine.lock:
            engine.ingest(second.encode(), mono=time.monotonic())
    finally:
        engine.shutdown()
        manager.close()
    content = manager.path.read_text(encoding="utf-8")
    assert "设备握手完成：board=MEGA_2560" in content
    assert "收到首帧有效数据" in content and "raw=(0, 1, 2" in content
    assert "配置应用：board=MEGA_2560 enabled=['A0']" in content
    assert "WARNING" in content and "协议坏帧" in content
    assert "超过3.5秒未收到有效帧" in content
    assert "数据接收恢复：此前状态=有效帧接收超时" in content


def test_handshake_timeout_is_logged_once(tmp_path):
    manager = RuntimeLogging(tmp_path).start()
    engine = AcquisitionEngine()
    try:
        with engine.lock:
            engine.connected = True
            engine.connection_started = time.monotonic() - 9
            engine._last_health_check = 0
        engine._check_connection_health()
        with engine.lock:
            engine._last_health_check = 0
        engine._check_connection_health()
    finally:
        engine.shutdown()
        manager.close()
    content = manager.path.read_text(encoding="utf-8")
    assert content.count("连接超过8秒仍未完成INFO/HEADER及有效首帧握手") == 1
