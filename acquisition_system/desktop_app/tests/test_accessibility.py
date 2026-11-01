import gc
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6 import QtCore, QtGui, QtWidgets as W
import shiboken6
from desktop_app.accessibility import (
    AcquisitionTableWidget, install_cocoa_table_accessibility,
    prune_destroyed_interfaces, _interfaces,
)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Cocoa compatibility is Darwin-only')
def test_table_group_preserves_real_controls_and_gc_lifetime():
    app=W.QApplication.instance() or W.QApplication([])
    install_cocoa_table_accessibility()
    table=AcquisitionTableWidget(2,2)
    table.setAccessibleName('通道配置表单')
    check=W.QCheckBox();check.setAccessibleName('A0 启用')
    edit=W.QLineEdit('100');edit.setAccessibleName('A0 量程')
    table.setCellWidget(0,0,check);table.setCellWidget(0,1,edit)
    interface=QtGui.QAccessible.queryAccessibleInterface(table)
    assert interface.role()==QtGui.QAccessible.Role.Grouping
    assert interface.interface_cast(QtGui.QAccessible.InterfaceType.TableInterface) is None
    assert interface.selectionInterface() is None
    assert interface.childCount()==2
    assert interface.child(0).object() is check
    assert interface.child(1).object() is edit
    assert interface.indexOfChild(interface.child(1))==1
    accessible_id=QtGui.QAccessible.uniqueId(interface)
    del interface
    gc.collect()
    interface=QtGui.QAccessible.accessibleInterface(accessible_id)
    assert shiboken6.isValid(interface) and interface.childCount()==2
    table.setAccessibleDescription('SIMULATED；A0_zeroed=1.25 mm')
    assert '1.25' in interface.text(QtGui.QAccessible.Text.Description)
    table.deleteLater()
    app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not shiboken6.isValid(interface)
    prune_destroyed_interfaces()
    assert all(shiboken6.isValid(item) for item in _interfaces.values())
    # Dropping only invalid wrappers does not delete the native interface twice.
    del interface
    gc.collect()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Cocoa compatibility is Darwin-only')
def test_factory_does_not_replace_other_tables_or_buttons():
    app=W.QApplication.instance() or W.QApplication([])
    install_cocoa_table_accessibility()
    table=W.QTableWidget(1,1)
    button=W.QPushButton('连接')
    assert QtGui.QAccessible.queryAccessibleInterface(table).role()==QtGui.QAccessible.Role.Table
    assert QtGui.QAccessible.queryAccessibleInterface(button).role()==QtGui.QAccessible.Role.Button
    table.deleteLater();button.deleteLater()
    app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
