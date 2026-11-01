"""Protocol v1/v2 framing and strict validation. No GUI or serial dependencies."""
from dataclasses import dataclass
import json
import math
import re


class ProtocolError(ValueError):
    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class Capabilities:
    protocol_version: int
    firmware_version: str
    board_model: str
    channel_count: int
    channels: tuple
    adc_bits: int
    sample_interval_ms: int
    filter: str | None = None

    @classmethod
    def parse(cls, payload):
        try:
            d = json.loads(payload)
            if not isinstance(d, dict):
                raise ValueError("INFO 必须为对象")
            for k in ("protocol_version", "channel_count", "adc_bits", "sample_interval_ms"):
                if type(d[k]) is not int:
                    raise ValueError(f"{k} 必须为整数")
            model = d["board_model"]
            if model not in ("UNO_R4_MINIMA", "UNO_R4_WIFI", "MEGA_2560"):
                raise ValueError("未知板型")
            version = d["protocol_version"]
            n, bits = (16, 10) if model == "MEGA_2560" else (6, 14)
            interval = d["sample_interval_ms"]
            allowed_intervals = (1000,) if model == "MEGA_2560" else (500, 1000)
            if (d["channel_count"], d["adc_bits"]) != (n, bits) or interval not in allowed_intervals:
                raise ValueError("能力参数与协议不符")
            filter_name = d.get("filter")
            if version == 1:
                if filter_name is not None:
                    raise ValueError("v1 协议不支持滤波声明")
            elif version == 2:
                if model == "MEGA_2560" or interval != 500 or filter_name != "trim5_200ms_A0_A1":
                    raise ValueError("v2 协议滤波能力不符")
            else:
                raise ValueError("不支持的协议版本")
            if d["channels"] != [f"A{i}" for i in range(n)]:
                raise ValueError("通道顺序不符")
            if not isinstance(d["firmware_version"], str) or not d["firmware_version"]:
                raise ValueError("缺少固件版本")
            return cls(version, d["firmware_version"], model, n, tuple(d["channels"]), bits, interval, filter_name)
        except (ValueError, TypeError, KeyError) as e:
            raise ProtocolError(f"INFO 无效：{e}") from e

    @property
    def header(self):
        if self.protocol_version == 2:
            return [
                "device_time_s",
                *[f"{c}_raw" for c in self.channels],
                "A0_filtered", "A1_filtered", "A0_window", "A1_window",
                "adc_bits",
            ]
        return ["device_time_s", *[f"{c}_raw" for c in self.channels], "adc_bits"]


@dataclass(frozen=True)
class Sample:
    device_time_s: float
    raw: tuple
    adc_bits: int
    filtered: tuple = ()
    windows: tuple = ()

    @property
    def measurement_counts(self):
        """Counts used for display/conversion; raw remains the recorded wire value."""
        if len(self.filtered) != 2 or len(self.raw) < 2:
            return self.raw
        return (*self.filtered, *self.raw[2:])


class LineFramer:
    def __init__(self, maximum=8192):
        self.maximum = maximum
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, chunk):
        """Return lines or ProtocolError objects; discard overlong input until newline."""
        result = []
        for b in chunk:
            if self.discarding:
                if b == 10:
                    self.discarding = False
                continue
            if b == 10:
                try:
                    result.append(bytes(self.buffer).rstrip(b"\r").decode("utf-8", errors="strict"))
                except UnicodeError:
                    result.append(ProtocolError("接收到非法 UTF-8"))
                self.buffer.clear()
            else:
                self.buffer.append(b)
                if len(self.buffer) > self.maximum:
                    self.buffer.clear()
                    self.discarding = True
                    result.append(ProtocolError("串口行过长，已丢弃此坏行"))
        return result


class Parser:
    def __init__(self):
        self.capabilities = None
        self.header = None
        self.last_time = None

    @property
    def ready(self):
        return self.capabilities is not None and self.header == self.capabilities.header

    def parse(self, line):
        if not line:
            return None
        if line.startswith("#INFO "):
            cap = Capabilities.parse(line[6:])
            if self.capabilities and cap != self.capabilities:
                self.capabilities = cap
                self.header = None
                self.last_time = None
                raise ProtocolError("设备能力改变，必须重新连接确认", fatal=True)
            self.capabilities = cap
            if self.header is not None and self.header != cap.header:
                raise ProtocolError("HEADER 与 INFO 不匹配", fatal=True)
            return cap
        if line.startswith("#HEADER "):
            header = line[8:].split(",")
            if self.capabilities and header != self.capabilities.header:
                raise ProtocolError("HEADER 与设备能力不匹配", fatal=True)
            if self.header and header != self.header:
                raise ProtocolError("HEADER 发生变化", fatal=True)
            self.header = header
            return "header"
        if line.startswith("#"):
            raise ProtocolError(f"设备消息：{line}")
        if not self.ready:
            raise ProtocolError("握手未完成，忽略采样行")
        cells = line.split(",")
        cap = self.capabilities
        expected_cells = cap.channel_count + (6 if cap.protocol_version == 2 else 2)
        if len(cells) != expected_cells:
            raise ProtocolError("采样列数错误")
        try:
            t = float(cells[0])
            if not math.isfinite(t) or t < 0:
                raise ValueError("时间必须为非负有限数")
            if cap.protocol_version == 1:
                if not all(re.fullmatch(r"[0-9]+", c) for c in cells[1:]):
                    raise ValueError("原始值及位数必须为非负整数")
                values = [int(c) for c in cells[1:]]
                raw, filtered, windows, adc_bits = tuple(values[:-1]), (), (), values[-1]
            else:
                integer_cells = cells[1:1 + cap.channel_count + 2] + [cells[-1]]
                if not all(re.fullmatch(r"[0-9]+", c) for c in integer_cells):
                    raise ValueError("原始值、滤值及位数必须为非负整数")
                raw = tuple(int(c) for c in cells[1:1 + cap.channel_count])
                filtered = tuple(int(c) for c in cells[1 + cap.channel_count:3 + cap.channel_count])
                windows = tuple(self._parse_window(c) for c in cells[3 + cap.channel_count:5 + cap.channel_count])
                adc_bits = int(cells[-1])
                if raw[:2] != tuple(window[-1] for window in windows):
                    raise ValueError("窗口末点与 A0/A1 原始值不一致")
                expected_filtered = tuple(self._trimmed_mean(window) for window in windows)
                if filtered != expected_filtered:
                    raise ValueError("固件滤值与 5 点去极值均值不一致")
            if adc_bits != cap.adc_bits:
                raise ProtocolError("采样 ADC 位数改变", fatal=True)
            maximum = (1 << cap.adc_bits) - 1
            if any(x > maximum for x in (*raw, *filtered, *(x for window in windows for x in window))):
                raise ValueError("采样值超出 ADC 范围")
            if self.last_time is not None and t <= self.last_time:
                raise ProtocolError("设备时间倒退/重复：疑似重启或计时异常", fatal=True)
            self.last_time = t
            return Sample(t, raw, adc_bits, filtered, windows)
        except ValueError as e:
            if isinstance(e, ProtocolError):
                raise
            raise ProtocolError(f"坏帧：{e}") from e

    @staticmethod
    def _parse_window(cell):
        parts = cell.split(";")
        if len(parts) != 5 or not all(re.fullmatch(r"[0-9]+", part) for part in parts):
            raise ValueError("滤波窗口必须是 5 个分号分隔的非负整数")
        return tuple(int(part) for part in parts)

    @staticmethod
    def _trimmed_mean(window):
        # Five integers minus one minimum and one maximum leaves three values.
        # Adding one implements nearest-integer rounding for division by three.
        return (sum(window) - min(window) - max(window) + 1) // 3
