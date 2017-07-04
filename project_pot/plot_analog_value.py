import serial
import numpy as np
import matplotlib.pyplot as plt


def dynamic_update_plot(line, x, y, new_x, new_y):
    ax = plt.gca()
    x = np.append(x, new_x)
    y = np.append(y, new_y)
    line.set_xdata(x)
    line.set_ydata(y)
    ax.relim()
    ax.autoscale_view(True, True, True)
    plt.draw()
    plt.pause(1e-17)
    return x, y


plt.ion()
# open serial port
ser = serial.Serial(port='COM6', baudrate=9600)

pv = np.array([])
x = np.array([])
ax = plt.gca()

line, = plt.plot(x, pv)
i = 0
while ser.isOpen():

    i += 1

    val = int(ser.readline())

    x, pv = dynamic_update_plot(line, x, pv, i, val)
