"""受限的 Windows Arduino 驱动检查与修复。"""
from dataclasses import dataclass
import ctypes
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading
import time
from typing import Callable

from .logging_support import get_logger


logger = get_logger("windows_drivers")
MISSING_DRIVER_CODE = 28
R4_RUNTIME_ID = "USB\\VID_2341&PID_0069&MI_00\\"
R4_BOOTLOADER_ID = "USB\\VID_2341&PID_0369\\"
MEGA_IDENTITIES = {
    ("2341", "0042"): "arduino.inf",
    ("2341", "0010"): "arduino.inf",
    ("2A03", "0042"): "usbser.inf",
    ("2A03", "0010"): "usbser.inf",
}
MEGA_DRIVER_HASHES = {
    "arduino.inf": "b0a328e4c091c99bb5383f6ed1b1bb4ffb7294290a3d387dddbb1921cca6ffc3",
    "arduino.cat": "25f58519bc9ee9968607d8f0d8375f4736d30b17ff612d43c806b03f9460b4e3",
}


@dataclass(frozen=True)
class DriverDevice:
    board_model: str
    label: str
    instance_id: str
    name: str
    problem_code: int
    role: str
    repairable: bool
    driver_package: str | None = None


@dataclass(frozen=True)
class DriverScan:
    available: bool
    devices: tuple[DriverDevice, ...] = ()
    error: str | None = None

    @property
    def missing(self):
        return tuple(device for device in self.devices if device.repairable)

    @property
    def bootloaders(self):
        return tuple(device for device in self.devices if device.role == "bootloader")


@dataclass(frozen=True)
class DriverRepairResult:
    success: bool
    cancelled: bool
    message: str
    device: DriverDevice
    staged: bool = False
    restart_required: bool = False
    replug_required: bool = False


@dataclass(frozen=True)
class _ElevatedRun:
    exit_code: int | None
    cancelled: bool = False
    error: str | None = None


_PNP_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
@(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Present -eq $true } |
  ForEach-Object {
    [PSCustomObject]@{
      instance_id = [string]$_.PNPDeviceID
      name = [string]$_.Name
      problem_code = [int]$_.ConfigManagerErrorCode
    }
  }) | ConvertTo-Json -Compress
""".strip()


def _system_directory():
    """从 Windows API 取得可信系统目录，不读取可被覆盖的环境变量。"""
    if os.name != "nt":
        return Path(r"C:\Windows\System32")
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "无法取得 Windows 系统目录")
    return Path(buffer.value)


def _decode_pnp_json(text):
    payload = json.loads(text or "[]")
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("设备枚举结果不是列表")
    records = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        instance_id = str(item.get("instance_id") or "").strip().upper()
        if not instance_id:
            continue
        try:
            problem_code = int(item.get("problem_code") or 0)
        except (TypeError, ValueError):
            problem_code = -1
        records.append((instance_id, str(item.get("name") or "").strip(), problem_code))
    return records


def _classify_device(instance_id, name, problem_code):
    """只识别原装板的精确运行接口，排除复合父设备和 DFU 接口。"""
    instance_id = str(instance_id).strip().upper()
    name = str(name or "").strip()
    try:
        problem_code = int(problem_code)
    except (TypeError, ValueError):
        problem_code = -1
    if instance_id.startswith(R4_RUNTIME_ID):
        return DriverDevice(
            "UNO_R4_MINIMA", "Arduino UNO R4 Minima", instance_id,
            name or "Arduino UNO R4 Minima", problem_code, "runtime",
            problem_code == MISSING_DRIVER_CODE, "usbser.inf")
    if instance_id.startswith(R4_BOOTLOADER_ID):
        return DriverDevice(
            "UNO_R4_MINIMA", "Arduino UNO R4 Minima", instance_id,
            name or "Arduino UNO R4 Minima bootloader", problem_code,
            "bootloader", False, None)
    if not instance_id.startswith("USB\\VID_") or "&MI_" in instance_id:
        return None
    first = instance_id.split("\\", 2)[1]
    fields = dict(field.split("_", 1) for field in first.split("&") if "_" in field)
    package = MEGA_IDENTITIES.get((fields.get("VID"), fields.get("PID")))
    if package is None:
        return None
    return DriverDevice(
        "MEGA_2560", "Arduino Mega 2560", instance_id,
        name or "Arduino Mega 2560", problem_code, "runtime",
        problem_code == MISSING_DRIVER_CODE, package)


def scan_windows_drivers():
    """只读枚举当前实际连接的受支持设备。"""
    if os.name != "nt":
        return DriverScan(False, error="驱动检查仅适用于 Windows")
    try:
        powershell = _system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        completed = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", _PNP_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False, creationflags=0x08000000)
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise OSError(detail or f"PowerShell 返回 {completed.returncode}")
        devices = tuple(device for device in
                        (_classify_device(*record)
                         for record in _decode_pnp_json(completed.stdout))
                        if device is not None)
        return DriverScan(True, devices)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Windows 驱动枚举失败：%s", error)
        return DriverScan(False, error=f"无法检查 Windows 设备驱动：{error}")


def _default_driver_root():
    frozen_root = getattr(sys, "_MEIPASS", None)
    base = Path(frozen_root) if frozen_root else Path(__file__).parent
    return base / "drivers" / "arduino-mega2560-official"


def _windows_build():
    if os.name != "nt":
        return None
    try:
        return int(sys.getwindowsversion().build)
    except (AttributeError, TypeError, ValueError):
        return None


def _supports_targeted_scan():
    build = _windows_build()
    return build is not None and build >= 19041


def _driver_path(device):
    if device.driver_package == "usbser.inf":
        raise ValueError("该设备使用 Windows 内置驱动，不加载外部 INF")
    root = _default_driver_root().resolve()
    package = device.driver_package
    if package != "arduino.inf":
        raise ValueError("该设备没有允许安装的 Mega 驱动包")
    path = (root / package).resolve()
    path.relative_to(root)
    catalog = path.with_suffix(".cat")
    if not path.is_file() or not catalog.is_file():
        raise FileNotFoundError(f"安装包缺少 Arduino 官方驱动文件：{package}")
    for candidate in (path, catalog):
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != MEGA_DRIVER_HASHES[candidate.name]:
            raise ValueError(f"Arduino 官方驱动校验失败：{candidate.name}")
    return path


def _shell_execute_pnputil(arguments, timeout_seconds):
    """Windows ShellExecute worker；由 daemon 线程隔离 UAC 等待。"""
    if os.name != "nt":
        return _ElevatedRun(None, error="驱动安装仅适用于 Windows")
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    try:
        pnputil = _system_directory() / "pnputil.exe"
    except OSError as error:
        return _ElevatedRun(None, error=str(error))
    if not pnputil.is_file():
        return _ElevatedRun(None, error="Windows 系统未提供 pnputil.exe")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040
    info.lpVerb = "runas"
    info.lpFile = str(pnputil)
    info.lpParameters = subprocess.list2cmdline([str(value) for value in arguments])
    info.nShow = 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            return _ElevatedRun(None, cancelled=True, error="用户取消了管理员授权")
        return _ElevatedRun(None, error=f"无法启动驱动安装（Windows 错误 {code}）")
    try:
        wait_result = kernel32.WaitForSingleObject(info.hProcess, int(timeout_seconds * 1000))
        if wait_result == 0x00000102:
            return _ElevatedRun(None, error="驱动操作等待超过 120 秒；可稍后重新检查")
        if wait_result != 0:
            return _ElevatedRun(None, error=f"等待驱动操作失败（Windows 结果 {wait_result}）")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            return _ElevatedRun(None, error="无法取得驱动安装结果")
        return _ElevatedRun(int(exit_code.value))
    finally:
        kernel32.CloseHandle(info.hProcess)


def _run_pnputil_elevated(arguments, timeout_seconds=120):
    """有界等待提权。未处理的 UAC 不会阻碍应用进程退出。"""
    results = Queue(maxsize=1)

    def worker():
        results.put(_shell_execute_pnputil(arguments, timeout_seconds))

    threading.Thread(target=worker, daemon=True, name="pnputil elevation").start()
    try:
        return results.get(timeout=max(0.01, timeout_seconds) + 0.05)
    except Empty:
        return _ElevatedRun(None, error=f"管理员授权或驱动操作等待超过 {timeout_seconds} 秒；可稍后重试")


def _resolved_device(scan, expected):
    exact = next((device for device in scan.devices
                  if device.instance_id == expected.instance_id), None)
    return exact if exact is not None and exact.problem_code == 0 else None


def _poll_resolution(scanner, expected, timeout):
    deadline = time.monotonic() + max(0, timeout)
    last = DriverScan(True)
    while True:
        last = scanner()
        if not last.available:
            return None, last
        resolved = _resolved_device(last, expected)
        if resolved is not None:
            return resolved, last
        if time.monotonic() >= deadline:
            return None, last
        time.sleep(0.2)


def _unresolved_result(current, scan, *, staged=False):
    if not scan.available:
        return DriverRepairResult(
            False, False, "驱动命令已完成，但无法重新检查设备状态", current, staged)
    exact = next((device for device in scan.devices
                  if device.instance_id == current.instance_id), None)
    if exact is not None and exact.problem_code == MISSING_DRIVER_CODE:
        message = "Windows 已执行设备刷新，但设备仍报告缺少驱动；请重新插拔 USB 后再检查"
        return DriverRepairResult(
            False, False, message, exact, staged, replug_required=True)
    if exact is not None:
        message = (f"驱动处理后设备报告错误代码 {exact.problem_code}；"
                   "软件不会继续自动安装，请检查设备或 USB 连接")
        return DriverRepairResult(False, False, message, exact, staged)
    else:
        message = "设备刷新后未重新出现；请检查 USB 连接，重新插拔后再检查"
        return DriverRepairResult(
            False, False, message, current, staged, replug_required=True)


def repair_windows_driver(
        device: DriverDevice,
        *,
        scanner: Callable[[], DriverScan] = scan_windows_drivers,
        elevated_runner: Callable[[list[str]], _ElevatedRun] = _run_pnputil_elevated,
        poll_timeout=2.5):
    """修复一个仍在线的 Code 28 运行态设备，并以重新枚举确认结果。"""
    before = scanner()
    current = next((candidate for candidate in before.devices
                    if candidate.instance_id == device.instance_id), None)
    if not before.available:
        return DriverRepairResult(False, False, before.error or "驱动检查不可用", device)
    if current is None:
        return DriverRepairResult(False, False, "设备已拔出或状态已经改变，请重新检查", device)
    if current.role != "runtime" or current.problem_code != MISSING_DRIVER_CODE or not current.repairable:
        return DriverRepairResult(False, False, "仅能修复当前缺少驱动的运行态设备", current)
    # R4 and the original Arduino.org Mega identity are standard CDC devices.
    # Re-enumerate only this exact instance so Windows can bind inbox usbser.
    if current.driver_package == "usbser.inf":
        if not _supports_targeted_scan():
            return DriverRepairResult(
                False, False,
                f"此 Windows 版本不支持定向设备刷新；请重新插拔 {current.label} 的 USB 后再检查",
                current, replug_required=True)
        logger.info("请求 Windows 定向刷新内置 CDC 驱动：board=%s instance=%s",
                    current.board_model, current.instance_id)
        operation = elevated_runner(["/scan-devices", "/instanceid", current.instance_id])
        if operation.cancelled:
            return DriverRepairResult(False, True, "已取消管理员授权，设备没有刷新", current)
        if operation.exit_code == 3010:
            return DriverRepairResult(
                False, False, "Windows 要求重启后完成 R4 驱动设置", current,
                restart_required=True)
        if operation.error or operation.exit_code != 0:
            detail = operation.error or f"pnputil 返回 {operation.exit_code}"
            return DriverRepairResult(False, False, f"设备刷新未完成：{detail}", current)
        resolved, after = _poll_resolution(scanner, current, poll_timeout)
        if resolved is not None:
            return DriverRepairResult(True, False, f"{current.label} 驱动已经就绪", resolved)
        return _unresolved_result(current, after)

    # Stage the signed Arduino package without /install.  /install can bind all
    # matching devices in the INF, while the following scan targets one instance.
    try:
        driver = _driver_path(current)
    except (OSError, ValueError) as error:
        return DriverRepairResult(False, False, str(error), current)
    logger.info("请求 Windows 暂存官方 Mega 驱动：instance=%s inf=%s",
                current.instance_id, driver.name)
    staged = elevated_runner(["/add-driver", str(driver)])
    if staged.cancelled:
        return DriverRepairResult(False, True, "已取消管理员授权，驱动没有加入 Windows", current)
    if staged.exit_code == 3010:
        return DriverRepairResult(
            False, False, "官方 Mega 驱动已加入 Windows，系统要求重启后完成设置",
            current, staged=True, restart_required=True)
    if staged.error or staged.exit_code != 0:
        detail = staged.error or f"pnputil 返回 {staged.exit_code}"
        return DriverRepairResult(False, False, f"驱动没有加入 Windows：{detail}", current)

    immediate = scanner()
    resolved = _resolved_device(immediate, current) if immediate.available else None
    if resolved is not None:
        return DriverRepairResult(True, False, f"{current.label} 驱动已经就绪", resolved, staged=True)

    if not _supports_targeted_scan():
        return DriverRepairResult(
            False, False,
            "官方 Mega 驱动已加入 Windows；此系统不支持定向刷新，请重新插拔 USB",
            current, staged=True, replug_required=True)

    refreshed = elevated_runner(["/scan-devices", "/instanceid", current.instance_id])
    if refreshed.cancelled:
        return DriverRepairResult(
            False, True, "官方驱动已经加入 Windows；定向刷新授权已取消，请重新插拔 USB",
            current, staged=True, replug_required=True)
    if refreshed.exit_code == 3010:
        return DriverRepairResult(
            False, False, "官方 Mega 驱动已加入 Windows，系统要求重启后完成设置",
            current, staged=True, restart_required=True)
    if refreshed.error or refreshed.exit_code != 0:
        detail = refreshed.error or f"pnputil 返回 {refreshed.exit_code}"
        return DriverRepairResult(
            False, False, f"官方驱动已加入 Windows，但设备刷新未完成：{detail}；请重新插拔 USB",
            current, staged=True, replug_required=True)
    resolved, after = _poll_resolution(scanner, current, poll_timeout)
    if resolved is not None:
        return DriverRepairResult(
            True, False, f"{current.label} 驱动已经就绪", resolved, staged=True)
    return _unresolved_result(current, after, staged=True)
