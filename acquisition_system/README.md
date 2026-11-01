# 直剪试验数据采集软件

Siyuan XU studio · NHRI

通过 USB 从 Arduino UNO R4 Minima、UNO R4 WiFi 或 Mega 2560 采集模拟信号，按通道换算位移和拉力，实时绘图，并分别保存原始数据和测试结果。

## 下载和使用

1. Windows 10/11 x64 使用 [1.2.7 Windows 软件包](https://github.com/siyuanxu/arduino_analog_sensor/releases/download/v1.2.7/ShearAcquisition-1.2.7-windows-x64.zip)。完整解压后打开 `ShearAcquisition/ShearAcquisition.exe`。Mac 用户可下载 [1.2.7 Apple Silicon 版](https://github.com/siyuanxu/arduino_analog_sensor/releases/download/v1.2.7/ShearAcquisition-1.2.7-macOS-arm64.zip)。
2. 对已烧好采集固件的板子，插入 USB 后在软件中选择端口并点击「连接」即可读取，不需要安装 Arduino IDE。Windows 在原装 UNO R4 Minima 或 Mega 2560 的采集接口明确缺少驱动时才请求管理员协助。正常设备直接使用；需要时按提示重新插拔 USB。R4 使用系统内置 USB 串口支持，Windows 包附有适用的原装 Mega 官方驱动。首次准备空白板的人员可取用[配套固件](https://github.com/siyuanxu/arduino_analog_sensor/releases/download/v1.2.5/ShearAcquisition-1.2.5-Arduino-firmware.zip)。
3. 在“连接与配置”中选择设备和实际使用的通道，填写传感器量程、单位和小数位。设好 CSV 保存位置后，在“数据采集”中清零并开始记录。
4. 结束记录后，“成果交付”页可查看两个 CSV。原始文件还保留通道参数与清零事件。

| 开发板 | 模拟输入 | 输出间隔 |
| --- | --- | --- |
| UNO R4 Minima / WiFi | A0～A5，14 位 | 0.5 秒 |
| Mega 2560 | A0～A15，10 位 | 1 秒 |

R4 固件在 A0 和 A1 上每 200 ms 采样，剔除最近五点中的一个最高值和一个最低值，再平均。桌面端采用处理后的值换算，同时保存原始窗口。工程量准确度取决于传感器、供电与标定。

## 从源码启动

安装 Python 3.12，在本目录创建并激活独立环境后运行：

```shell
python -m pip install -r desktop_app/requirements.txt
python -m desktop_app
```

[串口协议](PROTOCOL.md) · [UNO R4 固件](firmware/r4_adc) · [Mega 2560 固件](firmware/mega_adc)

## 数据保存

开始记录后逐点追加两份 CSV，每约 10 秒向系统请求磁盘同步，正常结束时再次同步。磁盘写入阻塞超过 3 秒会显示等待提示。突然断电仍受磁盘和系统状态影响；正在写入的最后一条以及尚未同步的数据可能丢失。
