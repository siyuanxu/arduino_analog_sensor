import json
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtWidgets as W
from desktop_app.ui import MainWindow


def test_arduino_preferred_and_connected_board_cannot_look_unselected(monkeypatch):
    app = W.QApplication.instance() or W.QApplication([])
    ports = [
        dict(device='/dev/cu.debug-console', description='n/a', manufacturer=None, vid=None, pid=None),
        dict(device='/dev/cu.usbmodemMEGA', description='IOUSBHostDevice', manufacturer='Arduino', vid=0x2341, pid=0x0042),
        dict(device='/dev/cu.Bluetooth-Incoming-Port', description='n/a', manufacturer=None, vid=None, pid=None),
    ]
    monkeypatch.setattr('desktop_app.ui.enumerate_ports', lambda: ports)
    window = MainWindow()
    window.timer.stop()
    try:
        assert window.port.currentData() == '/dev/cu.usbmodemMEGA'
        assert 'Arduino Mega 2560' in window.port.currentText()
        assert '尚未连接' in window.port_status.text()
        assert '已选择' in window.port_status.text()
        window.connection_port = '/dev/cu.usbmodemMEGA'
        engine = window.engine
        info = dict(protocol_version=1, firmware_version='1.0.0', board_model='MEGA_2560',
                    channel_count=16, channels=[f'A{i}' for i in range(16)],
                    adc_bits=10, sample_interval_ms=1000)
        with engine.lock:
            engine.connected = True
            window.refresh()
            assert '等待板型确认' in window.port_status.text()
            assert window.connect_btn.text() == '连接中…'
            engine.ingest(('#INFO '+json.dumps(info)+'\n#HEADER device_time_s,'+
                           ','.join(f'A{i}_raw' for i in range(16))+',adc_bits\n'+
                           '1.000,'+','.join(['123']*16)+',10\n').encode())
            # A stale editable field must not contradict the actual connection.
            window.port.setEditText('/dev/cu.debug-console')
            window.refresh()
        assert '已连接：Arduino Mega 2560' in window.port_status.text()
        assert '16 路' in window.port_status.text() and '10 位' in window.port_status.text()
        assert '/dev/cu.usbmodemMEGA' in window.port_status.toolTip()
        assert '已连接 · Arduino Mega 2560' in window.banner.text()
        assert window.port.currentData() == '/dev/cu.usbmodemMEGA'
        assert 'Arduino Mega 2560' in window.port.currentText()
        assert not window.port.isEnabled() and not window.mode.isEnabled()
        assert not window.connect_btn.isEnabled() and window.connect_btn.text() == '已连接'
        assert window.disconnect_btn.isEnabled()
        engine.disconnect()
        window.refresh()
        assert window.connect_btn.isEnabled() and window.connect_btn.text() == '连接'
        assert window.port.isEnabled() and window.mode.isEnabled()
        assert not window.disconnect_btn.isEnabled()
        assert '尚未连接' in window.port_status.text()
    finally:
        window.close()
        deadline = time.monotonic()+4
        while not window.shutdown_done and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert window.shutdown_done
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()
