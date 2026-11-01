"""Compact, single-line channel readout used by the acquisition page."""

from PySide6 import QtCore, QtGui, QtWidgets as W
from decimal import Decimal, InvalidOperation


class CompactReadout(W.QFrame):
    """A fixed-size instrument readout with details kept out of the data view."""

    _NATURAL_WIDTH = 144
    _MAX_WIDTH = 220
    _HEIGHT = 34

    def __init__(self, channel_name: str, color: str, parent=None):
        super().__init__(parent)
        self._channel_name = str(channel_name).strip() or "—"
        parsed_color = QtGui.QColor(color)
        self._color = parsed_color.name() if parsed_color.isValid() else "#64748b"
        self._value = "—"
        self._unit = ""
        self._title = ""
        self._raw = None
        self._calibration = ""
        self._stale = False
        self._over_range = False

        self.setFrameShape(W.QFrame.Shape.NoFrame)
        self.setSizePolicy(W.QSizePolicy.Policy.Fixed, W.QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(120)
        self.setFixedHeight(self._HEIGHT)

        layout = W.QHBoxLayout(self)
        layout.setContentsMargins(7, 1, 7, 1)
        layout.setSpacing(4)

        self.channel_label = W.QLabel(self._channel_name, self)
        self.value_label = W.QLabel(self._value, self)
        self.unit_label = W.QLabel("", self)
        self.status_label = W.QLabel("", self)
        for label in (
            self.channel_label,
            self.value_label,
            self.unit_label,
            self.status_label,
        ):
            label.setWordWrap(False)
            label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            label.setSizePolicy(
                W.QSizePolicy.Policy.Fixed, W.QSizePolicy.Policy.Preferred
            )

        self._set_font(self.channel_label, 10, bold=True)
        self._set_font(self.value_label, 14, bold=True)
        self._set_font(self.unit_label, 11)
        self._set_font(self.status_label, 9, bold=True)
        self.channel_label.setStyleSheet("color:#475569")
        self.value_label.setStyleSheet("color:#0f172a")
        self.unit_label.setStyleSheet("color:#334155")

        layout.addWidget(self.channel_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.unit_label)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self._update_content()

    @staticmethod
    def _set_font(label: W.QLabel, pixel_size: int, *, bold: bool = False):
        font = QtGui.QFont(label.font())
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        label.setFont(font)

    def set_reading(
        self,
        value: str,
        unit: str,
        *,
        title: str = "",
        raw=None,
        calibration: str = "",
        stale: bool = False,
        over_range: bool = False,
    ):
        """Update the current reading and its non-visual diagnostic details."""

        self._value = str(value)
        self._unit = str(unit)
        self._title = str(title).strip()
        self._raw = raw
        self._calibration = str(calibration).strip()
        self._stale = bool(stale)
        self._over_range = bool(over_range)
        self._update_content()

    def text(self) -> str:
        """Return the readable display text (compatible with the old QLabel API)."""

        parts = [self._channel_name, self.value_label.text()]
        if self._unit:
            parts.append(self._unit)
        text = " ".join(parts)
        states = self._state_words()
        return f"{text} · {' · '.join(states)}" if states else text

    def _state_words(self):
        states = []
        if self._stale:
            states.append("旧值")
        if self._over_range:
            states.append("超量程")
        return states

    def _update_content(self):
        self.channel_label.setText(self._channel_name)
        self.value_label.setText(self._value)
        self.unit_label.setText(self._unit)
        states = self._state_words()
        self.status_label.setText("·".join(states))
        self.status_label.setVisible(bool(states))

        if self._over_range:
            border = "#b91c1c"
            background = "#fff7f7"
            self.status_label.setStyleSheet("color:#991b1b")
        elif self._stale:
            border = "#64748b"
            background = "#f8fafc"
            self.status_label.setStyleSheet("color:#475569")
        else:
            border = "#dbe4ed"
            background = "#ffffff"
            self.status_label.setStyleSheet("color:#475569")
        self.setStyleSheet(
            "QFrame {"
            f"background:{background};border:1px solid {border};"
            f"border-left:2px solid {self._color};border-radius:3px;"
            "}"
            "QLabel {border:0;background:transparent;}"
        )

        title = self._title or self._channel_name
        reading = f"{self._value} {self._unit}".strip()
        state_text = "、".join(states)
        accessible = f"{self._channel_name} {title}，测值 {reading}"
        if state_text:
            accessible += f"，{state_text}"
        self.setAccessibleName(accessible)

        details = [f"通道：{self._channel_name}", f"传感器：{title}", f"测值：{reading}"]
        if self._raw is not None:
            details.append(f"原始值：{self._raw}")
        if self._calibration:
            details.append(f"标定：{self._calibration}")
        if state_text:
            details.append(f"状态：{state_text}")
        description = "\n".join(details)
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        self._fit_width()

    def _fit_width(self):
        """Fit realistic long readings without allowing cards to fill the page."""

        self._set_font(self.value_label, 14, bold=True)
        margins = self.layout().contentsMargins()
        spacing = self.layout().spacing()

        def required_width():
            labels = [self.channel_label, self.value_label]
            if self._unit:
                labels.append(self.unit_label)
            if not self.status_label.isHidden():
                labels.append(self.status_label)
            return (
                margins.left()
                + margins.right()
                + sum(QtGui.QFontMetrics(label.font()).horizontalAdvance(label.text()) for label in labels)
                + spacing * max(0, len(labels) - 1)
                + 8
            )

        needed = required_width()
        for pixel_size in (13, 12, 11, 10):
            if needed <= self._MAX_WIDTH:
                break
            self._set_font(self.value_label, pixel_size, bold=True)
            needed = required_width()

        # Extreme finite ranges can produce hundreds of fixed-point digits.
        # Prefer a readable scientific value to overlapping its unit/status.
        if needed > self._MAX_WIDTH:
            try:
                numeric = Decimal(self._value)
                if numeric.is_finite():
                    for precision in range(14, -1, -1):
                        self.value_label.setText(format(numeric, f'.{precision}E'))
                        needed = required_width()
                        if needed <= self._MAX_WIDTH:
                            break
            except InvalidOperation:
                pass

        width = max(self._NATURAL_WIDTH, min(self._MAX_WIDTH, needed))
        self.setFixedWidth(width)
        self.value_label.setMinimumWidth(
            QtGui.QFontMetrics(self.value_label.font()).horizontalAdvance(self.value_label.text())
        )

