import csv
import json
from dataclasses import replace
import time
import pytest
from desktop_app.protocol import Parser, LineFramer, ProtocolError, Sample
from desktop_app.models import ChannelConfig, labels, save_config, load_config
from desktop_app.controller import AcquisitionEngine
from desktop_app.recording import recording_paths


def handshake(n=6, bits=14, interval_ms=1000):
    info = dict(protocol_version=1, firmware_version='1.0.0', board_model='MEGA_2560' if n == 16 else 'UNO_R4_MINIMA', channel_count=n, channels=[f'A{i}' for i in range(n)], adc_bits=bits, sample_interval_ms=interval_ms)
    return '#INFO '+json.dumps(info)+'\n#HEADER device_time_s,'+','.join(f'A{i}_raw' for i in range(n))+',adc_bits\n'


def frame(t=1, n=6, bits=14, raw=123):
    return f'{t:.3f},'+','.join([str(raw)]*n)+f',{bits}\n'


def wait_for(predicate, seconds=3):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'condition timed out'


@pytest.mark.parametrize('n,bits', [(6,14),(16,10)])
def test_protocol_all_channels(n,bits):
    p = Parser()
    for line in handshake(n,bits).splitlines():
        p.parse(line)
    sample = p.parse(frame(n=n,bits=bits,raw=(1<<bits)-1).strip())
    assert sample == Sample(1., ((1<<bits)-1,)*n, bits)
    for line in handshake(n,bits).splitlines():
        p.parse(line)
    with pytest.raises(ProtocolError, match='倒退/重复') as e:
        p.parse(frame(n=n,bits=bits).strip())
    assert e.value.fatal


@pytest.mark.parametrize('interval_ms', [500, 1000])
def test_r4_accepts_current_and_legacy_sample_intervals(interval_ms):
    parser = Parser()
    for line in handshake(interval_ms=interval_ms).splitlines():
        parser.parse(line)
    assert parser.capabilities.sample_interval_ms == interval_ms


@pytest.mark.parametrize('n,bits,interval_ms', [(6, 14, 250), (16, 10, 500)])
def test_board_rejects_unsupported_sample_interval(n, bits, interval_ms):
    parser = Parser()
    with pytest.raises(ProtocolError, match='能力参数与协议不符'):
        parser.parse(handshake(n, bits, interval_ms).splitlines()[0])


@pytest.mark.parametrize('line', ['nan,0,0,0,0,0,0,14', '1,0,0,0,0,0,16384,14', '1,0,0,0,0,0,1.0,14', '1,0,0,14'])
def test_bad_frames(line):
    p = Parser()
    for h in handshake().splitlines():
        p.parse(h)
    with pytest.raises(ProtocolError):
        p.parse(line)
    assert p.last_time is None


def test_framer():
    f = LineFramer(maximum=10)
    assert f.feed(b'abc') == []
    assert f.feed(b'\r\nxy\nz') == ['abc','xy']
    assert isinstance(f.feed(b'x'*30)[0], ProtocolError)
    assert f.feed(b'\nvalid\n') == ['valid']
    assert isinstance(f.feed(b'\xff\n')[0], ProtocolError)


def test_conversion_and_labels():
    c = ChannelConfig(True, '拉力', -50, 150)
    assert c.convert(0, 14) == -50
    assert c.convert(16383, 14) == 150
    c = replace(c, mode='两点标定', raw0=100, raw1=200, value0=0, value1=10)
    assert c.convert(300, 14) == 20  # No clipping.
    assert labels([c, ChannelConfig(), c]) == ['A0 拉力1','A1 未启用','A2 拉力2']
    with pytest.raises(ValueError):
        replace(c, raw1=100).validate(14)


@pytest.mark.parametrize('n,bits', [(6,14),(16,10)])
def test_record_zero_events_and_disconnect(tmp_path,n,bits):
    e = AcquisitionEngine()
    try:
        e.connect(simulated='MEGA_2560' if n==16 else 'UNO_R4_MINIMA', interval=.02)
        wait_for(lambda:e.latest is not None)
        cfg = [ChannelConfig(True,'拉力' if i%3==0 else '水平位移', -5, 100, raw1=(1<<bits)-1) for i in range(n)]
        cfg[-1].enabled = False
        e.configure(cfg)
        path = tmp_path/'SIMULATED_test.csv'
        e.start_recording(path, '双板验收')
        wait_for(lambda:e.sample_number >= 3)
        e.zero([0,1])
        with e.lock:
            expected_offset = e.offsets[0]
        wait_for(lambda:e.sample_number >= 5)
        with pytest.raises(ValueError,match='锁定'):
            e.configure(cfg)
        e.disconnect('测试断线')
        assert e.recorder is None
        raw_path, result_path = recording_paths(path)
        with raw_path.open(encoding='utf-8-sig') as f:
            meta = json.loads(f.readline()[2:])
            reader = csv.DictReader(f)
            rows = list(reader)
        assert meta['source']=='SIMULATED'
        assert meta['experiment_name']=='双板验收'
        assert len(meta['sensor_labels']) == n
        assert all(None not in r for r in rows)
        samples = [r for r in rows if r['record_type']=='sample']
        assert samples and all(r[f'A{n-1}_raw'] for r in samples)
        z = next(r for r in rows if r['record_type']=='zero')
        assert json.loads(z['message'].split('offsets_json=',1)[1])['A0'] == expected_offset
        assert rows[-1]['record_type']=='stop'
        assert '+' in samples[0]['pc_receive_time'] or samples[0]['pc_receive_time'].endswith('Z')
        with result_path.open(encoding='utf-8-sig') as f:
            result_rows = list(csv.DictReader(f))
        assert result_rows and f'A{n-1}' not in ''.join(result_rows[0])
        e.connected = True
        e.last_received = time.monotonic()
        with pytest.raises(FileExistsError):
            e.start_recording(path)
    finally:
        e.shutdown()


def test_config_board_isolation(tmp_path):
    p = Parser()
    p.parse(handshake().splitlines()[0])
    cap = p.capabilities
    configs = [ChannelConfig() for _ in range(6)]
    path = tmp_path/'config.json'
    save_config(path, cap, configs)
    assert load_config(path, cap) == configs
    q = Parser(); q.parse(handshake(16,10).splitlines()[0])
    with pytest.raises(ValueError,match='其他板型'):
        load_config(path,q.capabilities)


def test_time_reversal_stops_record(tmp_path):
    e = AcquisitionEngine()
    try:
        e.connected = True
        e.simulated = True
        with e.lock:
            e.ingest((handshake()+frame()).encode())
        e.configure([ChannelConfig(True,'拉力')]+[ChannelConfig() for _ in range(5)])
        p = tmp_path/'SIMULATED_reboot.csv'
        e.start_recording(p)
        e.queue.put(('bytes', frame(t=.5).encode(), None, time.monotonic()))
        wait_for(lambda:not e.connected)
        assert e.recorder is None
        assert 'error' in recording_paths(p)[0].read_text(encoding='utf-8-sig')
    finally:
        e.shutdown()


def test_disk_error_stops_while_exact_plot_history_remains_available(tmp_path, monkeypatch):
    e = AcquisitionEngine()
    try:
        e.connect(simulated='UNO_R4_MINIMA', interval=.02)
        wait_for(lambda:e.latest is not None)
        e.configure([ChannelConfig(True,'拉力')]+[ChannelConfig() for _ in range(5)])
        e.start_recording(tmp_path/'SIMULATED_disk.csv')
        raw_path, result_path = e.recorder.raw_path, e.recorder.path
        def fail(*args, **kwargs):
            raise OSError('disk full fixture')
        monkeypatch.setattr(e.recorder, 'row', fail)
        def failure_complete():
            with e.lock:
                return e.recorder is None and '磁盘写入失败' in e.status
        wait_for(failure_complete)
        with e.lock:
            assert e.connected  # Preview remains possible.
            assert isinstance(e.history, list)
            assert '可能只有' in e.record_outcomes[raw_path]
            assert '可能只有' in e.record_outcomes[result_path]
    finally:
        e.shutdown()


def test_transport_queue_overflow_is_explicit():
    from queue import Queue
    from desktop_app.transport import Transport
    q = Queue(maxsize=1)
    q.put('full')
    errors=[]
    t=Transport(q, errors.append, simulated='UNO_R4_MINIMA')
    assert not t.put('bytes', b'1\n')
    assert errors and '溢出' in errors[0]
    assert t.stopped.is_set()


def test_plot_elapsed_origin_and_gap_without_fake_samples(tmp_path):
    e = AcquisitionEngine()
    try:
        e.connected=True
        e.simulated=True
        with e.lock:
            e.ingest((handshake()+frame(t=500)).encode())
        e.configure([ChannelConfig(True,'拉力')]+[ChannelConfig() for _ in range(5)])
        path=tmp_path/'SIMULATED_gap.csv'
        e.start_recording(path)
        assert e.history[0][0]==0
        with e.lock:
            e.ingest(frame(t=503).encode())
            assert e.history[-1][0]==3
            assert e.history[-2][1]==[None]*6
        e.stop_recording()
        with recording_paths(path)[0].open(encoding='utf-8-sig') as f:
            f.readline()
            rows=list(csv.DictReader(f))
        samples=[r for r in rows if r['record_type']=='sample']
        assert [float(r['device_time_s']) for r in samples]==[500,503]
        assert [float(r['elapsed_s']) for r in samples]==[0,3]
    finally:
        e.shutdown()


def test_half_second_r4_gap_and_freshness_are_interval_aware(monkeypatch):
    e = AcquisitionEngine()
    try:
        e.connected = True
        e.simulated = True
        with e.lock:
            e.ingest((handshake(interval_ms=500) + frame(t=500)).encode(), mono=100.0)
            e.ingest(frame(t=501).encode(), mono=101.0)
            assert e.history[-2][1] == [None] * 6
            assert e.stale_timeout_seconds == 1.75
        monkeypatch.setattr(time, 'monotonic', lambda: 102.8)
        assert e.stale
    finally:
        e.shutdown()


def test_unzeroed_range_status_and_negative_zeroed(tmp_path):
    e=AcquisitionEngine()
    try:
        e.connected=True
        e.simulated=True
        with e.lock:
            e.ingest((handshake()+frame(t=1,raw=25)).encode())
        cfg=ChannelConfig(True,'拉力',0,100,mode='两点标定',raw0=0,raw1=100,value0=0,value1=200)
        e.configure([cfg]+[ChannelConfig() for _ in range(5)])
        path=tmp_path/'SIMULATED_range.csv'
        e.start_recording(path)
        e.zero([0])
        with e.lock:
            e.ingest(frame(t=2,raw=20).encode())
            e.ingest(frame(t=3,raw=100).encode())
        e.stop_recording()
        raw_path, result_path = recording_paths(path)
        with raw_path.open(encoding='utf-8-sig') as f:
            meta=json.loads(f.readline()[2:])
            rows=list(csv.DictReader(f))
        assert meta['physical_values_not_clipped'] is True
        samples=[r for r in rows if r['record_type']=='sample']
        assert [int(r['A0_raw']) for r in samples] == [25,20,100]
        assert all(None not in r for r in rows)
        with result_path.open(encoding='utf-8-sig') as f:
            results=list(csv.DictReader(f))
        result_key='A0 拉力1(N)'
        assert [float(r[result_key]) for r in results] == [50,-10,150]
    finally:
        e.shutdown()


def test_session_frame_counters_reset_on_reconnect():
    e=AcquisitionEngine()
    try:
        e.connected=True
        with e.lock:
            e.ingest((handshake()+frame(t=1)+'2,broken\n'+frame(t=2)).encode())
            e.ingest(handshake().encode())
        assert e.sample_number==2
        assert e.bad_frame_count==1
        e.connect(simulated='MEGA_2560',interval=10)
        wait_for(lambda:e.cap is not None)
        assert e.sample_number==0
        assert e.bad_frame_count==0
    finally:
        e.shutdown()
