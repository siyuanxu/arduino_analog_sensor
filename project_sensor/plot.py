import serial
import numpy as np
import matplotlib.pyplot as plt


plt.ion()
# open serial port
ser = serial.Serial(port='COM6', baudrate=9600)
v = []
      
plt.ion()
i = 0
while ser.isOpen():
    val = ser.readline()
    v.append(int(val))
    plt.plot(v, '-r')
    plt.pause(0.005)
