import serial
import matplotlib.pyplot as plt
from tkinter import *
import time

activate = [0, 1]

activate_alpha = []

for i in [0, 1, 2, 3, 4, 5]:
    if i in activate:
        activate_alpha.append(1)
    else:
        activate_alpha.append(0)

ser = serial.Serial(port='COM3', baudrate=9600)
idx = []
vals = [[] for i in range(6)]

plt.ion()
fig, ax = plt.subplots()
line0, = ax.plot(idx, vals[0], label='Sensor 0', alpha=activate_alpha[0])
line1, = ax.plot(idx, vals[1], label='Sensor 1', alpha=activate_alpha[1])
line2, = ax.plot(idx, vals[2], label='Sensor 2', alpha=activate_alpha[2])
line3, = ax.plot(idx, vals[3], label='Sensor 3', alpha=activate_alpha[3])
line4, = ax.plot(idx, vals[4], label='Sensor 4', alpha=activate_alpha[4])
line5, = ax.plot(idx, vals[5], label='Sensor 5', alpha=activate_alpha[5])
ax.legend(loc=3)
lines = [line0, line1, line2, line3, line4, line5]

zero_hights = [0 for i in range(6)]


def update_serial_val(vals, ser):
    global zero_hights

    # one_batch = [int(ser.readline()) - zero_hights[i] for i in range(6)]
    trigger = int(ser.readline())
    while trigger != -1:
        trigger = int(ser.readline())
    
    one_batch = [int(ser.readline()) for i in range(6)]

    vals[0].append(one_batch[0])
    vals[1].append(one_batch[1])
    vals[2].append(one_batch[2])
    vals[3].append(one_batch[3])
    vals[4].append(one_batch[4])
    vals[5].append(one_batch[5])

    # time.sleep(0.1)

    idx = [i for i in range(len(vals[0]))]
    return idx, vals


def update_plot(lines, idx, vals):
    ax = plt.gca()
    [lines[i].set_xdata(idx) for i in range(len(lines))]
    [lines[i].set_ydata(vals[i]) for i in range(len(lines))]

    ax.relim()
    ax.autoscale_view(True, True, True)
    plt.draw()
    plt.pause(1e-17)
    return


running = True  # Global flag
zero_hight = 0


def scanning():
    global idx, vals, ser, lines
    if running:  # Only do this if the Stop button has not been clicked
        i = 0
        while i < 10:
            try:
                idx, vals = update_serial_val(vals, ser)
                update_plot(lines, idx, vals)
                i += 1
            except ValueError:
                pass
    # After 0.1 second, call scanning again (create a recursive loop)
    root.after(100, scanning)


def stop():
    """Stop scanning by setting the global flag to False."""
    global running
    running = False


def save():
    global idx, vals, activate
    import pandas as pd
    df = pd.DataFrame()
    df['index'] = idx
    for i in activate:
        df[f'value{i}'] = vals[i]
    df.to_csv('multi_analog.csv', index=False)
    plt.close()
    root.destroy()


def clear():
    global idx, vals
    idx = []
    vals = [[] for i in range(6)]


root = Tk()
root.title("Title")
root.geometry("200x100")

app = Frame(root)
app.grid()

# start = Button(app, text="Start Scan", command=start)
stop = Button(app, text="Stop", command=stop)
save = Button(app, text='Save', command=save)
clear = Button(app, text='clear', command=clear)

stop.grid()
save.grid()
clear.grid()

root.after(100, scanning)  # After 0.1 second, call scanning
root.mainloop()
