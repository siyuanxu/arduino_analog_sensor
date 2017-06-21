import serial
import numpy as np
import matplotlib.pyplot as plt


plt.ion()
# open serial port
ser = serial.Serial(port='COM6', baudrate=9600)
v = []
pv = []
plt.ion()
i = 0
while ser.isOpen():
    val = ser.readline()
    print(val)
    # v.append(float(val))
    # i += 1
    # if i == 2:
    #     pv.append(val)
    #     i = 0
    #     plt.plot(pv, '-r')
    #     plt.pause(0.1)
