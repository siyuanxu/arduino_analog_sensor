"""App-scoped Cocoa table compatibility using Qt's public accessibility factory.

Qt 6.12.0's Cocoa synthesized table elements can invalidate interfaces still
referenced by accessibilitySelectedChildren. Expose our two tables as groups of
real cell editor widgets instead; no Qt binaries or system AX settings change.
"""
import sys
from PySide6 import QtCore, QtGui, QtWidgets
import shiboken6

_factory_installed = False
# Qt owns destruction of registered interfaces even though PySide's ownership
# flag remains True. Keep wrappers alive until Qt has invalidated them.
_interfaces = {}


def prune_destroyed_interfaces():
    for key, interface in list(_interfaces.items()):
        if not shiboken6.isValid(interface):
            _interfaces.pop(key, None)


def _widget_destroyed(*_args):
    # QObject::destroyed may run before Qt's accessibility-cache cleanup slot.
    # Never release a still-valid wrapper in that signal's call stack.
    QtCore.QTimer.singleShot(0, prune_destroyed_interfaces)


class AcquisitionTableWidget(QtWidgets.QTableWidget):
    """Only this application's configuration and delivery tables use the factory."""


class TableFormAccessibility(QtWidgets.QAccessibleWidget):
    def __init__(self, widget):
        super().__init__(widget, QtGui.QAccessible.Role.Grouping)

    def _controls(self):
        widget = self.object()
        if widget is None or not shiboken6.isValid(widget):
            return []
        return [control for row in range(widget.rowCount())
                for col in range(widget.columnCount())
                if (control := widget.cellWidget(row, col)) is not None
                and shiboken6.isValid(control)]

    def childCount(self):
        return len(self._controls())

    def child(self, index):
        controls = self._controls()
        if 0 <= index < len(controls):
            return QtGui.QAccessible.queryAccessibleInterface(controls[index])
        return None

    def indexOfChild(self, interface):
        if interface is None:
            return -1
        obj = interface.object()
        for index, control in enumerate(self._controls()):
            if control is obj:
                return index
        return -1

    def childAt(self, x, y):
        for control in reversed(self._controls()):
            rect = QtCore.QRect(control.mapToGlobal(QtCore.QPoint(0, 0)), control.size())
            if control.isVisible() and rect.contains(x, y):
                return QtGui.QAccessible.queryAccessibleInterface(control)
        return None

    def focusChild(self):
        widget = self.object()
        focus = widget.focusWidget() if widget is not None else None
        for control in self._controls():
            if control is focus or (focus is not None and control.isAncestorOf(focus)):
                return QtGui.QAccessible.queryAccessibleInterface(control)
        return None


def _table_factory(class_name, obj):
    if class_name != 'AcquisitionTableWidget' or not isinstance(obj, AcquisitionTableWidget):
        return None
    interface = TableFormAccessibility(obj)
    _interfaces[id(interface)] = interface
    obj.destroyed.connect(_widget_destroyed)
    return interface


def install_cocoa_table_accessibility():
    """Keep one factory for process lifetime; PySide does not expose removeFactory."""
    global _factory_installed
    if sys.platform == 'darwin' and not _factory_installed:
        QtGui.QAccessible.installFactory(_table_factory)
        _factory_installed = True
