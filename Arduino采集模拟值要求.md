## 目的

采用单片机读取常见的拉压传感器、位移传感器的原始AD值并输出到串口。

## 硬件资料

1. 位移传感器、拉力传感器采用常用的电桥式传感器（红黑绿白四线接头），例如[200kN拉力传感器](https://item.taobao.com/item.htm?spm=a1z09.2.0.0.O6lQbX&id=20480243402&_u=emvmhqj9d63)；[100~200mm位移传感器](https://item.taobao.com/item.htm?id=14554997081&_u=t2dmg8j26111)，但不局限与上述两个；

2. 放大芯片和数模转换芯片暂时采用的是[AD620](https://item.taobao.com/item.htm?spm=a1z09.2.0.0.zbwg8r&id=40452620676&_u=emvmhqjb3a3)以及[HX711](https://item.taobao.com/item.htm?spm=a1z09.2.0.0.zbwg8r&id=19276424320&_u=emvmhqjbc6d)。本人自行购买的可能相互的适应性不好，你可以在相似的价格区间内进行挑选；

3. 单片机现在使用的是Arduino UNO R3，如果没有性能上的短板就不必更换了，因为考虑到Arduino社区环境比活跃，相关资料也更好取得；

   ​

## 要求

1. 如果可以，希望加入多通道同时采集功能；
2. 单片机串口输出RAW AD值即可，传感器标定在上位机上进行；
3. 采集频率最好在5Hz以上；
4. 供电部分轻量化，最好使用干电池或者充电宝供电。

