import numpy as np
import serial.tools.list_ports as listports
import serial
import matplotlib.pyplot as plt
from tkinter import *


# coefficient and state parameters
force_coef = 7.5
disp_coef = 0.00385
test_demo = False
running = True  # global running flag
balance_position = [0, 0, 0, 0]
plt.rcParams["figure.figsize"] = [16,9]


# Find Arduino Uno board and open the port
def find_arduino():
    ports = listports.comports()
    Arduino_ports = [port for port in ports if 'Arduino' in port.description]
    print(len(Arduino_ports))
    if len(Arduino_ports) > 1:
        print('Multiple Devices are connected')
        [print(port.description) for port in Arduino_ports]
        return
    elif len(Arduino_ports) == 0:
        print('Device Not detected')
    else:
        Arduino_port = Arduino_ports[0].device

    return Arduino_port


# start a test demo
test_demo = False
if test_demo:
    # open uno port
    ser = serial.Serial(port=find_arduino(), baudrate=115200)
    # skip the first 20 lines
    print('dumping the first 10 lines')
    [ser.readline() for i in range(10)]
    print('serial read start')
    data_line = [int(i) for i in str(ser.readline())[2:-5].split(' ')]
    print(data_line)
    ser.close()

# values update function

vals = [[], [], [], []]  # initiate
index = []

# open uno port
ser = serial.Serial(port=find_arduino(), baudrate=115200)


def vals_update(vals, ser, force_coef, disp_coef):
    global balance_position
    data_line = np.array([int(i) for i in str(ser.readline())[
                         2:-5].split(' ')]) - np.array(balance_position)
    vals[0].append(data_line[0] * force_coef)
    vals[1].append(data_line[1] * disp_coef)
    vals[2].append(data_line[2] * disp_coef)
    vals[3].append(data_line[3] * disp_coef)
    return vals


plt.ion()
# plt.show()

fig, [[ax_0, ax_1], [ax_2, ax_3]] = plt.subplots(2, 2, sharex=True)

line_0, = ax_0.plot(index, [], label='Force (N)')
line_1, = ax_1.plot(index, [], label='Disp1 (mm)')
line_2, = ax_2.plot(index, [], label='Disp2 (mm)')
line_3, = ax_3.plot(index, [], label='Disp3 (mm)')

ax_0.legend(loc='upper left')
ax_1.legend(loc='upper left')
ax_2.legend(loc='upper left')
ax_3.legend(loc='upper left')

# skip the first 20 lines
print('dumping the first 10 lines')
[ser.readline() for i in range(10)]
print('serial port read start')

################# Control #########################


def scanning():
    global vals, ser, force_coef, disp_coef, line_0, line_1, line_2, line_3, ax_0, ax_1, ax_2, ax_3, fig, running
    
    while running:
        vals = vals_update(vals, ser, force_coef, disp_coef)
        # print(vals)
        line_0.set_ydata(vals[0])
        line_1.set_ydata(vals[1])
        line_2.set_ydata(vals[2])
        line_3.set_ydata(vals[3])

        index = np.arange(0, len(vals[0]), 1)
        line_0.set_xdata(index)
        line_1.set_xdata(index)
        line_2.set_xdata(index)
        line_3.set_xdata(index)

        for ax in [ax_0, ax_1, ax_2, ax_3]:
            ax.relim()
            ax.autoscale_view()
        fig.tight_layout()
        fig.canvas.draw_idle()
        fig.canvas.flush_events()

    import pandas as pd
    df = pd.DataFrame()
    df['CH0(N)'] = vals[0]
    df['CH1(mm)'] = vals[1]
    df['CH2(mm)'] = vals[2]
    df['CH3(mm)'] = vals[3]

    import datetime
    name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    df.to_csv(f'{name}.csv', index=False, float_format = '%.2f')
    plt.close()
    root.destroy()

def clear():
    global vals
    vals=[[], [], [], []]
    return


def balance():
    global balance_position
    balance_position = [int(i) for i in str(ser.readline())[2:-5].split(' ')]
    return


def save_stop():
    global running
    running = False
    return


root = Tk()
# root.title("Title")
root.geometry("100x550")

app = Frame(root)
app.grid()

save_stop = Button(app, fg='white', bg='blue',text='Save\nstop', font=10,width=10,height=10,command=save_stop)
clear = Button(app, fg='white', bg='red',text='clear', font=10,width=10,height=10,command=clear)
balance = Button(app, fg='white', bg='blue', text='balance', font=10,width=10,height=10,command=balance)

balance .grid()
clear .grid()
save_stop.grid()



root.after(10, scanning)  # After 0.1 second, call scanning
root.mainloop()
