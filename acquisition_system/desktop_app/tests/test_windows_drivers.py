import json
import time

import pytest

from desktop_app import windows_drivers as drivers


@pytest.fixture(autouse=True)
def targeted_scan_supported(monkeypatch):
    monkeypatch.setattr(drivers, "_supports_targeted_scan", lambda: True)


@pytest.mark.parametrize("instance_id,board,role,repairable,package", [
    (r"USB\VID_2341&PID_0069&MI_00\ABC", "UNO_R4_MINIMA", "runtime", True, "usbser.inf"),
    (r"USB\VID_2341&PID_0369\ABC", "UNO_R4_MINIMA", "bootloader", False, None),
    (r"USB\VID_2341&PID_0042\ABC", "MEGA_2560", "runtime", True, "arduino.inf"),
    (r"USB\VID_2341&PID_0010\ABC", "MEGA_2560", "runtime", True, "arduino.inf"),
    (r"USB\VID_2A03&PID_0042\ABC", "MEGA_2560", "runtime", True, "usbser.inf"),
    (r"USB\VID_2A03&PID_0010\ABC", "MEGA_2560", "runtime", True, "usbser.inf"),
])
def test_exact_supported_code28_identities(instance_id, board, role, repairable, package):
    device = drivers._classify_device(instance_id, "", 28)
    assert (device.board_model, device.role, device.repairable, device.driver_package) == (
        board, role, repairable, package)


@pytest.mark.parametrize("instance_id", [
    r"USB\VID_2341&PID_0069\COMPOSITE_PARENT",
    r"USB\VID_2341&PID_0069&MI_02\DFU_INTERFACE",
    r"USB\VID_2341&PID_0069&MI_01\OTHER_INTERFACE",
    r"USB\VID_2341&PID_0042&MI_00\NOT_ORIGINAL_MEGA_LAYOUT",
    r"USB\VID_1A86&PID_7523\CH340_NOT_IN_THIS_RELEASE",
    r"USB\VID_2341&PID_0043\UNO_R3",
])
def test_composite_dfu_clone_and_unrelated_devices_are_not_targeted(instance_id):
    assert drivers._classify_device(instance_id, "unknown", 28) is None


def test_other_problem_is_reported_but_not_repairable():
    device = drivers._classify_device(r"USB\VID_2341&PID_0069&MI_00\ABC", "R4", 10)
    assert device.problem_code == 10 and not device.repairable


def test_decoder_accepts_single_object_and_bad_problem_code():
    payload = json.dumps(dict(instance_id=r"USB\VID_2341&PID_0042\ABC",
                              name="Mega", problem_code="bad"))
    assert drivers._decode_pnp_json(payload) == [
        (r"USB\VID_2341&PID_0042\ABC", "Mega", -1)]


def scan(*devices, available=True, error=None):
    return drivers.DriverScan(available, tuple(devices), error)


def r4(code):
    return drivers._classify_device(r"USB\VID_2341&PID_0069&MI_00\ABC", "R4", code)


def test_repair_rejects_stale_absent_and_nonmissing_devices():
    missing, ready = r4(28), r4(0)
    result = drivers.repair_windows_driver(missing, scanner=lambda: scan())
    assert not result.success and "拔出" in result.message
    result = drivers.repair_windows_driver(ready, scanner=lambda: scan(ready))
    assert not result.success and "仅能修复" in result.message


def test_uac_cancel_is_visible(monkeypatch, tmp_path):
    inf = tmp_path / "usbser.inf"
    inf.write_text("fixture", encoding="ascii")
    monkeypatch.setattr(drivers, "_driver_path", lambda device: inf)
    missing = r4(28)
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: scan(missing),
        elevated_runner=lambda args: drivers._ElevatedRun(None, True, "cancelled"))
    assert not result.success and result.cancelled and "取消" in result.message


def test_success_requires_fresh_enumeration_without_code28(monkeypatch, tmp_path):
    missing, ready = r4(28), r4(0)
    states = iter((scan(missing), scan(ready)))
    called = []
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: next(states),
        elevated_runner=lambda args: called.append(args) or drivers._ElevatedRun(0),
        poll_timeout=0)
    assert result.success and not result.cancelled
    assert called == [["/scan-devices", "/instanceid", missing.instance_id]]
    assert all("/install" not in call for call in called)


def test_command_success_with_unresolved_device_is_not_success(monkeypatch, tmp_path):
    missing = r4(28)
    states = iter((scan(missing), scan(missing)))
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: next(states),
        elevated_runner=lambda args: drivers._ElevatedRun(0), poll_timeout=0)
    assert not result.success and "仍报告缺少驱动" in result.message


def test_other_same_board_does_not_make_target_successful():
    missing = r4(28)
    other_ready = drivers._classify_device(
        r"USB\VID_2341&PID_0069&MI_00\OTHER", "R4 other", 0)
    states = iter((scan(missing), scan(other_ready)))
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: next(states),
        elevated_runner=lambda args: drivers._ElevatedRun(0), poll_timeout=0)
    assert not result.success and "未重新出现" in result.message
    assert result.replug_required


@pytest.mark.parametrize("problem_code", [10, 31])
def test_different_target_problem_is_not_success_and_not_repairable(problem_code):
    missing, faulty = r4(28), r4(problem_code)
    states = iter((scan(missing), scan(faulty)))
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: next(states),
        elevated_runner=lambda args: drivers._ElevatedRun(0), poll_timeout=0)
    assert not result.success and f"错误代码 {problem_code}" in result.message
    assert result.device.problem_code == problem_code and not result.device.repairable


def test_mega_package_requires_inf_and_matching_catalog(monkeypatch, tmp_path):
    source = drivers._default_driver_root()
    monkeypatch.setattr(drivers, "_default_driver_root", lambda: tmp_path)
    mega = drivers._classify_device(r"USB\VID_2341&PID_0042\ABC", "Mega", 28)
    (tmp_path / "arduino.inf").write_bytes((source / "arduino.inf").read_bytes())
    with pytest.raises(FileNotFoundError):
        drivers._driver_path(mega)
    (tmp_path / "arduino.cat").write_bytes((source / "arduino.cat").read_bytes())
    assert drivers._driver_path(mega) == tmp_path / "arduino.inf"
    (tmp_path / "arduino.inf").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验失败"):
        drivers._driver_path(mega)


def test_mega_stages_without_global_install_then_refreshes_target(monkeypatch):
    mega_missing = drivers._classify_device(r"USB\VID_2341&PID_0042\ABC", "Mega", 28)
    mega_ready = drivers._classify_device(r"USB\VID_2341&PID_0042\ABC", "Mega", 0)
    inf = drivers._default_driver_root() / "arduino.inf"
    states = iter((scan(mega_missing), scan(mega_missing), scan(mega_ready)))
    calls = []
    result = drivers.repair_windows_driver(
        mega_missing, scanner=lambda: next(states),
        elevated_runner=lambda args: calls.append(args) or drivers._ElevatedRun(0),
        poll_timeout=0)
    assert result.success and result.staged
    assert calls == [["/add-driver", str(inf)],
                     ["/scan-devices", "/instanceid", mega_missing.instance_id]]
    assert all("/install" not in call for call in calls)


def test_2a03_mega_uses_inbox_driver_without_bundled_package():
    missing = drivers._classify_device(r"USB\VID_2A03&PID_0042\ABC", "Mega", 28)
    ready = drivers._classify_device(r"USB\VID_2A03&PID_0042\ABC", "Mega", 0)
    states = iter((scan(missing), scan(ready)))
    calls = []
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: next(states),
        elevated_runner=lambda args: calls.append(args) or drivers._ElevatedRun(0),
        poll_timeout=0)
    assert result.success and not result.staged
    assert calls == [["/scan-devices", "/instanceid", missing.instance_id]]
    assert all("/add-driver" not in call for call in calls)


def test_mega_second_uac_cancel_reports_staged_driver():
    mega = drivers._classify_device(r"USB\VID_2341&PID_0042\ABC", "Mega", 28)
    calls = []

    def runner(args):
        calls.append(args)
        return drivers._ElevatedRun(0) if len(calls) == 1 else drivers._ElevatedRun(None, True)

    result = drivers.repair_windows_driver(
        mega, scanner=lambda: scan(mega), elevated_runner=runner, poll_timeout=0)
    assert not result.success and result.cancelled and result.staged
    assert result.replug_required and not result.restart_required and "重新插拔" in result.message


def test_reboot_required_exit_code_is_not_claimed_as_success():
    missing = r4(28)
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: scan(missing),
        elevated_runner=lambda args: drivers._ElevatedRun(3010), poll_timeout=0)
    assert not result.success and result.restart_required and "重启" in result.message


def test_old_windows_does_not_run_unsupported_targeted_scan(monkeypatch):
    monkeypatch.setattr(drivers, "_supports_targeted_scan", lambda: False)
    missing = r4(28)
    calls = []
    result = drivers.repair_windows_driver(
        missing, scanner=lambda: scan(missing),
        elevated_runner=lambda args: calls.append(args) or drivers._ElevatedRun(0))
    assert not calls and not result.success and result.replug_required
    assert "不支持定向" in result.message and "重新插拔" in result.message


def test_old_windows_stages_mega_once_then_requests_replug(monkeypatch):
    monkeypatch.setattr(drivers, "_supports_targeted_scan", lambda: False)
    mega = drivers._classify_device(r"USB\VID_2341&PID_0042\ABC", "Mega", 28)
    calls = []
    result = drivers.repair_windows_driver(
        mega, scanner=lambda: scan(mega),
        elevated_runner=lambda args: calls.append(args) or drivers._ElevatedRun(0))
    assert len(calls) == 1 and calls[0][0] == "/add-driver"
    assert result.staged and result.replug_required and "不支持定向" in result.message


def test_late_elevation_result_is_returned_once_without_fake_timeout(monkeypatch):
    calls = []

    def slow_worker(arguments, timeout):
        calls.append((arguments, timeout))
        time.sleep(.08)
        return drivers._ElevatedRun(0)

    monkeypatch.setattr(drivers, "_shell_execute_pnputil", slow_worker)
    started = time.monotonic()
    result = drivers._run_pnputil_elevated(["/scan-devices"], timeout_seconds=.01)
    assert time.monotonic() - started >= .07
    assert result.exit_code == 0 and not result.error
    assert calls == [(["/scan-devices"], .01)]


def test_process_timeout_warns_once_then_keeps_waiting(monkeypatch):
    class Kernel:
        def __init__(self):
            self.results = iter((0x00000102, 0x00000102, 0))
            self.calls = 0

        def WaitForSingleObject(self, process, interval):
            self.calls += 1
            assert process == 123 and interval == 10
            return next(self.results)

    warnings = []
    monkeypatch.setattr(drivers.logger, "warning", lambda *args: warnings.append(args))
    kernel = Kernel()
    assert drivers._wait_for_process(kernel, 123, 10) is None
    assert kernel.calls == 3 and len(warnings) == 1


def test_elevation_cancel_is_returned_for_ui_retry(monkeypatch):
    expected = drivers._ElevatedRun(None, cancelled=True, error="cancelled")
    calls = []
    monkeypatch.setattr(
        drivers, "_shell_execute_pnputil",
        lambda arguments, timeout: calls.append(arguments) or expected)
    result = drivers._run_pnputil_elevated(["/scan-devices"])
    assert result is expected and result.cancelled
    assert calls == [["/scan-devices"]]


def test_scan_is_safe_and_unavailable_off_windows():
    if drivers.os.name != "nt":
        result = drivers.scan_windows_drivers()
        assert not result.available and not result.devices
