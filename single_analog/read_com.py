import serial
import matplotlib.pyplot as plt
from tkinter import *


def update_serial_val(vals, ser):
    global zero_hight
    val = int(ser.readline())
    val -= zero_hight
    vals.append(val)
    idx = [i for i in range(len(vals))]
    return idx, vals


def update_plot(line, idx, val):
    ax = plt.gca()
    line.set_xdata(idx)
    line.set_ydata(val)
    ax.relim()
    ax.autoscale_view(True, True, True)
    plt.draw()
    plt.pause(1e-17)
    return


ser = serial.Serial(port='COM3', baudrate=9600)
idx = []
vals = []


plt.ion()
fig, ax = plt.subplots()
line, = ax.plot(idx, vals)


running = True  # Global flag
zero_hight = 0


def scanning():
    global idx, vals, ser, line
    if running:  # Only do this if the Stop button has not been clicked
        i = 0
        while i < 20:
            try:
                idx, vals = update_serial_val(vals, ser)
                update_plot(line, idx, vals)
                i += 1
            except ValueError:
                pass
    # After 1 second, call scanning again (create a recursive loop)
    root.after(100, scanning)
    # else:


# def resume():
#     """Enable scanning by setting the global flag to True."""
#     global running
#     running = True


def stop():
    """Stop scanning by setting the global flag to False."""
    global running
    running = False


def save():
    global idx, vals
    import pandas as pd
    df = pd.DataFrame()
    df['index'] = idx
    df['value'] = vals
    df.to_csv('single_analog.csv', index=False)
    plt.close()
    root.destroy()


def clear():
    global idx, vals
    [idx, vals] = [[], []]


def balance():
    global idx, vals, zero_hight
    zero_hight = int(ser.readline())
    [idx, vals] = [[], []]


root = Tk()
root.title("Title")
root.geometry("200x100")

app = Frame(root)
app.grid()

# start = Button(app, text="Start Scan", command=start)
stop = Button(app, text="Stop", command=stop)
save = Button(app, text='Save', command=save)
clear = Button(app, text='clear', command=clear)
balance = Button(app, text='balance', command=balance)

stop.grid()
save.grid()
clear.grid()
balance.grid()

root.after(100, scanning)  # After 0.1 second, call scanning
root.mainloop()
