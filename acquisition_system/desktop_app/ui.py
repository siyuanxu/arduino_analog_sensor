from datetime import datetime
from pathlib import Path
import re
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
import time
import math
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as W
import pyqtgraph as pg
from .controller import AcquisitionEngine
from .models import ChannelConfig, KINDS, labels, load_config, save_config
from .transport import enumerate_ports
from .delivery import read_csv_preview
from .accessibility import AcquisitionTableWidget, install_cocoa_table_accessibility
from .logging_support import get_runtime_logging, get_logger
from .recording import recording_paths
from .readout import CompactReadout
from .plot_ranges import aligned_zero_ranges
from . import windows_drivers

logger = get_logger('ui')

UI_REFRESH_INTERVAL_MS = 250
WINDOWS_DEVICE_CHANGE_DEBOUNCE_MS = 600
IS_WINDOWS = os.name == 'nt'

COLORS = ['#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#db2777', '#64748b', '#4f46e5', '#65a30d', '#c026d3', '#0d9488', '#ca8a04', '#7c3aed', '#e11d48', '#0284c7']
BOARD_NAMES = {'MEGA_2560':'Arduino Mega 2560', 'UNO_R4_MINIMA':'Arduino UNO R4 Minima', 'UNO_R4_WIFI':'Arduino UNO R4 WiFi'}


def port_name(port):
    if port.get('vid') in (0x2341, 0x2A03) and port.get('pid') == 0x0042:
        return 'Arduino Mega 2560'
    if port.get('vid') in (0x2341, 0x2A03) or 'arduino' in str(port.get('manufacturer', '')).lower() or 'arduino' in str(port.get('description', '')).lower():
        return 'Arduino 开发板'
    return 'USB 串口设备' if port.get('vid') is not None else '系统串口'


class CommandBridge(QtCore.QObject):
    completed = QtCore.Signal(object, object)


class MainWindow(W.QMainWindow):
    def __init__(self, engine=None, logging_manager=None):
        super().__init__()
        install_cocoa_table_accessibility()
        self.runtime_logging = logging_manager or get_runtime_logging()
        self.engine = engine or AcquisitionEngine()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="GUI command dispatcher")
        self.delivery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="CSV bounded preview")
        self.delivery_path = None
        self.delivery_preview = None
        self.delivery_request = 0
        self.latest_seen_path = None
        self.target_ready = False
        self.target_request = 0
        self.record_target = None
        self.target_raw_path = None
        self.target_results_path = None
        self.was_recording = False
        self.pending_start = False
        self.last_recording_path = None
        self.active_record_path = None
        self.active_record_paths = set()
        self.record_outcomes = {}
        self.bridge = CommandBridge(self)
        self.bridge.completed.connect(self.command_complete)
        self.driver_bridge = CommandBridge(self)
        self.driver_bridge.completed.connect(self.driver_command_complete)
        self.shutdown_done = False
        self.closing = False
        self.pending_commands = 0
        self.pending_connection = False
        self.connection_port = None
        self.port_metadata = {}
        self.port_detection_summary = ''
        self.driver_check_pending = False
        self.driver_install_pending = False
        self.driver_rescan_requested = False
        self.driver_attempted_ids = set()
        self.driver_devices = {}
        self.setWindowTitle("多通道直剪试验采集工作站")
        self.resize(1280, 720)
        self.seen_revision = -1
        self.cap_identity = None
        self.last_plot = 0
        self.seen_plot_epoch = -1
        self.seen_plot_sample_number = -1
        self.seen_plot_history_length = 0
        self.plot_history = []
        self.plot_refresh_requested = True
        self.plot_buffer_source = None
        self.plot_buffer_length = 0
        self.plot_buffer_capacity = 0
        self.plot_x = np.empty(0, dtype=np.float64)
        self.plot_y = {}
        self.plot_minmax = {}
        self._config_input_revision = 0
        self._validated_config_signature = None
        self._valid_channels = False
        self._configuration_lock_signature = None
        self._connection_ui_signature = None
        self._logging_ui_signature = None
        self._live_ui_signature = None
        self._delivery_state_signature = None
        self._recorder_path_signature = None
        self._recording_ui_state = None
        self.cards, self.curves, self.curve_checks = [], [], []
        self.build()
        self.driver_timer = QtCore.QTimer(self)
        self.driver_timer.setSingleShot(True)
        self.driver_timer.setInterval(WINDOWS_DEVICE_CHANGE_DEBOUNCE_MS)
        self.driver_timer.timeout.connect(self.refresh_ports)
        self.refresh_ports()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.start(UI_REFRESH_INTERVAL_MS)

    def button(self, text, action):
        b = W.QPushButton(text)
        b.clicked.connect(action)
        return b

    def build(self):
        base = W.QWidget()
        self.setCentralWidget(base)
        root = W.QVBoxLayout(base)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)
        self.banner = W.QLabel("未连接 · 请在第一页选择设备")
        self.banner.setStyleSheet("padding:4px;background:#edf3f9;color:#16324d;border-radius:4px;font-weight:600")
        self.banner.setFixedHeight(28)
        self.banner.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        root.addWidget(self.banner)
        self.tabs = W.QTabWidget()
        root.addWidget(self.tabs, 1)

        configpage = W.QWidget()
        cfg = W.QVBoxLayout(configpage)
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setSpacing(4)
        cfg.addWidget(W.QLabel("① 设备连接"))
        row = W.QHBoxLayout()
        self.mode = W.QComboBox()
        self.mode.setAccessibleName('连接方式')
        self.mode.setToolTip('USB 开发板为实物连接；SIMULATED 仅为软件模拟。实际型号显示在右侧开发板选项及下方连接状态。')
        self.mode.addItems(["USB 开发板", "SIMULATED · UNO R4（6 路）", "SIMULATED · Mega 2560（16 路）"])
        self.port = W.QComboBox()
        self.port.setAccessibleName('开发板及端口')
        self.port.setEditable(True)
        self.port.setMinimumWidth(250)
        row.addWidget(self.mode)
        row.addWidget(self.port, 1)
        self.refresh_ports_btn = self.button("重新扫描", self.refresh_ports)
        row.addWidget(self.refresh_ports_btn)
        self.connect_btn = self.button("连接", self.connect_device)
        row.addWidget(self.connect_btn)
        self.disconnect_btn = self.button("断开", lambda: self.run_command(self.engine.disconnect))
        row.addWidget(self.disconnect_btn)
        cfg.addLayout(row)
        self.port_status = W.QLabel()
        self.port_status.setWordWrap(False)
        self.port_status.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.port_status.setStyleSheet('padding:2px;font-weight:600;color:#16324d')
        cfg.addWidget(self.port_status)
        driver_row = W.QHBoxLayout()
        self.driver_status = W.QLabel()
        self.driver_status.setWordWrap(False)
        self.driver_status.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.driver_status.setStyleSheet('padding:2px;color:#92400e')
        self.driver_status.hide()
        driver_row.addWidget(self.driver_status, 1)
        self.driver_device = W.QComboBox()
        self.driver_device.setAccessibleName('需要修复驱动的开发板')
        self.driver_device.setMinimumWidth(190)
        self.driver_device.hide()
        driver_row.addWidget(self.driver_device)
        self.driver_install_btn = self.button("安装驱动…", self.install_selected_windows_driver)
        self.driver_install_btn.setToolTip("为选中的 Arduino 安装随软件提供的官方驱动；Windows 会要求管理员授权。")
        self.driver_install_btn.hide()
        driver_row.addWidget(self.driver_install_btn)
        cfg.addLayout(driver_row)
        self.port.currentIndexChanged.connect(self.update_selected_device)
        self.port.editTextChanged.connect(self.update_selected_device)
        self.mode.currentIndexChanged.connect(self.update_selected_device)
        channel_row = W.QHBoxLayout()
        channel_title = W.QLabel("② 通道配置 ⓘ")
        channel_title.setToolTip("只勾选实际使用的通道；同类传感器自动编号。\n名义换算：下限 + raw/(2^bits−1) × (上限−下限)，未实测标定。\n两点标定允许外推；应用配置会重置清零值和曲线。")
        channel_row.addWidget(channel_title)
        self.enabled_label = W.QLabel("已启用 0 / 0 路")
        self.enabled_label.setToolTip("未启用通道仍保存原始ADC；实时窗口和测试结果只显示启用通道。")
        channel_row.addStretch()
        channel_row.addWidget(self.enabled_label)
        cfg.addLayout(channel_row)
        self.table = AcquisitionTableWidget(0, 12)
        self.table.setAccessibleName("通道配置表单")
        self.table.setAccessibleDescription("按通道顺序提供真实复选框和参数编辑控件；可用鼠标选择多行清零。")
        self.table.setHorizontalHeaderLabels(["通道", "启用", "传感器类型 / 编号", "单位", "量程下限", "量程上限", "小数位", "换算方式", "raw₀", "value₀", "raw₁", "value₁"])
        self.table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(150)
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.ResizeToContents)
        cfg.addWidget(self.table, 1)
        cfgrow = W.QHBoxLayout()
        self.apply_btn = self.button("应用配置", self.apply_config)
        self.load_btn = self.button("载入配置…", self.load_config)
        self.save_btn = self.button("保存配置…", self.save_config)
        self.load_btn.setToolTip("载入当前板型专用的通道、量程和标定配置。")
        self.save_btn.setToolTip("保存当前板型的通道配置，供下次载入。")
        cfgrow.addWidget(self.apply_btn)
        cfgrow.addWidget(self.load_btn)
        cfgrow.addWidget(self.save_btn)
        cfgrow.addStretch()
        self.enter_btn = self.button("进入数据采集 →", self.enter_acquisition)
        cfgrow.addWidget(self.enter_btn)
        cfg.addLayout(cfgrow)
        record_title = W.QLabel("③ 记录文件 ⓘ")
        record_title.setToolTip("选择一个文件基名，开始记录时生成“原始数据”和“测试结果”两个CSV。\n选择位置不会创建文件；任一目标已存在则禁止覆盖。")
        cfg.addWidget(record_title)
        experiment_row = W.QHBoxLayout()
        experiment_row.addWidget(W.QLabel("试验名称"))
        self.experiment_name = W.QLineEdit()
        self.experiment_name.setPlaceholderText("例如：试样01_法向荷载100kPa")
        experiment_row.addWidget(self.experiment_name, 1)
        cfg.addLayout(experiment_row)
        target_row = W.QHBoxLayout()
        self.target_path_edit = W.QLineEdit()
        self.target_path_edit.setPlaceholderText("开始记录前选择新的 CSV 文件基名与保存目录")
        target_row.addWidget(self.target_path_edit, 1)
        self.choose_target_btn = self.button("选择保存位置…", self.choose_record_target)
        target_row.addWidget(self.choose_target_btn)
        cfg.addLayout(target_row)
        self.target_status = W.QLabel("尚未选择保存位置")
        self.target_status.setWordWrap(False)
        self.target_status.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        cfg.addWidget(self.target_status)
        self.target_timer = QtCore.QTimer(self)
        self.target_timer.setSingleShot(True)
        self.target_timer.timeout.connect(self.validate_record_target)
        self.target_path_edit.textChanged.connect(self.target_edited)
        self.target_path_edit.editingFinished.connect(lambda: self.target_timer.start(0))
        log_row = W.QHBoxLayout()
        self.diagnostic_toggle = W.QToolButton()
        self.diagnostic_toggle.setText("日志 ▸")
        self.diagnostic_toggle.setToolTip("查看运行日志、端口诊断和最新全部原始值。")
        self.diagnostic_toggle.setCheckable(True)
        log_row.addWidget(self.diagnostic_toggle)
        log_row.addStretch()
        log_row.addWidget(W.QLabel("日志级别"))
        self.log_level = W.QComboBox()
        self.log_level.setAccessibleName("日志级别")
        self.log_level.addItems(['INFO', 'WARNING', 'ERROR'])
        self.log_level.setCurrentText(self.runtime_logging.level)
        self.log_level.setToolTip("INFO：普通事件、警告和错误；WARNING：警告和错误；ERROR：仅错误。影响文件与窗口，重启后保留。")
        self.log_level.currentTextChanged.connect(self.change_log_level)
        log_row.addWidget(self.log_level)
        log_row.addWidget(self.button("打开日志目录", self.open_log_directory))
        cfg.addLayout(log_row)
        self.log_path_label = W.QLabel()
        self.log_path_label.setWordWrap(False)
        self.log_path_label.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.log_path_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        cfg.addWidget(self.log_path_label)
        self.log_path_label.hide()
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.setMaximumBlockCount(1500)
        self.log.hide()
        self.diagnostic_toggle.toggled.connect(self.toggle_log_view)
        cfg.addWidget(self.log)
        self.raw_diagnostic = W.QLabel()
        self.raw_diagnostic.setWordWrap(True)
        self.raw_diagnostic.hide()
        cfg.addWidget(self.raw_diagnostic)
        self.config_scroll = W.QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        self.config_scroll.setWidget(configpage)
        self.tabs.addTab(self.config_scroll, "连接与配置")
        self.log_failure_banner = W.QLabel()
        self.log_failure_banner.setWordWrap(True)
        self.log_failure_banner.setStyleSheet('color:#b91c1c;font-weight:600')
        self.log_failure_banner.hide()
        root.addWidget(self.log_failure_banner)

        preview = W.QWidget()
        pre = W.QVBoxLayout(preview)
        pre.setContentsMargins(6, 6, 6, 6)
        pre.setSpacing(4)
        self.card_area = W.QScrollArea()
        self.card_area.setWidgetResizable(True)
        self.card_area.setToolTip("悬停读数窗查看传感器名称、原始ADC和标定信息。")
        self.card_area.setFrameShape(W.QFrame.Shape.NoFrame)
        self.card_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card_widget = W.QWidget()
        self.card_grid = W.QGridLayout(card_widget)
        self.card_grid.setContentsMargins(0, 0, 0, 0)
        self.card_grid.setSpacing(4)
        self.card_grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        self.card_area.setWidget(card_widget)
        pre.addWidget(self.card_area)
        plotrow = W.QHBoxLayout()
        range_label = W.QLabel("范围")
        range_label.setToolTip("只改变曲线显示，不删除历史，不影响 CSV。")
        plotrow.addWidget(range_label)
        self.range_mode = W.QComboBox()
        self.range_mode.setAccessibleName("曲线显示范围")
        self.range_mode.setToolTip("本段全程逐点显示；最近窗口只绘制末尾时段。\n切换不删除历史，不影响 CSV。")
        self.range_mode.addItems(["本段全程", "最近窗口"])
        plotrow.addWidget(self.range_mode)
        self.window_sec = W.QSpinBox()
        self.window_sec.setAccessibleName("最近窗口秒数")
        self.window_sec.setRange(5, 7200)
        self.window_sec.setValue(120)
        self.window_sec.setSuffix(" s")
        self.window_sec.setToolTip("最近窗口的时间长度（5～7200 秒）。")
        self.window_sec.setEnabled(False)
        plotrow.addWidget(self.window_sec)
        plot_hint = W.QLabel("自动缩放 · 零点对齐 ⓘ")
        plot_hint.setToolTip("左右轴保留各自单位与比例，0刻度始终对齐。\n清零或开始记录会开启新的绘图段。")
        plotrow.addStretch()
        plotrow.addWidget(plot_hint)
        pre.addLayout(plotrow)
        # Full-segment curves keep every point. Non-antialiased one-pixel lines
        # materially reduce paint cost on low-resolution Windows hardware.
        pg.setConfigOptions(antialias=False, background='w', foreground='#334155')
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(220)
        self.plot.setLabel('bottom', '时间', units='s')
        self.plot.setToolTip("横轴为本次绘图经过时间；成功清零或开始记录后重新从0秒绘图。")
        self.plot.setLabel('left', '拉力', units='N')
        self.plot.getAxis('left').enableAutoSIPrefix(False)
        self.plot.getAxis('bottom').enableAutoSIPrefix(False)
        self.plot.showGrid(x=True, y=True, alpha=.2)
        self.legend = self.plot.addLegend(offset=(10, 10), colCount=4)
        self.legend.hide()
        self.right = pg.ViewBox()
        self.plot.showAxis('right')
        self.plot.getAxis('right').setLabel('位移', units='mm')
        self.plot.getAxis('right').enableAutoSIPrefix(False)
        self.plot.scene().addItem(self.right)
        self.plot.getAxis('right').linkToView(self.right)
        self.right.setXLink(self.plot)
        for view in (self.plot.getViewBox(), self.right):
            view.setMouseEnabled(x=True, y=False)
            view.setMenuEnabled(False)
            view.disableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot.getPlotItem().setMenuEnabled(False)
        self.plot.getPlotItem().hideButtons()
        self.zero_line = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen('#94a3b8', width=1))
        self.plot.addItem(self.zero_line, ignoreBounds=True)
        self.plot.getViewBox().sigResized.connect(self.resize_plot)
        pre.addWidget(self.plot, 1)
        self.range_mode.currentIndexChanged.connect(self.change_plot_range)
        self.window_sec.valueChanged.connect(self.change_plot_range)
        self.curve_bar = W.QWidget()
        self.curve_layout = W.QGridLayout(self.curve_bar)
        self.curve_layout.setContentsMargins(0, 0, 0, 0)
        self.curve_layout.setHorizontalSpacing(8)
        self.curve_layout.setVerticalSpacing(0)
        self.curve_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        curve_row = W.QHBoxLayout()
        curve_label = W.QLabel("曲线 ⓘ")
        curve_label.setToolTip("勾选通道切换曲线显示，不影响采集和保存。")
        curve_row.addWidget(curve_label)
        curve_row.addWidget(self.curve_bar, 1)
        pre.addLayout(curve_row)
        recordrow = W.QHBoxLayout()
        self.start_btn = self.button("开始记录", self.start_recording)
        self.stop_btn = self.button("结束记录", lambda: self.run_command(self.engine.stop_recording))
        recordrow.addWidget(self.start_btn)
        recordrow.addWidget(self.stop_btn)
        self.record_label = W.QLabel("未记录")
        recordrow.addWidget(self.button("全部清零", lambda: self.run_command(self.engine.zero)))
        self.zero_channel = W.QComboBox()
        self.zero_channel.setMinimumWidth(130)
        self.zero_channel.setToolTip("选择一个通道；也可使用配置页选定的多行。")
        recordrow.addWidget(self.zero_channel)
        recordrow.addWidget(self.button("选中清零", self.zero_selected))
        recordrow.addStretch()
        recordrow.addWidget(self.record_label)
        recordrow.addWidget(self.button("成果 →", lambda: self.tabs.setCurrentIndex(2)))
        pre.addLayout(recordrow)
        self.status_label = W.QLabel()
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        pre.addWidget(self.status_label)
        self.record_path = W.QLabel("目标路径：尚未设置，请在第一页选择")
        self.record_path.setWordWrap(False)
        self.record_path.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.record_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        pre.addWidget(self.record_path)
        self.tabs.addTab(preview, "数据采集")

        delivery = W.QWidget()
        dl = W.QVBoxLayout(delivery)
        dl.setContentsMargins(6, 6, 6, 6)
        dl.setSpacing(4)
        choose = W.QHBoxLayout()
        choose.addWidget(W.QLabel("本次 / 最近文件"))
        self.recent_files = W.QComboBox()
        self.recent_files.setMinimumWidth(300)
        choose.addWidget(self.recent_files, 1)
        choose.addWidget(self.button("预览选中文件", self.preview_selected_file))
        choose.addWidget(self.button("选择已有 CSV…", self.choose_delivery_file))
        dl.addLayout(choose)
        self.delivery_state = W.QLabel("尚未选择成果文件")
        self.delivery_state.setWordWrap(False)
        self.delivery_state.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        dl.addWidget(self.delivery_state)
        self.delivery_path_label = W.QLabel("文件路径：—")
        self.delivery_path_label.setWordWrap(False)
        self.delivery_path_label.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
        self.delivery_path_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.delivery_path_label)
        actions = W.QHBoxLayout()
        actions.addWidget(self.button("刷新预览", self.reload_delivery))
        actions.addWidget(self.button("打开 CSV", lambda: self.open_delivery(False)))
        actions.addWidget(self.button("打开所在目录", lambda: self.open_delivery(True)))
        actions.addWidget(self.button("复制路径", self.copy_delivery_path))
        self.paired_file_button = self.button("切换原始 / 结果", self.preview_paired_file)
        self.paired_file_button.setEnabled(False)
        actions.addWidget(self.paired_file_button)
        actions.addStretch()
        actions.addWidget(self.button("导出当前曲线 PNG…", self.export_curve_png))
        dl.addLayout(actions)
        column_row = W.QHBoxLayout()
        column_row.addWidget(W.QLabel("预览列"))
        self.delivery_column_mode = W.QComboBox()
        self.delivery_column_mode.addItems(["已启用物理量", "原始 ADC", "完整 CSV"])
        self.delivery_column_mode.currentIndexChanged.connect(self.render_delivery_table)
        column_row.addWidget(self.delivery_column_mode)
        self.delivery_details_toggle = W.QToolButton()
        self.delivery_details_toggle.setText('参数 ▸')
        self.delivery_details_toggle.setCheckable(True)
        self.delivery_details_toggle.setToolTip('展开设备、标定和文件核对信息。')
        column_row.addWidget(self.delivery_details_toggle)
        column_row.addStretch()
        dl.addLayout(column_row)
        self.delivery_summary = W.QPlainTextEdit()
        self.delivery_summary.setReadOnly(True)
        self.delivery_summary.setFixedHeight(90)
        self.delivery_summary.setPlaceholderText("参数及传感器摘要")
        dl.addWidget(self.delivery_summary)
        self.delivery_summary.hide()
        self.delivery_details_toggle.toggled.connect(self.delivery_summary.setVisible)
        self.delivery_table = AcquisitionTableWidget()
        self.delivery_table.setAccessibleName("成果预览及数据文本摘要")
        self.delivery_table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.delivery_table.setAlternatingRowColors(True)
        self.delivery_table.setHorizontalScrollMode(W.QAbstractItemView.ScrollMode.ScrollPerPixel)
        dl.addWidget(self.delivery_table, 1)
        preview_hint = W.QLabel("前 40 行预览 ⓘ")
        preview_hint.setToolTip("预览只读，每文件最多512KiB；配对读取总计最多1MiB，不代表全文件校验。\n导出的PNG仅为当前有界曲线，完整数据以CSV为准。")
        dl.addWidget(preview_hint)
        self.tabs.addTab(delivery, "成果交付")
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.apply_aligned_y_ranges({'N': [], 'mm': []})

    def default_record_name(self):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', self.experiment_name.text()).strip(' .')[:60]
        if not name:
            name = 'acquisition'
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', name):
            name = 'experiment_' + name
        prefix = 'SIMULATED_' if self.engine.simulated else ''
        return prefix + name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.csv'

    def choose_record_target(self):
        old = self.target_path_edit.text().strip() or self.engine.last_path
        directory = Path(old).parent if old else Path.home()
        path,_ = W.QFileDialog.getSaveFileName(self,"选择文件基名（生成原始数据和测试结果两个 CSV）",str(directory/self.default_record_name()),"CSV (*.csv)",options=W.QFileDialog.Option.DontConfirmOverwrite)
        if path:
            if not path.lower().endswith('.csv'):
                path += '.csv'
            self.set_record_target(path)

    def set_record_target(self, path):
        self.target_path_edit.setText(str(Path(path).expanduser().resolve()))
        self.target_timer.start(0)

    def target_edited(self):
        self.target_ready = False
        self.record_target = None
        self.target_raw_path = self.target_results_path = None
        self.target_request += 1
        self.target_status.setText("正在检查保存位置…" if self.target_path_edit.text().strip() else "尚未选择保存位置")
        self.start_btn.setEnabled(False)
        self.target_timer.start(250)

    def validate_record_target(self):
        text = self.target_path_edit.text().strip()
        if not text or self.closing:
            return
        self.target_request += 1
        request = self.target_request
        simulated = self.engine.simulated
        result = {}
        def check():
            try:
                path = Path(text).expanduser().resolve()
                if path.suffix.lower() != '.csv':
                    raise ValueError("请使用 .csv 文件名")
                if path.exists():
                    raise ValueError("目标文件已存在，禁止覆盖；请选择新文件名")
                raw_path, results_path = recording_paths(path)
                for candidate in (raw_path, results_path):
                    if candidate.exists():
                        raise ValueError(f"配对文件已存在，禁止覆盖：{candidate.name}；请选择新文件名")
                if not path.parent.is_dir():
                    raise ValueError("保存目录不存在，请先选择已有目录")
                if not os.access(path.parent, os.W_OK):
                    raise ValueError("保存目录不可写")
                if simulated and 'SIMULATED' not in path.name:
                    raise ValueError("模拟记录文件名须包含 SIMULATED")
                result['path'] = str(path)
                result['raw_path'], result['results_path'] = str(raw_path), str(results_path)
            except (OSError,ValueError) as error:
                result['error'] = str(error)
        def complete():
            if request != self.target_request:
                return
            self.record_target = result.get('path')
            self.target_raw_path, self.target_results_path = result.get('raw_path'), result.get('results_path')
            self.target_ready = bool(self.record_target)
            detail = (f"开始记录时新建两个CSV：\n原始数据：{self.target_raw_path}\n测试结果：{self.target_results_path}") if self.target_ready else "路径不可用："+result['error']
            self.target_status.setText("已就绪：原始数据 + 测试结果 ⓘ" if self.target_ready else "路径不可用："+result['error'])
            self.target_status.setToolTip(detail)
            self.target_path_edit.setToolTip(detail)
        self.run_command(check,complete,executor=self.delivery_executor)

    def prepare_next_target(self, previous_path):
        self.set_record_target(Path(previous_path).parent/self.default_record_name())

    def enter_acquisition(self):
        def action():
            cfg = self.read_config()
            if not self.engine.connected or not self.engine.cap:
                raise ValueError("请先连接并识别设备")
            if not any(c.enabled for c in cfg):
                raise ValueError("请至少启用并配置一个实际使用的通道")
            def apply():
                if cfg != self.engine.configs:
                    self.engine.configure(cfg)
            self.run_command(apply, lambda: self.tabs.setCurrentIndex(1))
        self.try_action(action)

    def update_enabled_count(self):
        selected = sum(bool(self.table.cellWidget(i, 1) and self.table.cellWidget(i, 1).isChecked()) for i in range(self.table.rowCount()))
        self.enabled_label.setText(f"已选 {selected} / {self.table.rowCount()} 路")

    def mark_config_dirty(self, *_args):
        self._config_input_revision += 1
        self._validated_config_signature = None

    def on_tab_changed(self, index):
        if index == 1:
            # Plot updates are deferred while the page is hidden. Showing the
            # acquisition page must nevertheless reveal the latest full state.
            self.update_plot(self.plot_history)
            self.plot_refresh_requested = False
            self._live_ui_signature = None
            self.refresh()
        if index == 2 and self.recent_files.count() and self.delivery_path is None:
            self.preview_selected_file()

    def preview_selected_file(self):
        path = self.recent_files.currentData()
        if path:
            self.load_delivery(path)

    def choose_delivery_file(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "选择采集 CSV（只读预览）", "", "CSV (*.csv)")
        if path:
            self.add_recent_file(path)
            self.load_delivery(path)

    def add_recent_file(self, path):
        path = str(Path(path).resolve())
        idx = self.recent_files.findData(path)
        if idx < 0:
            self.recent_files.insertItem(0, Path(path).name, path)
            self.recent_files.setItemData(0, path, QtCore.Qt.ItemDataRole.ToolTipRole)
            while self.recent_files.count() > 20:
                self.recent_files.removeItem(self.recent_files.count()-1)
            idx = 0
        self.recent_files.setCurrentIndex(idx)

    def reload_delivery(self):
        if self.delivery_path:
            self.load_delivery(self.delivery_path)
        else:
            self.preview_selected_file()

    def preview_paired_file(self):
        if self.delivery_preview and self.delivery_preview.get('pair_path'):
            path = self.delivery_preview['pair_path']
            self.add_recent_file(path)
            self.load_delivery(path)

    def load_delivery(self, path):
        self.delivery_request += 1
        request = self.delivery_request
        self.delivery_path = str(Path(path).resolve())
        self.delivery_path_label.setText("文件：" + Path(self.delivery_path).name)
        self.delivery_path_label.setToolTip(self.delivery_path)
        self.delivery_preview = None
        self.paired_file_button.setEnabled(False)
        self.delivery_error = None
        self.delivery_state.setText("正在读取有界预览… 原文件保持不变。")
        self.delivery_summary.clear()
        self.delivery_table.clear()
        self.delivery_table.setRowCount(0)
        self.delivery_table.setColumnCount(0)
        result = {}
        source_path = self.delivery_path
        def read():
            result['preview'] = read_csv_preview(source_path)
        def success():
            if request == self.delivery_request:
                self.show_delivery(result['preview'])
        def failed(error):
            if request == self.delivery_request:
                self.delivery_error = str(error)
                self.delivery_state.setText("无法预览：" + str(error))
        self.run_command(read, success, failed, executor=self.delivery_executor)

    def show_delivery(self, preview):
        self.delivery_preview = preview
        meta = preview['metadata']
        cap = meta.get('capabilities')
        kind = preview.get('file_kind', 'legacy')
        self.delivery_column_mode.blockSignals(True)
        self.delivery_column_mode.clear()
        self.delivery_column_mode.addItems(['测试结果（已选通道）'] if kind == 'results' else ['原始数据（全部通道）'] if kind == 'raw' else ['已启用物理量', '原始 ADC', '完整 CSV'])
        self.delivery_column_mode.setEnabled(kind == 'legacy')
        self.delivery_column_mode.blockSignals(False)
        self.paired_file_button.setEnabled(bool(preview.get('pair_path')))
        if cap is None:
            self.delivery_summary.setPlainText("测试结果 CSV：参数、设备及来源未验证。\n"+preview.get('pair_verification', '同名原始数据文件未提供。')+"\n第一列为本次记录经过秒数，其后为已选通道的清零后测值；列名包含通道、传感器及单位。")
            self.render_delivery_table()
            self.update_delivery_state()
            return
        names = meta.get('sensor_labels', [])
        enabled = [(i,c) for i,c in enumerate(meta['configs']) if c.get('enabled')]
        file_type = {'results':'测试结果（已选通道、清零后测值）', 'raw':'原始数据（全部通道、参数与事件）', 'legacy':'旧版混合记录'}[kind]
        lines = [f"{file_type} | 来源：{meta['source']}  |  试验：{meta.get('experiment_name') or '未填写'}",
                 f"设备：{cap['board_model']} · {cap['adc_bits']} bit · 启用 {len(enabled)} / {cap['channel_count']} 路",
                 f"开始时间：{meta.get('created_at','未填写')}  |  软件：{meta.get('software_version','未知')}  |  固件：{cap['firmware_version']}"]
        if kind == 'results':
            lines.insert(1, '参数来自同名原始文件；'+preview.get('pair_verification', '仅核对当前预览范围，不代表全文件校验。'))
        for i,c in enabled:
            name = names[i] if i < len(names) else f"A{i} {c.get('kind','')}"
            mode = c.get('mode', '')
            calibration = "未实测标定" if mode == '名义换算' else f"({c.get('raw0')}, {c.get('value0')}) → ({c.get('raw1')}, {c.get('value1')})"
            lines.append(f"{name} · {c.get('unit','')} · 量程 {c.get('minimum')}～{c.get('maximum')} · {mode} · {calibration}")
        self.delivery_summary.setPlainText('\n'.join(lines))
        self.render_delivery_table()
        self.update_delivery_state()

    def render_delivery_table(self):
        preview = self.delivery_preview
        if not preview:
            return
        meta, header = preview['metadata'], preview['header']
        common = ['record_type', 'elapsed_s', 'device_time_s', 'pc_receive_time']
        mode = self.delivery_column_mode.currentIndex()
        if preview.get('file_kind', 'legacy') != 'legacy':
            columns = list(header)
        elif mode == 0:
            columns = common[:2] + [f"A{i}_{suffix}" for i,c in enumerate(meta['configs']) if c.get('enabled') for suffix in ('physical','offset','zeroed','status')]
        elif mode == 1:
            columns = common + ['adc_bits'] + [f"{c}_raw" for c in meta['capabilities']['channels']]
        else:
            columns = list(header)
        indices = [header.index(c) for c in columns]
        self.delivery_visible_columns = columns
        self.delivery_table.clear()
        self.delivery_table.setColumnCount(len(columns))
        self.delivery_table.setRowCount(len(preview['rows']))
        self.delivery_table.setHorizontalHeaderLabels(columns)
        names = meta.get('sensor_labels', [])
        meaning = {'raw':'原始 ADC', 'physical':'未清零物理量', 'offset':'清零偏置', 'zeroed':'清零后物理量', 'status':'量程状态'}
        for j,column in enumerate(columns):
            match = re.fullmatch(r'A(\d+)_(raw|physical|offset|zeroed|status)', column)
            if match:
                i, suffix = int(match[1]), match[2]
                config = meta['configs'][i]
                name = names[i] if i < len(names) else f"A{i} {config.get('kind','')}"
                unit = config.get('unit','') if suffix not in ('raw','status') else ''
                hint = f"{name} · {meaning[suffix]}" + (f" ({unit})" if unit else '')
                self.delivery_table.horizontalHeaderItem(j).setToolTip(hint)
                if mode == 0:
                    self.delivery_table.horizontalHeaderItem(j).setText(f"A{i} {meaning[suffix]}"+(f" ({unit})" if unit else ''))
            for row_index,row in enumerate(preview['rows']):
                self.delivery_table.setItem(row_index,j,W.QTableWidgetItem(row[indices[j]]))
        description = "来源：" + meta['source'] + "；预览数据（最多40行）：\n"
        for row in preview['rows']:
            description += "；".join(f"{column}={row[index]}" for column,index in zip(columns,indices)) + "\n"
            if len(description) > 12000:
                description = description[:12000] + "\n摘要已截断；完整数据请打开原CSV。"
                break
        self.delivery_table.setAccessibleDescription(description)
        self.delivery_table.resizeColumnsToContents()
        for j in range(self.delivery_table.columnCount()):
            self.delivery_table.setColumnWidth(j,min(240,max(90,self.delivery_table.columnWidth(j))))

    def update_delivery_state(self):
        if not self.delivery_preview:
            self._delivery_state_signature = None
            return
        source = self.delivery_preview['metadata']['source']
        source = '来源未验证' if source == 'UNKNOWN' else source
        ongoing = self.delivery_path in getattr(self,'active_record_paths',set())
        if ongoing:
            state = "正在记录 · 尚未结束；此处查看已 flush 的内容"
        else:
            known = getattr(self,'record_outcomes',{}).get(self.delivery_path)
            if known:
                state = known + " · 当前未在记录"
            elif self.delivery_preview.get('file_kind') != 'results' and not self.delivery_preview['limited'] and self.delivery_preview['rows'][-1][0] == 'stop':
                state = "已结束（文件末行 stop 事件）"
            else:
                state = "已保存文件 · 结束状态未验证（仅预览前部内容）"
        detail = f"{source} | {state} | 预览 {len(self.delivery_preview['rows'])} 行（不代表全文件校验）"
        short_state = '正在记录 · 尚未结束' if ongoing else '已结束' if known and known.startswith('已结束') else state.split('（')[0].split('；')[0]
        signature = (source, short_state, detail, ongoing, len(self.delivery_preview['rows']))
        if signature == self._delivery_state_signature:
            return
        self._delivery_state_signature = signature
        self.delivery_state.setText(f"{source} · {short_state} · {len(self.delivery_preview['rows'])} 行预览")
        self.delivery_state.setToolTip(detail)
        self.delivery_state.setStyleSheet("color:#b45309;font-weight:bold" if ongoing else "color:#16324d")

    def open_delivery(self, directory):
        def action():
            if not self.delivery_path:
                raise ValueError("请先选择成果文件")
            target = Path(self.delivery_path).parent if directory else Path(self.delivery_path)
            if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target))):
                raise ValueError("系统未能打开此文件或目录")
        self.try_action(action)

    def copy_delivery_path(self):
        if self.delivery_path:
            W.QApplication.clipboard().setText(self.delivery_path)

    def export_curve_png(self):
        def action():
            if not self.cards or self.engine.latest is None:
                raise ValueError("尚无已配置通道的有效曲线")
            prefix = 'SIMULATED_' if self.engine.simulated else ''
            path,_ = W.QFileDialog.getSaveFileName(self,"导出当前曲线（不会改写 CSV）",prefix+'current_curve_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.png',"PNG (*.png)",options=W.QFileDialog.Option.DontConfirmOverwrite)
            if path:
                if not path.lower().endswith('.png'):
                    path += '.png'
                self.save_curve_png(path)
        self.try_action(action)

    def save_curve_png(self, path):
        """Create a new labeled image; never overwrite any source or existing file."""
        if Path(path).exists():
            raise FileExistsError("PNG 路径已存在，禁止覆盖")
        plot = self.plot.grab().toImage()
        legend_rows = (len(self.curve_channels)+3)//4
        width = max(1100,plot.width())
        top = 100
        height = top+plot.height()+40+legend_rows*30
        img = QtGui.QImage(width,height,QtGui.QImage.Format.Format_ARGB32)
        img.fill(QtGui.QColor('white'))
        painter = QtGui.QPainter(img)
        try:
            painter.setPen(QtGui.QColor('#16324d'))
            painter.setFont(QtGui.QFont('Arial',14))
            painter.drawText(18,28,"直剪试验 · 当前曲线")
            painter.setFont(QtGui.QFont('Arial',10))
            painter.drawText(18,52,self.banner.text())
            painter.drawText(18,76,"试验："+(self.experiment_name.text() or '未填写')+"；仅为当前有界图形，完整数据以 CSV 为准。")
            painter.drawImage(0,top,plot)
            names = labels(self.engine.configs)
            for k,i in enumerate(self.curve_channels):
                x,y=18+(k%4)*(width//4),top+plot.height()+28+(k//4)*30
                painter.setPen(QtGui.QPen(QtGui.QColor(COLORS[i]),3))
                painter.drawLine(x,y-4,x+22,y-4)
                painter.setPen(QtGui.QColor('#334155'))
                suffix = '' if self.curve_checks[k].isChecked() else '（隐藏）'
                painter.drawText(x+30,y,names[i]+suffix)
        finally:
            painter.end()
        # QImage writes only to a newly-created file device, preserving exclusion.
        file = QtCore.QFile(str(path))
        if not file.open(QtCore.QIODevice.OpenModeFlag.WriteOnly | QtCore.QIODevice.OpenModeFlag.NewOnly):
            raise OSError(file.errorString())
        try:
            if not img.save(file,'PNG'):
                raise OSError("PNG 编码/写入失败")
        finally:
            file.close()

    def run_command(self, fn, on_success=None, on_error=None, executor=None):
        self.pending_commands += 1
        future = (executor or self.executor).submit(fn)
        future.add_done_callback(lambda f: self.bridge.completed.emit(f, (on_success, on_error)))

    @QtCore.Slot(object, object)
    def command_complete(self, future, callbacks):
        on_success, on_error = callbacks
        self.pending_commands -= 1
        try:
            future.result()
            if on_success:
                on_success()
        except Exception as e:
            logger.log(30 if isinstance(e, ValueError) else 40, "后台操作未完成", exc_info=True)
            if on_error:
                on_error(e)
            W.QMessageBox.warning(self, "操作未完成", str(e))
        if not self.shutdown_done:
            self.refresh()

    def try_action(self, fn):
        try:
            return fn()
        except Exception as e:
            logger.log(30 if isinstance(e, ValueError) else 40, "界面操作未完成", exc_info=True)
            W.QMessageBox.warning(self, "操作未完成", str(e))
            return None

    def run_driver_command(self, fn, on_success, on_error=None):
        """Run Windows device inspection/repair without blocking Qt or serial I/O."""
        future = Future()

        def worker():
            try:
                future.set_result(fn())
            except BaseException as error:
                future.set_exception(error)
            try:
                self.driver_bridge.completed.emit(future, (on_success, on_error))
            except RuntimeError:
                # The window may have been destroyed while Windows was showing UAC.
                pass

        # Driver checks and UAC can wait on Windows. A daemon worker lets the
        # acquisition app close immediately even when the system dialog remains.
        threading.Thread(target=worker, daemon=True,
                         name="Windows driver operation").start()

    @QtCore.Slot(object, object)
    def driver_command_complete(self, future, callbacks):
        if self.closing or self.shutdown_done:
            return
        on_success, on_error = callbacks
        try:
            result = future.result()
            on_success(result)
        except Exception as error:
            logger.exception("Windows 驱动操作未完成")
            if on_error:
                on_error(error)

    def set_driver_message(self, text='', detail='', devices=(), button=False):
        self.driver_status.setText(text)
        self.driver_status.setToolTip(detail or text)
        self.driver_status.setVisible(bool(text))
        self.driver_devices = {device.instance_id: device for device in devices}
        self.driver_device.blockSignals(True)
        self.driver_device.clear()
        for device in devices:
            self.driver_device.addItem(device.label, device.instance_id)
        self.driver_device.blockSignals(False)
        choose = len(devices) > 1
        self.driver_device.setVisible(choose)
        self.driver_install_btn.setVisible(bool(button and devices))
        self.driver_install_btn.setEnabled(bool(button and devices and
                                                not self.driver_install_pending))

    def check_windows_drivers(self, force=False):
        if (not IS_WINDOWS or self.driver_install_pending or self.closing or
                self.engine.connected or self.pending_connection or self.engine.recorder or
                self.mode.currentIndex() != 0):
            return
        if self.driver_check_pending:
            self.driver_rescan_requested = self.driver_rescan_requested or force
            return
        self.driver_check_pending = True

        def finished(scan):
            self.driver_check_pending = False
            repeat = self.driver_rescan_requested
            self.driver_rescan_requested = False
            if (self.closing or self.engine.connected or self.pending_connection or
                    self.engine.recorder or self.mode.currentIndex() != 0):
                self.set_driver_message()
                return
            self.apply_windows_driver_scan(scan, force=force)
            if repeat and not self.driver_install_pending:
                QtCore.QTimer.singleShot(0, lambda: self.check_windows_drivers(force=True))

        def failed(error):
            self.driver_check_pending = False
            repeat = self.driver_rescan_requested
            self.driver_rescan_requested = False
            logger.warning("Windows 驱动检查失败：%s", error)
            if (not self.closing and not self.engine.connected and
                    not self.pending_connection and not self.engine.recorder and
                    self.mode.currentIndex() == 0 and (force or not self.port_metadata)):
                self.set_driver_message("无法检查 Arduino 驱动 · 可重试",
                                        str(error), button=False)
            if repeat and not self.driver_install_pending:
                QtCore.QTimer.singleShot(0, lambda: self.check_windows_drivers(force=True))

        self.run_driver_command(windows_drivers.scan_windows_drivers, finished, failed)

    def windows_device_changed(self):
        """Coalesce USB arrival/removal events before scanning Windows PnP once."""
        if (IS_WINDOWS and not self.closing and not self.engine.connected and
                not self.pending_connection and not self.engine.recorder and
                not self.driver_install_pending and self.mode.currentIndex() == 0):
            self.driver_timer.start()

    def apply_windows_driver_scan(self, scan, force=False):
        if not scan.available:
            if force or (scan.error and not self.port_metadata):
                self.set_driver_message("无法检查 Arduino 驱动 · 可重新扫描",
                                        scan.error or "Windows 设备检查不可用")
            return
        repairable = [device for device in scan.devices if device.repairable]
        bootloaders = [device for device in scan.devices
                       if device.problem_code is not None and device.role == 'bootloader']
        faults = [device for device in scan.devices
                  if device.role == 'runtime' and device.problem_code not in (0, 28)]
        if repairable:
            detail = "\n".join(f"{device.label}：设备错误代码 {device.problem_code}"
                               for device in repairable)
            pending = [device for device in repairable
                       if device.instance_id not in self.driver_attempted_ids]
            if len(repairable) == 1 and pending:
                self.set_driver_message("检测到 Arduino 缺少驱动 · 等待 Windows 授权…",
                                        detail, repairable)
                self.start_windows_driver_install(repairable[0], automatic=True)
                return
            text = ("发现多个缺驱动的 Arduino · 请选择设备"
                    if len(repairable) > 1 else
                    "Arduino 驱动未安装 · 点击重试")
            self.set_driver_message(text, detail, repairable, button=True)
            return
        if bootloaders:
            detail = "\n".join(device.label for device in bootloaders)
            self.set_driver_message("R4 处于固件升级模式 · 请复位后重新扫描", detail)
            return
        if faults:
            detail = "\n".join(f"{device.label}：设备错误代码 {device.problem_code}"
                               for device in faults)
            self.set_driver_message("Arduino 设备异常 · 无需重复安装驱动", detail)
            return
        self.set_driver_message()

    def install_selected_windows_driver(self):
        instance_id = self.driver_device.currentData()
        if instance_id is None and len(self.driver_devices) == 1:
            instance_id = next(iter(self.driver_devices))
        device = self.driver_devices.get(instance_id)
        if device is not None:
            self.start_windows_driver_install(device, automatic=False)

    def start_windows_driver_install(self, device, automatic=False):
        if (self.driver_install_pending or not device.repairable or self.closing or
                self.engine.connected or self.pending_connection or self.engine.recorder or
                self.mode.currentIndex() != 0):
            return
        self.driver_attempted_ids.add(device.instance_id)
        self.driver_install_pending = True
        self.set_driver_message("正在请求 Windows 安装 Arduino 驱动…",
                                f"{device.label}\n请在系统窗口中确认管理员授权。",
                                [device])

        def finished(result):
            self.driver_install_pending = False
            current = result.device
            if result.success:
                self.set_driver_message("驱动已安装 · 正在重新扫描端口", result.message)
                QtCore.QTimer.singleShot(300, self.refresh_ports)
            elif getattr(result, 'restart_required', False):
                text = ("驱动已加入 Windows · 请重启后重新扫描"
                        if getattr(result, 'staged', False) else
                        "Windows 要求重启 · 重启后重新扫描")
                self.set_driver_message(text, result.message)
            elif getattr(result, 'replug_required', False):
                text = ("驱动已加入 Windows · 请重新插拔 USB"
                        if getattr(result, 'staged', False) else
                        "请重新插拔 Arduino USB 后再扫描")
                self.set_driver_message(text, result.message)
            elif not current.repairable:
                self.set_driver_message("Arduino 设备异常 · 无需重复安装驱动",
                                        result.message)
            elif getattr(result, 'staged', False):
                self.set_driver_message("驱动已加入 Windows · 请重新扫描",
                                        result.message)
            elif result.cancelled:
                self.set_driver_message("已取消驱动安装 · 可点击重试", result.message,
                                        [current], button=True)
            else:
                self.set_driver_message("驱动安装未完成 · 可点击重试", result.message,
                                        [current], button=True)

        def failed(error):
            self.driver_install_pending = False
            self.set_driver_message("驱动安装未完成 · 可点击重试", str(error),
                                    [device], button=True)

        logger.info("请求 Windows Arduino 驱动修复：board=%s automatic=%s",
                    device.board_model, automatic)
        self.run_driver_command(lambda: windows_drivers.repair_windows_driver(device),
                                finished, failed)

    def refresh_ports(self, *_args, check_driver=True):
        previous = self.connection_port if self.engine.connected else self.port.currentData() or self.port.currentText()
        self.port.blockSignals(True)
        self.port.clear()
        try:
            ports = sorted(enumerate_ports(), key=lambda p: (0 if port_name(p).startswith('Arduino') else 1 if p.get('vid') is not None else 2, p['device']))
            self.port_metadata = {p['device']:p for p in ports}
            for p in ports:
                self.port.addItem(f"{port_name(p)} · {p['device']}", p['device'])
                self.port.setItemData(self.port.count()-1, "USB 描述仅供选择，实际板型以连接后握手为准。\n"+str(p), QtCore.Qt.ItemDataRole.ToolTipRole)
            idx = self.port.findData(previous)
            if idx >= 0:
                self.port.setCurrentIndex(idx)
            elif previous and not ports:
                self.port.setEditText(str(previous))
            self.port_detection_summary = "未发现开发板端口，请检查 USB 数据线与供电；也可手动输入端口。" if not ports else "已发现串口，请选择开发板后点击连接。"
            logger.info("端口枚举：%s", [(p['device'], p['description']) for p in ports])
        except Exception as e:
            logger.exception("端口枚举失败")
            self.port_detection_summary = f"端口扫描失败：{e}"
        finally:
            self.port.blockSignals(False)
        self.update_selected_device()
        if IS_WINDOWS and check_driver:
            self.check_windows_drivers(force=True)

    def update_selected_device(self, *_args):
        if self.engine.connected or self.pending_connection:
            return
        real = self.mode.currentIndex() == 0
        self.port.setEnabled(real)
        self.refresh_ports_btn.setEnabled(real)
        if not real:
            self.driver_timer.stop()
            self.set_driver_message()
            self.port_status.setText("已选择模拟设备（SIMULATED），不会连接实际开发板。")
            return
        selected = self.port.currentData() if self.port.currentText() == self.port.itemText(self.port.currentIndex()) else self.port.currentText().strip()
        if selected:
            name = port_name(self.port_metadata[selected]) if selected in self.port_metadata else '手动输入端口'
            self.port_status.setText(f"已选择：{name} · 尚未连接")
            self.port_status.setToolTip(f"端口：{selected}\n点击连接后通过握手确认实际板型。")
        else:
            self.port_status.setText(self.port_detection_summary)

    def refresh_connection_state(self, engine):
        busy = engine.connected or self.pending_connection
        actual_port = getattr(engine.transport, 'port', None) or self.connection_port
        signature = (
            busy, engine.connected, self.pending_connection, engine.simulated,
            id(engine.cap), engine.latest is not None, engine.stale,
            actual_port, self.mode.currentIndex(),
        )
        if signature == self._connection_ui_signature:
            return
        self._connection_ui_signature = signature
        self.mode.setEnabled(not busy)
        self.port.setEnabled(not busy and self.mode.currentIndex() == 0)
        self.refresh_ports_btn.setEnabled(not busy and self.mode.currentIndex() == 0)
        self.connect_btn.setEnabled(not busy)
        self.disconnect_btn.setEnabled(engine.connected)
        if busy and self.driver_status.isVisible():
            self.set_driver_message()
        ready = engine.connected and engine.cap is not None and engine.latest is not None
        self.connect_btn.setText('已连接' if ready else '连接中…' if busy else '连接')
        if engine.connected:
            if actual_port and not engine.simulated:
                self.connection_port = actual_port
                self.port.blockSignals(True)
                index = self.port.findData(actual_port)
                if index >= 0:
                    self.port.setCurrentIndex(index)
                else:
                    self.port.setEditText(actual_port)
                self.port.blockSignals(False)
            if ready:
                model = BOARD_NAMES.get(engine.cap.board_model, engine.cap.board_model)
                source = 'SIMULATED · ' if engine.simulated else ''
                endpoint = '模拟连接' if engine.simulated else f'端口：{actual_port or "手动端口"}'
                freshness = '接收超时，当前为旧值' if engine.stale else '数据接收正常'
                self.port_status.setText(f"已连接：{source}{model} | {engine.cap.channel_count} 路 · ADC {engine.cap.adc_bits} 位 | {freshness}")
                self.port_status.setToolTip(f"已连接：{source}{model}\n{endpoint}\n{engine.cap.channel_count}路模拟输入，ADC{engine.cap.adc_bits}位；{freshness}。")
            else:
                self.port_status.setText(f"正在连接{'模拟设备' if engine.simulated else actual_port or '开发板'}，等待板型确认和首个有效数据…")
        elif not self.pending_connection:
            self.update_selected_device()

    def toggle_log_view(self, checked):
        self.diagnostic_toggle.setText("日志 ▾" if checked else "日志 ▸")
        self.log.setVisible(checked)
        self.raw_diagnostic.setVisible(checked)
        self.log_path_label.setVisible(checked)
        self.refresh_logging()

    def change_log_level(self, level):
        self.runtime_logging.set_level(level)
        self.refresh_logging()

    def open_log_directory(self):
        path = Path(self.runtime_logging.path).parent
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path))):
            logger.error("无法打开日志目录：%s", path)
            W.QMessageBox.warning(self, "无法打开日志目录", str(path))

    def refresh_logging(self):
        manager = self.runtime_logging
        error = manager.error
        state = "日志写入异常" if error else "运行日志" if getattr(manager, 'started', False) else "运行日志尚未启动"
        signature = (state, str(manager.path), error)
        if signature != self._logging_ui_signature:
            self._logging_ui_signature = signature
            self.log_path_label.setText(f"{state}：{Path(manager.path).name} ⓘ")
            self.log_path_label.setToolTip(f"{manager.path}\n5 MiB轮转，保留5份备份。")
            self.log_failure_banner.setText(f"运行日志无法完整保存：{error}。请检查日志目录权限或磁盘空间。" if error else "")
            self.log_failure_banner.setVisible(bool(error))
        if self.diagnostic_toggle.isChecked():
            logtext = '\n'.join(manager.recent_lines(limit=200))
            if self.log.toPlainText() != logtext:
                self.log.setPlainText(logtext)
                self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def connect_device(self):
        def action():
            if self.engine.connected or self.pending_connection:
                return
            idx = self.mode.currentIndex()
            simulated = [None, "UNO_R4_MINIMA", "MEGA_2560"][idx]
            port = self.port.currentData() if self.port.currentText() == self.port.itemText(self.port.currentIndex()) else self.port.currentText().strip()
            if not simulated and not port:
                raise ValueError("请选择或输入端口，例如 COM3 或 /dev/cu.usbmodem…")
            self.cap_identity = None
            self.connection_port = port if not simulated else None
            self.pending_connection = True
            self.connect_btn.setEnabled(False)
            def complete():
                self.pending_connection = False
                self.target_timer.start(0)
            self.run_command(lambda: self.engine.connect(port=port, simulated=simulated), complete, lambda error: complete())
        self.try_action(action)

    def populate_config(self):
        cap, configs = self.engine.cap, self.engine.configs
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                old = self.table.cellWidget(row, col)
                if old:
                    old.hide()
                    self.table.removeCellWidget(row, col)
                    old.deleteLater()
        self.table.setRowCount(len(configs))
        for i, c in enumerate(configs):
            for j, text in ((0, f"A{i}"), (3, c.unit)):
                item = W.QTableWidgetItem(text)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, item)
            check = W.QCheckBox()
            check.setChecked(c.enabled)
            check.toggled.connect(self.update_enabled_count)
            check.toggled.connect(self.mark_config_dirty)
            self.table.setCellWidget(i, 1, check)
            kind = W.QComboBox()
            kind.addItems(KINDS)
            kind.setCurrentText(c.kind)
            kind.currentTextChanged.connect(lambda text, row=i: self.table.item(row, 3).setText("N" if text == "拉力" else "mm" if text != "未配置" else ""))
            kind.currentTextChanged.connect(self.mark_config_dirty)
            self.table.setCellWidget(i, 2, kind)
            for j, attr in ((4, "minimum"), (5, "maximum"), (8, "raw0"), (9, "value0"), (10, "raw1"), (11, "value1")):
                edit = W.QLineEdit(format(getattr(c, attr), '.12g'))
                edit.setMinimumWidth(80)
                edit.textChanged.connect(self.mark_config_dirty)
                self.table.setCellWidget(i, j, edit)
            decimals = W.QSpinBox()
            decimals.setRange(0, 6)
            decimals.setValue(c.decimals)
            decimals.valueChanged.connect(self.mark_config_dirty)
            self.table.setCellWidget(i, 6, decimals)
            mode = W.QComboBox()
            mode.addItems(["名义换算", "两点标定"])
            mode.setCurrentText(c.mode)
            mode.currentTextChanged.connect(self.mark_config_dirty)
            self.table.setCellWidget(i, 7, mode)
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                control = self.table.cellWidget(row, col)
                if control is not None:
                    control.setAccessibleName(f"A{row} {self.table.horizontalHeaderItem(col).text()}")
        self.table.resizeColumnsToContents()
        self.update_enabled_count()
        self.mark_config_dirty()
        self._configuration_lock_signature = None

    def read_config(self):
        result = []
        for i in range(self.table.rowCount()):
            result.append(ChannelConfig(enabled=self.table.cellWidget(i, 1).isChecked(), kind=self.table.cellWidget(i, 2).currentText(), minimum=float(self.table.cellWidget(i, 4).text()), maximum=float(self.table.cellWidget(i, 5).text()), decimals=self.table.cellWidget(i, 6).value(), mode=self.table.cellWidget(i, 7).currentText(), raw0=float(self.table.cellWidget(i, 8).text()), value0=float(self.table.cellWidget(i, 9).text()), raw1=float(self.table.cellWidget(i, 10).text()), value1=float(self.table.cellWidget(i, 11).text())))
        return result

    def apply_config(self):
        def action():
            cfg = self.read_config()
            self.run_command(lambda: self.engine.configure(cfg))
        self.try_action(action)

    def save_config(self):
        def action():
            if not self.engine.cap:
                raise ValueError("请先连接设备")
            cfg = self.read_config()
            for c in cfg:
                c.validate(self.engine.cap.adc_bits)
            path, _ = W.QFileDialog.getSaveFileName(self, "保存板型专用配置", f"{self.engine.cap.board_model}_config.json", "JSON (*.json)")
            if path:
                save_config(path, self.engine.cap, cfg)
                logger.info("已保存板型配置：board=%s path=%s", self.engine.cap.board_model, path)
        self.try_action(action)

    def load_config(self):
        def action():
            if not self.engine.cap:
                raise ValueError("请先连接设备")
            path, _ = W.QFileDialog.getOpenFileName(self, "载入板型专用配置", "", "JSON (*.json)")
            if path:
                cfg = load_config(path, self.engine.cap)
                logger.info("已载入板型配置：board=%s path=%s", self.engine.cap.board_model, path)
                self.run_command(lambda: self.engine.configure(cfg), self.populate_config)
        self.try_action(action)

    def start_recording(self):
        def action():
            if self.engine.recorder:
                return
            if not self.target_ready or not self.record_target:
                raise ValueError("请先在「连接与配置」设置有效的新 CSV 保存路径")
            cfg = self.read_config()
            name = self.experiment_name.text().strip()
            path = self.record_target
            def command():
                if cfg != self.engine.configs:
                    self.engine.configure(cfg)
                self.engine.start_recording(path, name)
            self.pending_start = True
            self.set_configuration_locked(True)
            self.start_btn.setEnabled(False)
            def finished():
                self.pending_start = False
            self.run_command(command, finished, lambda error: finished())
        self.try_action(action)

    def zero_selected(self):
        selected = self.zero_channel.currentData()
        rows = [selected] if selected is not None else sorted({x.row() for x in self.table.selectionModel().selectedRows()})
        if not rows:
            W.QMessageBox.information(self, "选择通道", "请在清零下拉框选择一个通道，或在「连接与配置」表中选择多行后清零。")
            return
        self.run_command(lambda: self.engine.zero(rows))

    def rebuild_display(self):
        self._readout_layout_signature = None
        for layout in (self.card_grid, self.curve_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        for curve, axis in self.curves:
            axis.removeItem(curve)
        self.legend.clear()
        self.cards, self.curves, self.curve_checks = [], [], []
        self.curve_channels = []
        self.reset_plot_buffer()
        self.zero_channel.clear()
        self.zero_channel.addItem("配置页所选通道", None)
        names = labels(self.engine.configs)
        for i, (c, name) in enumerate(zip(self.engine.configs, names)):
            if not c.enabled:
                continue
            self.zero_channel.addItem(name, i)
            display_index = len(self.curves)
            self.curve_channels.append(i)
            card = CompactReadout(f'A{i}', COLORS[i])
            self.card_grid.addWidget(card, len(self.cards)//8, len(self.cards)%8)
            self.cards.append((i, card))
            curve = pg.PlotCurveItem(pen=pg.mkPen(COLORS[i], width=1), connect='finite', antialias=False)
            axis = self.plot.getPlotItem() if c.unit == 'N' else self.right
            axis.addItem(curve)
            self.legend.addItem(curve, name)
            check = W.QCheckBox(f'A{i}')
            check.setToolTip(f'{name}\n切换曲线显示，不影响采集和保存。')
            check.setAccessibleName(f'{name} 曲线显示')
            check.setChecked(c.enabled)
            check.setStyleSheet(f"color:{COLORS[i]}")
            check.toggled.connect(curve.setVisible)
            check.toggled.connect(self.request_plot_refresh)
            curve.setVisible(c.enabled)
            self.curve_layout.addWidget(check, 0, display_index)
            self.curves.append((curve, axis))
            self.curve_checks.append(check)
        if not self.cards:
            label = W.QLabel("尚未启用通道")
            label.setToolTip("在连接与配置页勾选实际使用的传感器并应用。")
            self.card_grid.addWidget(label, 0, 0)
        self.legend.hide()
        self.resize_plot()
        self.relayout_readouts()

    def request_plot_refresh(self, *_args):
        self.plot_refresh_requested = True

    def change_plot_range(self, *_args):
        """Redraw the current history immediately without mutating it."""
        recent = self.range_mode.currentIndex() == 1
        self.window_sec.setEnabled(recent)
        self.update_plot(self.plot_history)

    def relayout_readouts(self):
        if not hasattr(self, 'card_grid'):
            return
        if not self.cards:
            self.card_area.setFixedHeight(26)
            return
        width = max(card.width() for _, card in self.cards)
        columns = max(1, min(len(self.cards), (max(120, self.width()-60)+4)//(width+4)))
        signature = (columns, tuple(card.width() for _, card in self.cards))
        if signature == getattr(self, '_readout_layout_signature', None):
            return
        self._readout_layout_signature = signature
        for _, card in self.cards:
            self.card_grid.removeWidget(card)
        for position, (_, card) in enumerate(self.cards):
            self.card_grid.addWidget(card, position//columns, position%columns)
        rows = (len(self.cards)+columns-1)//columns
        self.card_area.setFixedHeight(rows*34 + max(0, rows-1)*4 + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout_readouts()

    def nativeEvent(self, event_type, message):
        if IS_WINDOWS and bytes(event_type) in (b'windows_generic_MSG',
                                                b'windows_dispatcher_MSG'):
            try:
                import ctypes
                from ctypes import wintypes
                native_message = wintypes.MSG.from_address(int(message))
                if native_message.message == 0x0219:  # WM_DEVICECHANGE
                    self.windows_device_changed()
            except (TypeError, ValueError, OSError):
                logger.debug("无法解析 Windows 设备变化消息", exc_info=True)
        return super().nativeEvent(event_type, message)

    def resize_plot(self):
        self.right.setGeometry(self.plot.getViewBox().sceneBoundingRect())
        self.right.linkedViewChanged(self.plot.getViewBox(), self.right.XAxis)

    def set_configuration_locked(self, locked):
        signature = (bool(locked), self.engine.cap is not None, self.table.rowCount())
        if signature == self._configuration_lock_signature:
            return
        self._configuration_lock_signature = signature
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, col)
                if widget:
                    widget.setEnabled(not locked)
        self.experiment_name.setEnabled(not locked)
        self.target_path_edit.setEnabled(not locked)
        self.choose_target_btn.setEnabled(not locked)
        self.apply_btn.setEnabled(not locked and self.engine.cap is not None)
        self.load_btn.setEnabled(not locked and self.engine.cap is not None)
        self.save_btn.setEnabled(not locked and self.engine.cap is not None)

    def refresh(self):
        self.refresh_logging()
        e = self.engine
        if not e.lock.acquire(blocking=False):
            return
        try:
            self.refresh_connection_state(e)
            if e.cap and self.cap_identity != (e.cap.board_model, id(e.cap)):
                self.cap_identity = (e.cap.board_model, id(e.cap))
                self.populate_config()
            if self.seen_revision != e.revision:
                self.seen_revision = e.revision
                self.rebuild_display()
            recording = bool(e.recorder)
            stale = e.stale
            self.set_configuration_locked(recording or self.pending_start)
            enter_enabled = e.connected and e.cap is not None
            if self.enter_btn.isEnabled() != enter_enabled:
                self.enter_btn.setEnabled(enter_enabled)
            validation_signature = (self._config_input_revision, e.cap.adc_bits if e.cap else 14)
            if validation_signature != self._validated_config_signature:
                try:
                    draft = self.read_config()
                    for c in draft:
                        c.validate(validation_signature[1])
                    self._valid_channels = any(c.enabled for c in draft)
                except (ValueError, TypeError):
                    self._valid_channels = False
                self._validated_config_signature = validation_signature
            start_enabled = (self.target_ready and self._valid_channels and e.connected and
                             e.latest is not None and not recording and
                             not self.pending_start and not stale)
            if self.start_btn.isEnabled() != start_enabled:
                self.start_btn.setEnabled(start_enabled)
            if self.stop_btn.isEnabled() != recording:
                self.stop_btn.setEnabled(recording)
            plot_error = getattr(self, '_last_plot_error', None)
            waiting_timeout = bool(e.connected and not e.cap and e.connection_started and
                                   time.monotonic() - e.connection_started > 8)
            live_signature = (
                id(e.cap), e.revision, e.plot_epoch, e.sample_number,
                e.bad_frame_count, e.status, e.connected, e.simulated, stale,
                recording, self.target_ready, self.target_results_path,
                self.target_raw_path, plot_error, self.status_label.width(),
                waiting_timeout, self.diagnostic_toggle.isChecked(),
                self.tabs.currentIndex(),
            )
            if live_signature != self._live_ui_signature:
                self._live_ui_signature = live_signature
                acquisition_visible = self.tabs.currentIndex() == 1
                if acquisition_visible and recording != self._recording_ui_state:
                    self._recording_ui_state = recording
                    self.record_label.setText("● 正在记录" if recording else "预览 · 尚未记录")
                    self.record_label.setStyleSheet("color:#dc2626;font-weight:bold" if recording else "color:#64748b")
                if e.cap:
                    source = "SIMULATED 模拟数据 · " if e.simulated else "已连接 · " if e.connected else "已断开 · "
                    state = "已断开" if not e.connected else "等待首帧" if e.latest is None else "接收超时 / 旧值" if stale else "数据有效"
                    self.banner.setText(f"{source}{BOARD_NAMES.get(e.cap.board_model,e.cap.board_model)} | 启用 {sum(c.enabled for c in e.configs)} / {e.cap.channel_count} 路 | {e.cap.adc_bits} bit | {state}")
                    self.banner.setToolTip(f"本次连接：有效帧 {e.sample_number} · 坏帧 {e.bad_frame_count}\n{e.status}")
                else:
                    waiting = ("无法判断：端口已打开但未完成有效握手，请检查固件、波特率及设备状态"
                               if waiting_timeout else "等待设备 INFO / HEADER 和有效数据")
                    self.banner.setText((waiting if e.connected else "未连接") + f" | 本次连接：有效帧 {e.sample_number} · 坏帧 {e.bad_frame_count}")
                if acquisition_visible:
                    names = labels(e.configs)
                    for i, card in self.cards:
                        if i >= len(e.configs):
                            continue
                        c = e.configs[i]
                        p = e.physical[i]
                        val = "—" if p is None else f"{p-e.offsets[i]:.{c.decimals}f}"
                        raw = e.latest.raw[i] if e.latest else '—'
                        cal = "未实测标定" if c.mode == "名义换算" else "两点标定"
                        card.set_reading(val, c.unit, title=names[i], raw=raw, calibration=cal,
                                         stale=stale or not e.connected,
                                         over_range=p is not None and not c.minimum <= p <= c.maximum)
                    self.relayout_readouts()
                    self.status_label.setText('绘图范围异常 ⓘ' if plot_error else QtGui.QFontMetrics(self.status_label.font()).elidedText(e.status, QtCore.Qt.TextElideMode.ElideRight, max(100, self.status_label.width())))
                    self.status_label.setToolTip(plot_error or e.status)
                    if recording:
                        self.record_path.setText('正在写入：原始数据 + 测试结果 ⓘ')
                        self.record_path.setToolTip(f'测试结果：{e.recorder.path}\n原始数据：{e.recorder.raw_path}')
                    elif self.target_ready:
                        self.record_path.setText('保存位置已就绪 · 2 个 CSV ⓘ')
                        self.record_path.setToolTip(f'测试结果：{self.target_results_path}\n原始数据：{self.target_raw_path}')
                    else:
                        self.record_path.setText("下一次目标文件：尚未设置有效保存位置，请在第一页选择")
                    if self.diagnostic_toggle.isChecked():
                        self.raw_diagnostic.setText("最新全部原始值（仅诊断预览）：" + str(e.latest.raw) if e.latest else "尚未收到有效原始值")
            completed_path = self.last_recording_path if self.was_recording and not recording else None
            if recording:
                self.last_recording_path = str(e.recorder.path)
            self.was_recording = recording
            recorder_signature = ((str(e.recorder.path), str(e.recorder.raw_path))
                                  if e.recorder else None)
            if recorder_signature != self._recorder_path_signature:
                self._recorder_path_signature = recorder_signature
                self.active_record_path = str(Path(e.recorder.path).resolve()) if e.recorder else None
                self.active_record_paths = {str(Path(p).resolve()) for p in (e.recorder.path, e.recorder.raw_path)} if e.recorder else set()
            if self.record_outcomes != e.record_outcomes:
                self.record_outcomes = dict(e.record_outcomes)
            new_record_path = e.last_path if e.last_path and e.last_path != self.latest_seen_path else None
            if new_record_path:
                self.latest_seen_path = new_record_path
            plot_epoch = e.plot_epoch
            plot_sample_number = e.sample_number
            history_length = len(e.history)
            history_reset = (plot_epoch != self.seen_plot_epoch or
                             history_length < self.seen_plot_history_length)
            if history_reset:
                # Full copies happen only when a new visual segment begins
                # (connect/configure/zero), not on every incoming sample.
                history_delta = list(e.history)
            elif history_length > self.seen_plot_history_length:
                history_delta = e.history[self.seen_plot_history_length:history_length]
            else:
                history_delta = None
            redraw_plot = history_reset or history_delta is not None or self.plot_refresh_requested
        finally:
            e.lock.release()
        if completed_path:
            self.prepare_next_target(completed_path)
        if new_record_path:
            self.add_recent_file(recording_paths(new_record_path)[0])
            self.add_recent_file(new_record_path)
            if self.tabs.currentIndex() == 2:
                self.load_delivery(new_record_path)
        self.update_delivery_state()
        if history_reset:
            self.plot_history = history_delta
        elif history_delta is not None:
            self.plot_history.extend(history_delta)
        if redraw_plot:
            if self.tabs.currentIndex() == 1:
                self.last_plot = time.monotonic()
                self.update_plot(self.plot_history)
                self.plot_refresh_requested = False
            else:
                # Keep collecting history, but avoid Qt curve/style work on a
                # hidden page. on_tab_changed redraws the latest state.
                self.plot_refresh_requested = True
            self.seen_plot_epoch = plot_epoch
            self.seen_plot_sample_number = plot_sample_number
            self.seen_plot_history_length = history_length

    def update_plot(self, history):
        self.sync_plot_buffer(history)
        if not history:
            for curve, _ in self.curves:
                curve.setData([], [])
            self.plot.setXRange(0, 1, padding=0)
            self.apply_aligned_y_ranges({'N': [], 'mm': []})
            return
        length = self.plot_buffer_length
        all_x = self.plot_x[:length]
        end = float(all_x[-1])
        if self.range_mode.currentIndex() == 1:
            start = max(float(all_x[0]), end - float(self.window_sec.value()))
            first = int(np.searchsorted(all_x, start, side='left'))
        else:
            start = float(all_x[0])
            first = 0
        x = all_x[first:]
        ranges = {'N': [], 'mm': []}
        for k, (curve, _) in enumerate(self.curves):
            i = self.curve_channels[k]
            ys = self.plot_y[i][first:length]
            curve.setData(x, ys)
            if self.curve_checks[k].isChecked():
                if first == 0:
                    minimum, maximum = self.plot_minmax[i]
                else:
                    finite = ys[np.isfinite(ys)]
                    minimum = float(np.min(finite)) if finite.size else None
                    maximum = float(np.max(finite)) if finite.size else None
                if minimum is not None and maximum is not None:
                    ranges[self.engine.configs[i].unit or 'mm'].extend((minimum, maximum))
        self.plot.setXRange(start, max(start+1, end), padding=0)
        self.apply_aligned_y_ranges(ranges)

    def reset_plot_buffer(self):
        self.plot_buffer_source = None
        self.plot_buffer_length = 0
        self.plot_buffer_capacity = 0
        self.plot_x = np.empty(0, dtype=np.float64)
        self.plot_y = {channel: np.empty(0, dtype=np.float64) for channel in self.curve_channels}
        self.plot_minmax = {channel: [None, None] for channel in self.curve_channels}

    def ensure_plot_capacity(self, required):
        if required <= self.plot_buffer_capacity:
            return
        capacity = max(64, self.plot_buffer_capacity or 1)
        while capacity < required:
            capacity *= 2
        x = np.empty(capacity, dtype=np.float64)
        if self.plot_buffer_length:
            x[:self.plot_buffer_length] = self.plot_x[:self.plot_buffer_length]
        y_arrays = {}
        for channel in self.curve_channels:
            values = np.empty(capacity, dtype=np.float64)
            if self.plot_buffer_length and channel in self.plot_y:
                values[:self.plot_buffer_length] = self.plot_y[channel][:self.plot_buffer_length]
            y_arrays[channel] = values
        self.plot_x = x
        self.plot_y = y_arrays
        self.plot_buffer_capacity = capacity

    def sync_plot_buffer(self, history):
        channels_changed = set(self.plot_y) != set(self.curve_channels)
        rebuild = (self.plot_buffer_source is not history or
                   self.plot_buffer_length > len(history) or channels_changed)
        if rebuild:
            self.reset_plot_buffer()
            self.plot_buffer_source = history
        self.ensure_plot_capacity(len(history))
        start = self.plot_buffer_length
        for position in range(start, len(history)):
            x, values = history[position]
            self.plot_x[position] = x
            for channel in self.curve_channels:
                value = values[channel]
                if value is None or not math.isfinite(value):
                    self.plot_y[channel][position] = np.nan
                    continue
                value = float(value)
                self.plot_y[channel][position] = value
                minimum, maximum = self.plot_minmax[channel]
                self.plot_minmax[channel][0] = value if minimum is None else min(minimum, value)
                self.plot_minmax[channel][1] = value if maximum is None else max(maximum, value)
        self.plot_buffer_length = len(history)

    def apply_aligned_y_ranges(self, ranges):
        try:
            aligned = aligned_zero_ranges(ranges)
        except ValueError as error:
            if str(error) != getattr(self, '_last_plot_error', None):
                logger.error('绘图范围异常：%s', error)
            self._last_plot_error = str(error)
            if hasattr(self, 'status_label'):
                self.status_label.setText('绘图范围异常 ⓘ')
                self.status_label.setToolTip(str(error))
            return
        self._last_plot_error = None
        self.plot.setYRange(*aligned['N'], padding=0)
        self.right.setYRange(*aligned['mm'], padding=0)

    def closeEvent(self, event):
        if self.shutdown_done:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.centralWidget().setEnabled(False)
        self.timer.stop()
        self.target_timer.stop()
        self.driver_timer.stop()
        self.status_label.setText("正在结束记录并关闭设备…")
        def finished():
            self.shutdown_done = True
            self.executor.shutdown(wait=False)
            self.close()
        def failed(error):
            self.closing = False
            self.centralWidget().setEnabled(True)
            self.timer.start(UI_REFRESH_INTERVAL_MS)
            self.status_label.setText(f"关闭未完成，可检查后重试：{error}")
        def shutdown_all():
            self.engine.shutdown()
            self.delivery_executor.shutdown(wait=True)
        self.run_command(shutdown_all, finished, failed)


def main():
    from .splash import main as launch
    return launch()
