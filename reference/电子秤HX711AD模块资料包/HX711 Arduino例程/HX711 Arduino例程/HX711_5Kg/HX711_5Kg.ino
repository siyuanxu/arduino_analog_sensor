#include "HX711.h"
unsigned int Weight = 0;

void setup()
{
	Init_Hx711();				//初始化HX711模块连接的IO设置

	Serial.begin(9600);
	Serial.print("Welcome to use!\n");

	Get_Maopi();		//获取毛皮
}

void loop()
{
	Weight = Get_Weight();	//计算放在传感器上的重物重量
	Serial.print(Weight);	//串口显示重量
	Serial.print(" g\n");	//显示单位
	Serial.print("\n");		//显示单位
	delay(1000);				//延时1s

}
