/*
 * library is moded by siyuanxu https://github.com/siyuanxu/DFRobot_ADS1115_mod
 * when 2/3x gain mode is used, 1bit=0.1875mv and the voltage range is +-6.04V
 */
#include <Wire.h>
#include <DFRobot_ADS1115.h>

DFRobot_ADS1115 ads;

void setup(void) 
{
    Serial.begin(115200);
    ads.setAddr_ADS1115(ADS1115_IIC_ADDRESS0);   // 0x48
    ads.setGain(eGAIN_TWOTHIRDS);   // 2/3x gain
    ads.setMode(eMODE_SINGLE);       // single-shot mode
    ads.setRate(eRATE_128);          // 128SPS (default)
    ads.setOSMode(eOSMODE_SINGLE);   // Set to start a single-conversion
    ads.init();
}

void loop(void) 
{
    if (ads.checkADS1115())
    {
        int adc0, adc1, adc2, adc3;
        adc0 = ads.readbit(0); //turn AD into N(force)
//        Serial.print("v1=");
        Serial.print(adc0);
        Serial.print(" ");
        delay(10);
        adc1 = ads.readbit(1);
//        Serial.print("v2=");
        Serial.print(adc1);
        Serial.print(" ");
        delay(10);
        adc2 = ads.readbit(2);
//        Serial.print("v3=");
        Serial.print(adc2);
        Serial.print(" ");
        delay(10);
        adc3 = ads.readbit(3);
//        Serial.print("v4=");
        Serial.print(adc3);
        Serial.println("");
        delay(10);
    }
    else
    {
        Serial.println("Analog/Digital Convertor is Disconnected!");
    }

}       
