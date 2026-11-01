import time
import pytest
from desktop_app.tests.test_zero_history import make_engine, frame


@pytest.mark.parametrize('operation', ['zero', 'disconnect', 'fatal_stop'])
def test_recording_write_failure_is_not_replaced_by_success_or_disconnect(tmp_path, operation):
    engine = make_engine(16, 10)
    try:
        engine.start_recording(tmp_path/f'SIMULATED_{operation}.csv')
        recorder = engine.recorder
        original = recorder.raw_writer

        class FaultWriter:
            def writerow(self, row):
                target = 'zero' if operation == 'zero' else 'stop'
                if row['record_type'] == target:
                    raise OSError('regression: disk full during '+target)
                original.writerow(row)

        recorder.raw_writer = FaultWriter()
        if operation == 'zero':
            engine.zero([0])
            # The live zero is applied, but its failed event write remains visible.
            assert engine.offsets[0] == engine.physical[0]
            assert '磁盘写入失败' in engine.status and '记录已停止' in engine.status
            with engine.lock:
                engine.ingest(frame(101, list(range(16)), 10))
            assert '磁盘写入失败' in engine.status
        elif operation == 'disconnect':
            engine.disconnect('用户断开连接')
            assert not engine.connected and '结束写入失败' in engine.status
        else:
            engine.queue.put(('disconnect', 'QA串口意外断开', None, time.monotonic()))
            deadline = time.monotonic()+3
            while engine.connected and time.monotonic()<deadline:
                time.sleep(.005)
            assert not engine.connected and '结束写入失败' in engine.status
        assert engine.recorder is None
        assert recorder.raw_file.closed and recorder.result_file.closed
        assert all('失败' in outcome for outcome in engine.record_outcomes.values())
        assert '失败' in engine.messages[-1]
    finally:
        engine.shutdown()
