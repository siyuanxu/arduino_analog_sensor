"""Serial and simulated producers. A full queue is fatal, never silently drops frames."""
import json
import math
from queue import Full
import threading
import time
import serial
from serial.tools import list_ports
from .logging_support import get_logger
from .recording import now_iso

logger = get_logger("transport")


def enumerate_ports():
    return [{"device": p.device, "description": p.description, "manufacturer": p.manufacturer, "vid": p.vid, "pid": p.pid, "serial_number": p.serial_number, "hwid": p.hwid} for p in list_ports.comports()]


class Transport(threading.Thread):
    def __init__(self, queue, fatal, port=None, simulated=None, interval=1.0,
                 session_id=None):
        super().__init__(daemon=True, name="ADC transport")
        self.queue, self.fatal = queue, fatal
        self.port, self.simulated, self.interval = port, simulated, interval
        self.session_id = session_id
        self.stopped = threading.Event()
        self.serial = None

    def put(self, kind, payload):
        try:
            item = (kind, payload, now_iso(), time.monotonic())
            if self.session_id is not None:
                item += (self.session_id,)
            self.queue.put(item, timeout=0.25)
            return True
        except Full:
            logger.error("采集队列溢出：source=%s port=%s", self.simulated or "SERIAL", self.port or "-")
            message = "采集队列溢出，记录已停止；请降低系统负载后重新连接"
            if self.session_id is None:
                self.fatal(message)
            else:
                self.fatal(message, self.session_id)
            self.stopped.set()
            return False

    def run(self):
        try:
            if self.simulated:
                n, bits = (16, 10) if self.simulated == "MEGA_2560" else (6, 14)
                sample_interval_ms = 1000 if self.simulated == "MEGA_2560" else 500
                info = dict(protocol_version=1, firmware_version="SIMULATED-1.0.0", board_model=self.simulated, channel_count=n, channels=[f"A{i}" for i in range(n)], adc_bits=bits, sample_interval_ms=sample_interval_ms)
                header = "device_time_s," + ",".join(f"A{i}_raw" for i in range(n)) + ",adc_bits"
                logger.info("模拟传输启动：board=%s channels=%d adc_bits=%d interval=%.3fs", self.simulated, n, bits, self.interval)
                self.put("bytes", ("#INFO " + json.dumps(info) + "\n#HEADER " + header + "\n").encode())
                t = 0.0
                while not self.stopped.wait(self.interval):
                    t += sample_interval_ms / 1000.0
                    raw = [int((0.5+0.42*math.sin(t/12+i*.4))*((1 << bits)-1)) for i in range(n)]
                    if not self.put("bytes", (f"{t:.3f}," + ",".join(map(str, raw)) + f",{bits}\n").encode()):
                        break
            else:
                logger.info("正在打开串口：port=%s baud=115200", self.port)
                self.serial = serial.Serial(self.port, 115200, timeout=0.15, write_timeout=1)
                logger.info("串口已打开：port=%s", self.port)
                self.serial.write(b"INFO\nHEADER\n")
                self.serial.flush()
                last_request = time.monotonic()
                while not self.stopped.is_set():
                    data = self.serial.read(min(max(self.serial.in_waiting, 1), 4096))
                    # stop() may have been called while a driver-level read was
                    # blocked.  Never publish that late read into a new session.
                    if self.stopped.is_set():
                        break
                    if data and not self.put("bytes", data):
                        break
                    # Some boards reset when the port opens; request again after boot.
                    if time.monotonic()-last_request >= 5:
                        self.serial.write(b"INFO\nHEADER\n")
                        last_request = time.monotonic()
        except (serial.SerialException, OSError) as e:
            if not self.stopped.is_set():
                logger.exception("串口连接失败或意外断开：port=%s", self.port)
                self.put("disconnect", f"串口连接失败/断开：{e}")
        finally:
            if self.serial is not None:
                try:
                    self.serial.close()
                    logger.info("串口已关闭：port=%s", self.port)
                except (serial.SerialException, OSError):
                    logger.exception("关闭串口失败：port=%s", self.port)
            elif self.simulated:
                logger.info("模拟传输已停止：board=%s", self.simulated)

    def stop(self):
        self.stopped.set()
