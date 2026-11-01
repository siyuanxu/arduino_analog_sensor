import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets as W

from desktop_app.readout import CompactReadout


@pytest.fixture(scope="module")
def qapp():
    app = W.QApplication.instance() or W.QApplication([])
    yield app


def visible_label_texts(card):
    return " ".join(label.text() for label in card.findChildren(W.QLabel) if label.isVisible())


def test_compact_single_line_size_and_text(qapp):
    card = CompactReadout("A0", "#2563eb")
    card.set_reading("0.000", "mm", title="水平位移1")
    card.show()
    qapp.processEvents()

    assert card.text() == "A0 0.000 mm"
    assert 120 <= card.width() <= 180
    assert 32 <= card.height() <= 36
    assert card.sizePolicy().horizontalPolicy() == W.QSizePolicy.Policy.Fixed
    assert visible_label_texts(card) == "A0 0.000 mm"


def test_details_are_tooltip_only(qapp):
    card = CompactReadout("A3", "#16a34a")
    card.set_reading(
        "12.34",
        "N",
        title="拉力传感器1",
        raw=1023,
        calibration="两点标定 (0, 0) → (1023, 5000)",
    )
    card.show()
    qapp.processEvents()

    visible = visible_label_texts(card)
    assert "拉力传感器" not in visible
    assert "1023" not in visible
    assert "两点标定" not in visible
    assert "拉力传感器1" in card.toolTip()
    assert "原始值：1023" in card.toolTip()
    assert "两点标定" in card.toolTip()
    assert "原始值：1023" in card.accessibleDescription()


def test_stale_and_over_range_have_visible_and_accessible_words(qapp):
    card = CompactReadout("A1", "#dc2626")
    card.set_reading(
        "-5.20", "mm", title="竖向位移1", stale=True, over_range=True
    )
    card.show()
    qapp.processEvents()

    assert "旧值" in card.text() and "超量程" in card.text()
    assert "旧值" in visible_label_texts(card)
    assert "超量程" in visible_label_texts(card)
    assert "A1" in card.accessibleName()
    assert "竖向位移1" in card.accessibleName()
    assert "-5.20 mm" in card.accessibleName()
    assert "旧值" in card.accessibleName() and "超量程" in card.accessibleName()


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("-123456.789", "kN"),
        ("0.000001234", "mm"),
        ("16383", "N"),
    ],
)
def test_long_and_negative_values_remain_complete(qapp, value, unit):
    card = CompactReadout("A15", "not-a-color")
    card.set_reading(value, unit)
    card.show()
    qapp.processEvents()

    assert value in card.text()
    assert value in visible_label_texts(card)
    assert unit in visible_label_texts(card)
    assert card.value_label.minimumWidth() >= card.value_label.fontMetrics().horizontalAdvance(value)
    assert 144 <= card.width() <= 220


def test_accessibility_updates_with_new_reading(qapp):
    card = CompactReadout("A2", "#9333ea")
    card.set_reading("1.0", "mm", title="水平位移2", raw=10)
    first_name = card.accessibleName()
    card.set_reading("2.5", "N", title="拉力传感器2", raw=20, stale=True)

    assert first_name != card.accessibleName()
    assert "拉力传感器2" in card.accessibleName()
    assert "2.5 N" in card.accessibleName()
    assert "旧值" in card.accessibleName()
    assert "原始值：20" in card.toolTip()


@pytest.mark.parametrize('states', [False, True])
def test_extreme_fixed_point_reading_uses_scientific_text_without_overlap(qapp, states):
    from PySide6 import QtGui
    qapp.setFont(QtGui.QFont('Arial', 10))
    card = CompactReadout('A15', '#2563eb')
    original = '10000000000000000000000000000000000000000.000000'
    card.set_reading(original, 'N', title='拉力1', stale=states, over_range=states)
    card.show()
    qapp.processEvents()
    assert 'E+' in card.value_label.text()
    assert original in card.toolTip() and original in card.accessibleName()
    labels = [card.channel_label, card.value_label, card.unit_label]
    if states:
        labels.append(card.status_label)
    for left, right in zip(labels, labels[1:]):
        assert left.geometry().right() < right.geometry().left()
    assert card.unit_label.text() == 'N' and card.width() <= 220
    card.set_reading('-123.456', 'mm')
    qapp.processEvents()
    assert card.value_label.text() == '-123.456'
    assert card.value_label.geometry().right() < card.unit_label.geometry().left()
    card.close()
    card.deleteLater()
