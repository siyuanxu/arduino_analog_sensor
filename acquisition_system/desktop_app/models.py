from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

KINDS = ("未配置", "水平位移", "垂直位移", "拉力")


@dataclass
class ChannelConfig:
    enabled: bool = False
    kind: str = "未配置"
    minimum: float = 0.0
    maximum: float = 100.0
    decimals: int = 3
    mode: str = "名义换算"
    raw0: float = 0.0
    value0: float = 0.0
    raw1: float = 1023.0
    value1: float = 100.0

    @property
    def unit(self):
        return "N" if self.kind == "拉力" else "mm" if self.kind in ("水平位移", "垂直位移") else ""

    def validate(self, bits):
        if self.kind not in KINDS or (self.enabled and self.kind == "未配置"):
            raise ValueError("启用通道必须选择传感器类型")
        if type(self.enabled) is not bool or type(self.decimals) is not int or not 0 <= self.decimals <= 6:
            raise ValueError("启用状态或小数位数无效")
        if any(not math.isfinite(float(x)) for x in (self.minimum, self.maximum, self.raw0, self.value0, self.raw1, self.value1)):
            raise ValueError("配置数值必须有限")
        if self.maximum <= self.minimum:
            raise ValueError("量程上限必须大于下限")
        if self.mode not in ("名义换算", "两点标定"):
            raise ValueError("换算模式无效")
        if self.mode == "两点标定" and (self.raw0 == self.raw1 or self.value0 == self.value1 or not 0 <= self.raw0 <= (1 << bits)-1 or not 0 <= self.raw1 <= (1 << bits)-1):
            raise ValueError("两点标定需不同有效原始值及不同物理值")
        if not all(math.isfinite(self.convert(raw, bits)) for raw in (0, (1 << bits)-1)):
            raise ValueError("换算参数过大，计算结果超出有限数范围")

    def convert(self, raw, bits):
        if self.mode == "两点标定":
            return self.value0 + (raw-self.raw0)*(self.value1-self.value0)/(self.raw1-self.raw0)
        return self.minimum + raw / ((1 << bits)-1) * (self.maximum-self.minimum)


def labels(configs):
    counts = {}
    result = []
    for i, c in enumerate(configs):
        if c.enabled and c.kind != "未配置":
            counts[c.kind] = counts.get(c.kind, 0) + 1
            result.append(f"A{i} {c.kind}{counts[c.kind]}")
        else:
            result.append(f"A{i} 未启用")
    return result


def save_config(path, cap, configs):
    data = {"schema_version": 1, "board_model": cap.board_model, "adc_bits": cap.adc_bits, "channels": list(cap.channels), "configs": [asdict(c) for c in configs]}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path, cap):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if (d.get("schema_version"), d.get("board_model"), d.get("adc_bits"), d.get("channels")) != (1, cap.board_model, cap.adc_bits, list(cap.channels)):
        raise ValueError("配置属于其他板型或格式版本；禁止混用")
    cfg = [ChannelConfig(**x) for x in d["configs"]]
    if len(cfg) != cap.channel_count:
        raise ValueError("配置通道数错误")
    for c in cfg:
        c.validate(cap.adc_bits)
    return cfg
