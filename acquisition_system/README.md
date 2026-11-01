# 直剪试验数据采集软件

Siyuan XU studio · NHRI

通过 USB 从 Arduino UNO R4 Minima、UNO R4 WiFi 或 Mega 2560 采集模拟信号，按通道换算位移和拉力，实时绘图，并分别保存原始数据和测试结果。

## 下载和使用

1. 在 [GitHub Releases](https://github.com/siyuanxu/arduino_analog_sensor/releases) 选择对应系统的桌面包。macOS Apple Silicon 使用 arm64 版本，解压后打开 ShearAcquisition.app。Windows x64 请完整解压并保留同目录的依赖文件，然后运行 ShearAcquisition.exe。
2. 首次使用开发板时，可下载同一发布页的 Arduino 固件包。在 Arduino IDE 中打开与板型对应的草图，选择正确的开发板和串口后上传。UNO R4 使用 r4_adc，Mega 2560 使用 mega_adc。烧录前请先结束当前记录。
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
