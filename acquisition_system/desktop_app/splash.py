"""Branded startup screen, painted at the display's native pixel density."""
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__


def artwork(screen):
    width, height = 576, 284
    available = screen.availableGeometry()
    scale = min(1.0, available.width() / width, available.height() / height)
    density = screen.devicePixelRatio()
    image = QtGui.QPixmap(round(width * scale * density), round(height * scale * density))
    image.setDevicePixelRatio(density)
    image.fill(QtGui.QColor('#102e3a'))
    painter = QtGui.QPainter(image)
    painter.scale(scale, scale)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

    def label(x, baseline, text, size, color, weight):
        font = QtGui.QFont()
        font.setFamilies(['PingFang SC', 'Microsoft YaHei UI', 'Noto Sans CJK SC', 'Arial'])
        font.setPixelSize(size)
        font.setWeight(weight)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(color))
        painter.drawText(x, baseline, text)

    try:
        teal = '#72d9c7'
        white = '#f0f6f5'
        weight = QtGui.QFont.Weight
        painter.fillRect(32, 30, 4, 20, QtGui.QColor(teal))
        label(47, 47, 'NHRI', 18, teal, weight.DemiBold)
        label(32, 149, '直剪试验数据采集软件', 32, white, weight.DemiBold)

        # Two halves of a direct shear box, moving in opposite directions.
        painter.setPen(QtGui.QPen(QtGui.QColor('#55828b'), 1))
        painter.drawRect(448, 94, 80, 26)
        painter.drawRect(438, 128, 80, 26)
        painter.setPen(QtGui.QPen(QtGui.QColor(teal), 2))
        for x, y, direction in ((468, 107, 1), (498, 141, -1)):
            tip = x + direction * 38
            painter.drawLine(x, y, tip, y)
            painter.drawLine(tip, y, tip - direction * 6, y - 5)
            painter.drawLine(tip, y, tip - direction * 6, y + 5)

        painter.fillRect(32, 215, 512, 1, QtGui.QColor('#41616a'))
        label(32, 251, 'Siyuan XU studio', 17, white, weight.Medium)
        label(484, 250, f'v{__version__}', 14, '#adc7ca', weight.Normal)
    finally:
        painter.end()
    return image


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setFont(QtGui.QFont('Arial', 10))
    screen = app.primaryScreen()
    splash = QtWidgets.QSplashScreen(screen, artwork(screen))
    splash.setAccessibleName('直剪试验数据采集软件 · NHRI · Siyuan XU studio')
    splash.show()
    app.processEvents()
    elapsed = QtCore.QElapsedTimer()
    elapsed.start()

    # Show branding while the plotting library and main window initialize.
    from .ui import MainWindow
    window = MainWindow()

    def show_workstation():
        window.show()
        splash.finish(window)

    QtCore.QTimer.singleShot(max(0, 1400 - elapsed.elapsed()), show_workstation)
    return app.exec()
