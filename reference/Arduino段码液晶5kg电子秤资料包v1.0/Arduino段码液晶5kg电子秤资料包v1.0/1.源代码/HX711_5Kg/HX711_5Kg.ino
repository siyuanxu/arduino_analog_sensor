#include "HX711.h"
#include "SegmentLCD.h"
unsigned long Weight = 0;


void setup()
{
	unsigned char i;
	Init_Hx711();				//初始化HX711模块连接的IO设置
	Init_1621();

	
	for( i = 0 ; i < 6 ; i++ )
	{
		Write_1621_data(5-i,Table_Hello[i]);				//HELLO
	}
	delay(1000);

	Serial.begin(9600);
	Serial.print("Welcome to use!\n");

	Get_Maopi();		//获取毛皮
}

void loop()
{
	unsigned char i;
	Weight = Get_Weight();	//计算放在传感器上的重物重量
	Serial.print(Weight);	//串口显示重量
	Serial.print(" g\n");	//显示单位
	Serial.print("\n");		//显示单位

	if(Flag_Error == 0)
	{
		Write_1621_data(5,0x00);				//不显示
		Write_1621_data(4,0x00);				//不显示
		Write_1621_data(3,num[Weight/1000]);				
		Write_1621_data(2,num[Weight%1000/100]|0x80);		//加小数点	
		Write_1621_data(1,num[Weight%100/10]);
		Write_1621_data(0,num[Weight%10]);
	}
	else
	{
		for( i = 0 ; i < 6 ; i++ )
		{
			Write_1621_data(5-i,Table_Error[i]);				//Error
		}	
	}

	delay(100);				//延时0.1s

}
