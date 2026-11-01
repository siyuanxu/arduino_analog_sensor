# 直剪采集串口协议 v1 / v2

本文件是电脑软件、R4固件、Mega固件共享的实现契约。串口115200、8N1、无流控，ASCII/UTF-8文本，LF结束，接收兼容CRLF。

## 能力与控制消息

命令为单行 `INFO` 或 `HEADER`；`H` 可作为HEADER别名。连接后电脑主动请求INFO、HEADER。固件启动时亦尽可能各输出一次；不得无限等待USB连接。未知命令可回复 `#ERROR UNKNOWN_COMMAND`。

INFO响应：`#INFO ` 后接单行合法JSON，必须包含：

- `protocol_version`：旧 R4 和 Mega 为 1，带固件去尖峰的 R4 为 2。
- `firmware_version`: 版本字符串。R4当前为"1.2.0"，Mega当前为"1.0.0"。
- `board_model`: "UNO_R4_MINIMA"、"UNO_R4_WIFI"或"MEGA_2560"。
- `channel_count`: 6或16，必须与channels一致。
- `channels`: 顺序明确的字符串列表，R4为A0至A5，Mega为A0至A15。
- `adc_bits`: R4为14，Mega为10。
- `sample_interval_ms`: 正整数。R4当前为500，Mega当前为1000。

v2 INFO 另含 `"filter":"trim5_200ms_A0_A1"`，仅适用于 R4 的 14 位、500 ms 输出。

v1 HEADER响应：`#HEADER ` 后接CSV列名，格式为 `device_time_s,A0_raw,A1_raw,...,adc_bits`，中间按channels顺序包含全部通道；此处省略号仅为文档示意，不得在线上传输省略号。

## 采样帧

v1 按握手声明的周期发出设备时间、所有通道的原始 ADC 码和位数，R4 为 8 列，Mega 为 18 列。新桌面端仍接收 v1。

v2 R4 在板上每 200 ms 依次采一对 A0、A1。两路分别使用最近 5 个真实读数，从各自窗口中扣除一个最大值和一个最小值，再将其余三项的平均值四舍五入到整数 ADC 码。重复的极值只各扣一次。5 项占一个名义 1 秒采样窗口，首末测点间隔 0.8 秒。窗口随子采样滑动，相邻 500 ms 输出帧的窗口会重叠。满窗后发帧；出现漏采时重新收集子样本，无补点。A2～A5 在输出帧采集。板上仅处理 ADC 码，工程量换算和清零仍在电脑端执行。

v2 每 500 ms 输出 12 列。

```
#HEADER device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,A0_filtered,A1_filtered,A0_window,A1_window,adc_bits
```

A0_raw、A1_raw 是各自窗口最新的实际 ADC 读数；两个 filtered 字段是固件处理后的 ADC 码。每个 window 字段包含按旧到新排列的 5 个原始整数，以半角分号分隔。时间戳为设备输出帧时间，不能当成 5 个子样本同时采到的时间。电脑端核对窗口、最新原始码及处理值的一致性，使用固件处理值计算 A0、A1 的位移/拉力，A2～A5 按直接采样值换算。

串口输出不等待 USB 主机，按设备时钟调度，处理 millis 回绕并跳过错过的发送机会。

接收端验证INFO、HEADER、数据列数、时间、整数和ADC范围的一致性；无效帧不用于物理量。重复表头和INFO不能作为测值。设备时间倒退应视为重启/计时异常；正在记录时结束本次记录，保留已写入文件。重复或非递增时间不得制造正常连续测值。仅依据时间间隔提示疑似缺帧，不宣称可以准确统计全部漏帧。

## v1 兼容示例（非实测）

```
#INFO {"protocol_version":1,"firmware_version":"1.1.0","board_model":"UNO_R4_MINIMA","channel_count":6,"channels":["A0","A1","A2","A3","A4","A5"],"adc_bits":14,"sample_interval_ms":500}
#HEADER device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,adc_bits
0.500,0,100,200,300,400,16383,14
1.000,1,101,201,301,401,16382,14
```

## 电脑侧保存

原始 CSV 保留文件参数、事件、每帧的全部原始通道。v2 还保存两个五点窗口和板端处理结果。测试结果 CSV 每帧保存所选通道的工程量（包含清零偏置）。因此可以从同名原始文件复查尖峰和复算结果。两个文件的记录时间均从点击「开始记录」起算；中途清零不重置文件时间。

记录期间锁定量程、类型、启用状态、标定、单位和显示精度等配置（曲线可见性允许独立切换），清零走事件机制。禁止覆盖既有文件。设备断线/时间倒退/能力改变/磁盘错误须可见并停止当前记录；结束后可继续预览。
