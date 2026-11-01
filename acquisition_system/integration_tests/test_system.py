"""Cross-component acceptance checks; every input here is SIMULATED, not hardware.

Run from the project: python -m unittest discover -s acquisition_system/integration_tests -v
Firmware host binaries are built into a temporary directory with the host C++ compiler.
"""
import csv
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_app.controller import AcquisitionEngine
from desktop_app.models import ChannelConfig, labels
from desktop_app.recording import now_iso, recording_paths

HOST_BINARIES = {}


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for simulated device/controller")


def firmware_fixture(board):
    command = ([str(HOST_BINARIES["mega"]), "--sample"] if board == "MEGA_2560"
               else [str(HOST_BINARIES["wifi"])] if board == "UNO_R4_WIFI"
               else [str(HOST_BINARIES["r4"]), "--emit"])
    return subprocess.check_output(command)


def sample_bytes(cap, device_time, raw, windows=None):
    fields = [f"{device_time:.3f}", *map(str, raw)]
    if cap.protocol_version == 2:
        if windows is None:
            windows = [[v] * 5 for v in raw[:2]]
        # The synthetic transport only: emit the integer wire representation
        # expected from the firmware; the host must use those filtered counts.
        for window in windows:
            fields.append(str((sum(sorted(window)[1:-1]) + 1) // 3))
        fields.extend(";".join(map(str, window)) for window in windows)
    return (",".join([*fields, str(cap.adc_bits)]) + "\n").encode()


def read_csv(path):
    raw_path, _ = recording_paths(path)
    text = raw_path.read_text(encoding="utf-8-sig")
    meta = [json.loads(line[2:]) for line in text.splitlines() if line.startswith("# ")]
    content = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    return meta, list(csv.DictReader(io.StringIO(content)))


def read_results(path):
    _, result_path = recording_paths(path)
    with result_path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class SystemAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++")
        if not compiler:
            raise unittest.SkipTest("Cross-firmware fixtures require a host C++ compiler")
        cls.host_build = tempfile.TemporaryDirectory(prefix="adc-host-build-")
        for name, relative in [("r4", "r4_adc/tests/host_behavior_test.cpp"),
                               ("mega", "mega_adc/tests/host_test.cpp")]:
            binary = Path(cls.host_build.name) / name
            subprocess.check_call([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                   str(ROOT / "firmware" / relative), "-o", str(binary)])
            HOST_BINARIES[name] = binary
        binary = Path(cls.host_build.name) / "wifi"
        subprocess.check_call([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                               str(ROOT / "integration_tests/emit_r4_wifi.cpp"), "-o", str(binary)])
        HOST_BINARIES["wifi"] = binary

    @classmethod
    def tearDownClass(cls):
        cls.host_build.cleanup()

    def setUp(self):
        self.engine = AcquisitionEngine()
        self.temp = tempfile.TemporaryDirectory(prefix="adc-SIMULATED-")

    def tearDown(self):
        self.engine.shutdown()
        self.temp.cleanup()

    def feed(self, data):
        self.engine.queue.put(("bytes", data, now_iso(), time.monotonic()))

    def prepare_fixture(self, board):
        self.engine.connected = True
        self.engine.simulated = True
        self.feed(firmware_fixture(board))
        until(lambda: self.engine.latest is not None)
        cap = self.engine.cap
        configs = [ChannelConfig(enabled=True,
                    kind=("水平位移", "垂直位移", "拉力")[i % 3],
                    minimum=0, maximum=50000 if i % 3 == 2 else 100,
                    raw1=(1 << cap.adc_bits)-1) for i in range(cap.channel_count)]
        self.engine.configure(configs)
        return cap

    def assert_recording(self, board):
        cap = self.prepare_fixture(board)
        path = Path(self.temp.name) / f"SIMULATED_{board}.csv"
        self.engine.start_recording(path)
        self.engine.zero()
        offsets = list(self.engine.offsets)
        next_time = self.engine.latest.device_time_s + 1
        raw = [0 if i % 2 == 0 else (1 << cap.adc_bits)-1 for i in range(cap.channel_count)]
        count = self.engine.sample_number
        self.feed(sample_bytes(cap, next_time, raw))
        until(lambda: self.engine.sample_number > count)
        # The file is readable before close: per-frame writes are already flushed.
        meta, rows = read_csv(path)
        record = [r for r in rows if r["record_type"] == "sample"][-1]
        result = read_results(path)[-1]
        self.assertEqual(record["source"], "SIMULATED")
        self.assertEqual(int(record["adc_bits"]), cap.adc_bits)
        self.assertEqual(len(meta[0]["configs"]), cap.channel_count)
        self.assertIn("+", record["pc_receive_time"])
        for i, channel in enumerate(cap.channels):
            expected = self.engine.configs[i].convert(raw[i], cap.adc_bits)
            self.assertEqual(int(record[channel+"_raw"]), raw[i])
            result_header = f"{labels(self.engine.configs)[i]}({self.engine.configs[i].unit})"
            self.assertAlmostEqual(float(result[result_header]), expected-offsets[i], places=self.engine.configs[i].decimals)
        self.assertTrue(any(r["record_type"] == "zero" for r in rows))
        with self.assertRaises(ValueError):
            self.engine.configure(self.engine.configs)
        self.engine.stop_recording()
        with self.assertRaises(FileExistsError):
            self.engine.start_recording(path)
        _, rows = read_csv(path)
        self.assertEqual(rows[-1]["record_type"], "stop")
        self.assertTrue(all(None not in r for r in rows), "CSV event rows must remain rectangular")

    def test_r4_emitted_protocol_to_all_channel_csv(self):
        self.assert_recording("UNO_R4_MINIMA")

    def test_mega_emitted_protocol_to_all_channel_csv(self):
        self.assert_recording("MEGA_2560")

    def test_r4_wifi_formatter_to_all_channel_csv(self):
        self.assert_recording("UNO_R4_WIFI")

    def test_r4_filter_survives_zero_csv_and_delivery_replay(self):
        from desktop_app.delivery import read_csv_preview
        cap = self.prepare_fixture("UNO_R4_MINIMA")
        self.assertEqual(cap.protocol_version, 2)
        path = Path(self.temp.name) / "SIMULATED_r4_firmware_filter.csv"
        self.engine.start_recording(path)
        self.engine.zero()
        offsets = list(self.engine.offsets)
        latest = [14900, 2, 300, 400, 500, 600]
        windows = [[50, 51, 49, 50, 14900], [210, 211, 209, 210, 2]]
        n = self.engine.sample_number
        self.feed(sample_bytes(cap, self.engine.latest.device_time_s + 0.5,
                               latest, windows))
        until(lambda: self.engine.sample_number > n)
        self.engine.stop_recording()
        _, raw = read_csv(path)
        sample = [r for r in raw if r["record_type"] == "sample"][-1]
        result = read_results(path)[-1]
        for i, filtered in enumerate((50, 210)):
            self.assertEqual(int(sample[f"A{i}_raw"]), latest[i])
            self.assertEqual(int(sample[f"A{i}_filtered"]), filtered)
            self.assertEqual(list(map(int, sample[f"A{i}_window"].split(";"))), windows[i])
            config = self.engine.configs[i]
            key = f"{labels(self.engine.configs)[i]}({config.unit})"
            self.assertAlmostEqual(float(result[key]), config.convert(filtered, 14) - offsets[i],
                                   places=config.decimals)
        self.assertTrue(read_csv_preview(recording_paths(path)[1])["metadata_available"])

    def test_disabled_raw_kept_and_physical_blank(self):
        cap = self.prepare_fixture("MEGA_2560")
        configs = list(self.engine.configs)
        configs[-1] = ChannelConfig(raw1=1023)
        self.engine.configure(configs)
        path = Path(self.temp.name) / "SIMULATED_disabled.csv"
        self.engine.start_recording(path)
        self.engine.stop_recording()
        _, rows = read_csv(path)
        record = next(r for r in rows if r["record_type"] == "sample")
        self.assertEqual(int(record["A15_raw"]), self.engine.latest.raw[15])
        result_headers = read_results(path)[0].keys()
        self.assertFalse(any(header.startswith("A15 ") for header in result_headers))
        self.assertEqual(labels(configs)[0], "A0 水平位移1")
        self.assertEqual(labels(configs)[3], "A3 水平位移2")

    def test_reboot_stops_without_losing_previous_rows(self):
        cap = self.prepare_fixture("UNO_R4_MINIMA")
        path = Path(self.temp.name) / "SIMULATED_reboot.csv"
        self.engine.start_recording(path)
        self.feed(sample_bytes(cap, .001, [1]*cap.channel_count))
        until(lambda: not self.engine.connected)
        self.assertIsNone(self.engine.recorder)
        _, rows = read_csv(path)
        self.assertTrue(any(r["record_type"] == "sample" for r in rows))
        self.assertTrue(any(r["record_type"] == "error" for r in rows))

    def test_long_replay_keeps_zero_and_all_samples_in_plot_and_csv(self):
        cap = self.prepare_fixture("MEGA_2560")
        path = Path(self.temp.name) / "SIMULATED_long_replay.csv"
        self.engine.zero()
        self.engine.start_recording(path)
        frames = 7220  # Beyond the former 7200-point plotting limit.
        first = self.engine.latest.device_time_s
        replay = b"".join(sample_bytes(cap, first+i,
                         [int((i+c) % 1024) for c in range(16)])
                          for i in range(1, frames+1))
        before = self.engine.sample_number
        self.feed(replay)
        until(lambda: self.engine.sample_number == before+frames, timeout=30)
        self.engine.stop_recording()
        self.assertEqual(len(self.engine.history), frames+1)
        self.assertEqual(self.engine.history[0][0], 0)
        self.assertEqual(self.engine.history[-1][0], frames)
        _, rows = read_csv(path)
        samples = [r for r in rows if r["record_type"] == "sample"]
        self.assertEqual(len(samples), frames+1)
        self.assertEqual(float(samples[-1]["device_time_s"]), first+frames)
        self.assertEqual(int(samples[-1]["A15_raw"]), int((frames+15) % 1024))

    @unittest.skipUnless(os.name == "posix", "PTY fixture requires a POSIX test host")
    def test_real_pyserial_path_over_simulated_pty(self):
        # This opens only a newly-created PTY, never a physical Arduino port.
        master, slave = os.openpty()
        slave_name = os.ttyname(slave)
        stop = threading.Event()
        fixture = firmware_fixture("MEGA_2560").splitlines(keepends=True)
        info_header = b"".join(fixture[:2])
        def emulator():
            sent = False
            next_frame = time.monotonic() + .3
            device_time = 0
            pending = b""
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([master], [], [], .02)
                    if ready:
                        pending += os.read(master, 4096)
                        if b"INFO\n" in pending and not sent:
                            # Force a partial line across serial reads.
                            os.write(master, info_header[:17])
                            time.sleep(.03)
                            os.write(master, info_header[17:])
                            sent = True
                    if sent and time.monotonic() >= next_frame:
                        device_time += 1
                        raw = [i*60 for i in range(16)]
                        line = f"{device_time}.000,"+",".join(map(str, raw))+",10\n"
                        os.write(master, line.encode())
                        next_frame = time.monotonic() + .1
                except OSError:
                    return
        thread = threading.Thread(target=emulator, daemon=True)
        thread.start()
        try:
            self.engine.connect(port=slave_name)
            until(lambda: self.engine.latest is not None)
            self.assertEqual(self.engine.cap.channel_count, 16)
            self.assertEqual(self.engine.latest.raw[-1], 900)
            self.assertFalse(self.engine.simulated)
            self.engine.configure([ChannelConfig(enabled=True, kind="拉力", maximum=50000, raw1=1023)]+[ChannelConfig() for _ in range(15)])
            path = Path(self.temp.name) / "SIMULATED_PTY_validation.csv"
            self.engine.start_recording(path)
            start_count = self.engine.sample_number
            until(lambda: self.engine.sample_number >= start_count+2)
            self.engine.zero([0])
            self.engine.disconnect("SIMULATED PTY acceptance end")
            _, rows = read_csv(path)
            self.assertGreaterEqual(sum(r["record_type"] == "sample" for r in rows), 3)
            self.assertEqual(rows[-1]["record_type"], "stop")
        finally:
            stop.set()
            thread.join(timeout=1)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
