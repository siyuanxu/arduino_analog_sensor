import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time
import pytest
from PySide6 import QtCore, QtWidgets as W
from desktop_app.ui import MainWindow
from desktop_app.models import ChannelConfig
from desktop_app.recording import recording_paths


def refresh_ui(window):
    # Production intentionally skips busy snapshots; tests request a deterministic one.
    with window.engine.lock:
        window.refresh()


@pytest.fixture(scope="module")
def qt_app():
    app = W.QApplication.instance() or W.QApplication([])
    yield app
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize('model,n,bits', [('UNO_R4_MINIMA',6,14),('MEGA_2560',16,10)])
def test_offscreen_full_board(tmp_path,model,n,bits,qt_app):
    app = qt_app
    w = MainWindow()
    w.resize(1280,800)
    w.mode.setCurrentIndex(2 if n==16 else 1)
    w.show()
    try:
        w.engine.connect(simulated=model, interval=.03)
        deadline = time.monotonic()+3
        while not w.engine.latest and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        refresh_ui(w)
        assert w.table.rowCount()==n
        cfg = [ChannelConfig(True, ['拉力','水平位移','垂直位移'][i%3], 0,100,raw1=(1<<bits)-1) for i in range(n)]
        w.engine.configure(cfg)
        w.populate_config()
        refresh_ui(w)
        assert len(w.cards)==n and len(w.curves)==n
        w.engine.start_recording(tmp_path/f'SIMULATED_{model}.csv')
        refresh_ui(w)
        assert not w.table.cellWidget(0,2).isEnabled()
        assert w.curve_checks[0].isEnabled()
        w.curve_checks[0].setChecked(False)
        assert not w.curves[0][0].isVisible()
        w.engine.zero()
        deadline = time.monotonic()+.2
        while time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        w.last_plot=0
        refresh_ui(w)
        assert 'SIMULATED' in w.banner.text()
        out = Path(os.environ.get('ACQ_QA_DIR', str(Path(os.environ.get('ACQ_LOCAL_WORK', str(Path.home()/'Desktop/codex-space/.work/projects/直剪试验arduino')))/'active/acquisition-system/qa')))
        out.mkdir(exist_ok=True)
        w.tabs.setCurrentIndex(1)
        app.processEvents()
        w.grab().save(str(out/f'{model}_preview.png'))
        w.tabs.setCurrentIndex(0)
        app.processEvents()
        w.grab().save(str(out/f'{model}_configuration.png'))
        w.engine.disconnect()
        refresh_ui(w)
        assert '已断开' in w.banner.text()
    finally:
        w.close()
        deadline=time.monotonic()+4
        while not w.shutdown_done and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        assert w.shutdown_done
        w.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_slow_disk_does_not_block_gui(tmp_path, monkeypatch,qt_app):
    from PySide6 import QtCore
    from desktop_app.recording import Recorder
    app = qt_app
    w = MainWindow()
    w.show()
    ticks=[]
    timer=QtCore.QTimer()
    timer.timeout.connect(lambda:ticks.append(time.monotonic()))
    timer.start(20)
    try:
        w.engine.connect(simulated='UNO_R4_MINIMA',interval=.03)
        deadline=time.monotonic()+3
        while w.engine.latest is None and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        w.engine.configure([ChannelConfig(True,'拉力')]+[ChannelConfig() for _ in range(5)])
        original=Recorder.row
        def slow(self,*args,**kwargs):
            time.sleep(.15)
            return original(self,*args,**kwargs)
        monkeypatch.setattr(Recorder,'row',slow)
        before=len(ticks)
        w.run_command(lambda:w.engine.start_recording(tmp_path/'SIMULATED_slow.csv'))
        deadline=time.monotonic()+.45
        while time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        assert len(ticks)-before >= 10
        monkeypatch.setattr(Recorder,'row',original)
    finally:
        timer.stop()
        w.close()
        deadline=time.monotonic()+4
        while not w.shutdown_done and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert w.shutdown_done
        w.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_waiting_first_frame_and_range_badge(qt_app):
    app=qt_app
    w=MainWindow()
    w.show()
    try:
        w.engine.connect(simulated='UNO_R4_MINIMA',interval=.2)
        deadline=time.monotonic()+3
        while w.engine.cap is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        refresh_ui(w)
        assert '等待首帧' in w.banner.text()
        assert '有效帧 0' in w.banner.toolTip()
        while w.engine.latest is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        cfg=ChannelConfig(True,'拉力',0,100,mode='两点标定',raw0=0,raw1=100,value0=0,value1=200)
        w.engine.configure([cfg]+[ChannelConfig() for _ in range(5)])
        refresh_ui(w)
        # Hidden acquisition widgets are updated on entry, avoiding expensive
        # readout/plot work while the operator edits the configuration page.
        w.tabs.setCurrentIndex(1)
        app.processEvents()
        assert '超量程' in w.cards[0][1].text()
        w.engine.configure([ChannelConfig(True,'拉力',0,100)]+[ChannelConfig() for _ in range(5)])
        with w.engine.lock:
            w.engine.offsets[0]=150
        refresh_ui(w)
        assert '超量程' not in w.cards[0][1].text()
        assert '-' in w.cards[0][1].text()
        assert '坏帧 0' in w.banner.toolTip()
    finally:
        w.close()
        deadline=time.monotonic()+4
        while not w.shutdown_done and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert w.shutdown_done
        w.deleteLater()
        app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_failed_shutdown_restores_controls_and_retry(qt_app, monkeypatch):
    app=qt_app
    w=MainWindow()
    w.show()
    original=w.engine.shutdown
    errors=[]
    monkeypatch.setattr(W.QMessageBox,'warning',lambda parent,title,text:errors.append(text))
    def failure():
        raise RuntimeError('关闭失败测试')
    monkeypatch.setattr(w.engine,'shutdown',failure)
    w.close()
    deadline=time.monotonic()+3
    while w.closing and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert errors and not w.closing
    assert w.centralWidget().isEnabled() and w.timer.isActive()
    monkeypatch.setattr(w.engine,'shutdown',original)
    w.close()
    deadline=time.monotonic()+4
    while not w.shutdown_done and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert w.shutdown_done
    w.deleteLater()
    app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_three_pages_two_channels_and_readonly_delivery(qt_app,tmp_path):
    import hashlib
    app=qt_app
    w=MainWindow()
    w.resize(1280,800)
    w.show()
    try:
        assert [w.tabs.tabText(i) for i in range(w.tabs.count())]==['连接与配置','数据采集','成果交付']
        assert w.tabs.currentIndex()==0
        w.mode.setCurrentIndex(2)
        w.engine.connect(simulated='MEGA_2560',interval=.03)
        deadline=time.monotonic()+3
        while w.engine.latest is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        refresh_ui(w)
        cfg=[ChannelConfig(i in (1,15),'水平位移' if i==1 else '拉力' if i==15 else '未配置') for i in range(16)]
        w.engine.configure(cfg)
        w.populate_config()
        refresh_ui(w)
        assert len(w.cards)==len(w.curves)==len(w.curve_checks)==2
        assert w.curve_channels==[1,15]
        assert '启用 2 / 16' in w.banner.text()
        w.enter_acquisition()
        deadline=time.monotonic()+3
        while w.tabs.currentIndex()!=1 and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.tabs.currentIndex()==1
        w.experiment_name.setText('两通道流程验收')
        base=tmp_path/'SIMULATED_two_channels.csv'
        raw_path,path=recording_paths(base)
        w.engine.start_recording(base,'两通道流程验收')
        refresh_ui(w)
        w.tabs.setCurrentIndex(2)
        deadline=time.monotonic()+3
        while w.delivery_preview is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.delivery_preview is not None
        assert '正在记录' in w.delivery_state.text() and '尚未结束' in w.delivery_state.text()
        assert 'SIMULATED' in w.delivery_summary.toPlainText()
        assert '两通道流程验收' in w.delivery_summary.toPlainText()
        assert w.delivery_table.rowCount() > 0
        assert w.delivery_preview['header']==['记录时间(s)','A1 水平位移1(mm)','A15 拉力1(N)']
        assert w.delivery_preview['file_kind']=='results'
        assert w.recent_files.count()==2
        w.engine.stop_recording()
        before=hashlib.sha256(path.read_bytes()).digest()
        raw_before=hashlib.sha256(raw_path.read_bytes()).digest()
        w.reload_delivery()
        deadline=time.monotonic()+3
        while w.delivery_preview is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.delivery_preview is not None
        refresh_ui(w)
        assert '已结束' in w.delivery_state.text()
        assert '当前未在记录' in w.delivery_state.toolTip()
        assert hashlib.sha256(path.read_bytes()).digest()==before
        assert w.delivery_column_mode.currentText()=='测试结果（已选通道）'
        assert not w.delivery_column_mode.isEnabled()
        assert w.delivery_visible_columns==['记录时间(s)','A1 水平位移1(mm)','A15 拉力1(N)']
        assert w.delivery_table.columnCount()==3
        assert w.paired_file_button.isEnabled()
        w.preview_paired_file()
        deadline=time.monotonic()+3
        while w.delivery_preview is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.delivery_preview is not None
        assert w.delivery_preview['file_kind']=='raw'
        assert all(f'A{i}_raw' in w.delivery_visible_columns for i in range(16))
        assert 'A1_physical' not in w.delivery_visible_columns
        assert w.delivery_visible_columns==w.delivery_preview['header']
        w.preview_paired_file()
        deadline=time.monotonic()+3
        while w.delivery_preview is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.delivery_preview['file_kind']=='results'
        assert hashlib.sha256(path.read_bytes()).digest()==before
        assert hashlib.sha256(raw_path.read_bytes()).digest()==raw_before
        with pytest.raises(FileExistsError):
            w.save_curve_png(path)
        assert hashlib.sha256(path.read_bytes()).digest()==before
        png=tmp_path/'SIMULATED_plot.png'
        w.save_curve_png(png)
        assert png.read_bytes().startswith(b'\x89PNG')
        with pytest.raises(FileExistsError):
            w.save_curve_png(png)
        out=Path(os.environ.get('ACQ_QA_DIR', str(Path(os.environ.get('ACQ_LOCAL_WORK', str(Path.home()/'Desktop/codex-space/.work/projects/直剪试验arduino')))/'active/acquisition-system/qa')))
        app.processEvents()
        w.grab().save(str(out/'three_page_delivery.png'))
        w.tabs.setCurrentIndex(0)
        app.processEvents()
        w.grab().save(str(out/'three_page_two_channel_config.png'))
        w.tabs.setCurrentIndex(1)
        w.last_plot=0
        refresh_ui(w)
        app.processEvents()
        w.grab().save(str(out/'three_page_two_channel_acquisition.png'))
        (out/'current_curve_export.png').unlink(missing_ok=True)
        w.save_curve_png(out/'current_curve_export.png')
    finally:
        w.close()
        deadline=time.monotonic()+4
        while not w.shutdown_done and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert w.shutdown_done
        w.deleteLater()
        app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_preset_record_path_no_creation_and_next_unique_name(qt_app,tmp_path,monkeypatch):
    import json
    app=qt_app
    w=MainWindow()
    w.show()
    try:
        w.engine.connect(simulated='UNO_R4_MINIMA',interval=.04)
        deadline=time.monotonic()+3
        while w.engine.latest is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        w.engine.configure([ChannelConfig(True,'拉力')]+[ChannelConfig() for _ in range(5)])
        w.populate_config()
        refresh_ui(w)
        assert not w.start_btn.isEnabled()
        assert not w.record_target
        w.experiment_name.setText('试样01')
        path=tmp_path/'SIMULATED_preset.csv'
        raw_path,result_path=recording_paths(path)
        monkeypatch.setattr(W.QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(path),'CSV (*.csv)'))
        w.choose_record_target()
        deadline=time.monotonic()+3
        while not w.target_ready and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.target_ready and not path.exists()
        assert not raw_path.exists() and not result_path.exists()
        assert str(raw_path) in w.target_status.toolTip() and str(result_path) in w.target_status.toolTip()
        refresh_ui(w)
        assert w.start_btn.isEnabled()
        def forbidden_dialog(*args,**kwargs):
            pytest.fail('Start must use the preset path without a save dialog')
        monkeypatch.setattr(W.QFileDialog,'getSaveFileName',forbidden_dialog)
        w.start_recording()
        deadline=time.monotonic()+3
        while w.engine.recorder is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.engine.recorder is not None and raw_path.exists() and result_path.exists()
        assert not path.exists()
        refresh_ui(w)
        assert not w.target_path_edit.isEnabled() and not w.experiment_name.isEnabled()
        with raw_path.open(encoding='utf-8-sig') as f:
            metadata=json.loads(f.readline()[2:])
        assert metadata['raw_file']==str(raw_path.resolve())
        assert metadata['result_file']==str(result_path.resolve())
        assert metadata['experiment_name']=='试样01'
        assert result_path.read_text(encoding='utf-8-sig').splitlines()[0]=='记录时间(s),A0 拉力1(N)'
        w.engine.stop_recording()
        refresh_ui(w)
        deadline=time.monotonic()+3
        while not w.target_ready and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert w.target_ready
        new_path=Path(w.record_target)
        assert new_path.parent==path.parent and new_path!=path
        assert 'SIMULATED' in new_path.name and '试样01' in new_path.name
        assert not new_path.exists()
        assert w.experiment_name.isEnabled()
        w.set_record_target(path)
        deadline=time.monotonic()+3
        while '已存在' not in w.target_status.text() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert not w.target_ready and '已存在' in w.target_status.text()
        assert not w.start_btn.isEnabled()
    finally:
        w.close()
        deadline=time.monotonic()+4
        while not w.shutdown_done and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert w.shutdown_done
        w.deleteLater()
        app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()
