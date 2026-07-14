"""配置向导的设计令牌、QSS 与无障碍辅助函数。"""

from __future__ import annotations

import re

from meapet.ui_theme import (
    BUNDLED_CHEVRON_DOWN_PATH,
    BUNDLED_CHEVRON_UP_PATH,
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
    MIN_TARGET_SIZE,
    MONO_FONT_FAMILY,
    PALETTE,
    RADIUS_LARGE,
    RADIUS_MEDIUM,
    RADIUS_SMALL,
    rgba,
)


COLOR_BG = PALETTE["canvas"]
COLOR_CARD = PALETTE["surface"]
COLOR_ELEVATED = PALETTE["surface_elevated"]
COLOR_INPUT = PALETTE["surface_input"]
COLOR_ACCENT = PALETTE["primary"]
COLOR_ACCENT_2 = PALETTE["secondary"]
COLOR_FOCUS = PALETTE["focus"]
COLOR_TEXT = PALETTE["text_primary"]
COLOR_TEXT_SECONDARY = PALETTE["text_secondary"]
COLOR_MUTED = PALETTE["text_muted"]
COLOR_BORDER = PALETTE["border"]
COLOR_BORDER_STRONG = PALETTE["border_strong"]
COLOR_OK = PALETTE["success"]
COLOR_WARN = PALETTE["warning"]
COLOR_ERR = PALETTE["danger"]


STYLE_PAGE_CARD = f"""
    QFrame#PageCard {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
    }}
"""

STYLE_INPUT = f"""
    QLineEdit {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 12px;
        font-size: 14px;
        selection-background-color: {rgba(COLOR_ACCENT, 105)};
    }}
    QLineEdit:hover {{
        border-color: {COLOR_MUTED};
    }}
    QLineEdit:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 11px;
    }}
    QLineEdit:disabled {{
        background: {rgba(COLOR_INPUT, 150)};
        color: {rgba(COLOR_MUTED, 150)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
"""

STYLE_BTN_PRIMARY = f"""
    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        color: {PALETTE['on_primary']};
        border: 1px solid {rgba(COLOR_ACCENT, 210)};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 22px;
        font-size: 14px;
        font-weight: 700;
    }}
    QPushButton:hover {{
        background: {PALETTE['primary_hover']};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 21px;
    }}
    QPushButton:pressed {{
        background: {COLOR_ACCENT};
    }}
    QPushButton:disabled {{
        background: {COLOR_ELEVATED};
        color: {rgba(COLOR_MUTED, 150)};
        border-color: {COLOR_BORDER};
    }}
"""

STYLE_BTN_SECONDARY = f"""
    QPushButton {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 18px;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {rgba(COLOR_FOCUS, 28)};
        border-color: {COLOR_MUTED};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 17px;
    }}
    QPushButton:pressed {{
        background: {rgba(COLOR_FOCUS, 45)};
    }}
    QPushButton:disabled {{
        background: {rgba(COLOR_ELEVATED, 145)};
        color: {rgba(COLOR_MUTED, 135)};
        border-color: {rgba(COLOR_BORDER, 145)};
    }}
"""


WIZARD_STYLESHEET = f"""
    QDialog {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
    }}
    QWidget#WizardRoot {{
        background: transparent;
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}
    QFrame#WizardShell {{
        background: {COLOR_BG};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_LARGE}px;
    }}
    QFrame#WizardHeader,
    QFrame#WizardFooter {{
        background: {COLOR_BG};
        border: none;
    }}
    QFrame#WizardDivider {{
        background: {COLOR_BORDER};
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}
    QFrame#PageCard {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QFrame#SectionCard {{
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {COLOR_TEXT};
    }}
    QLabel#BrandMark {{
        background: {COLOR_ACCENT};
        color: {PALETTE['on_primary']};
        border-radius: 14px;
        font-size: 14px;
        font-weight: 800;
    }}
    QLabel#BrandName {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#StepLabel {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 12px;
        font-weight: 600;
        padding: 5px 10px;
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER};
        border-radius: 10px;
    }}
    QLabel#ConfigStatus {{
        min-height: 22px;
        color: {COLOR_TEXT_SECONDARY};
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#ConfigStatus[status="error"] {{
        color: {COLOR_ERR};
    }}
    QLabel#ConfigStatus[status="success"] {{
        color: {COLOR_OK};
    }}
    QLabel#PageEyebrow {{
        color: {COLOR_ACCENT};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#PageTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 22px;
        font-weight: 700;
    }}
    QLabel#PageDescription {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 13px;
    }}
    QLabel#FieldLabel {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#HelperText {{
        color: {COLOR_MUTED};
        font-size: 12px;
    }}
    QLabel#FontScaleValue {{
        color: {COLOR_ACCENT};
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_SMALL}px;
        padding: 5px 8px;
        font-size: 14px;
        font-weight: 700;
    }}
    QLabel[status="success"] {{
        color: {COLOR_OK};
    }}
    QLabel[status="warning"] {{
        color: {COLOR_WARN};
    }}
    QLabel[status="error"] {{
        color: {COLOR_ERR};
    }}
    QLabel[status="muted"] {{
        color: {COLOR_MUTED};
    }}
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 12px;
        selection-background-color: {rgba(COLOR_ACCENT, 105)};
    }}
    QLineEdit:hover,
    QTextEdit:hover,
    QPlainTextEdit:hover,
    QComboBox:hover,
    QSpinBox:hover,
    QDoubleSpinBox:hover {{
        border-color: {COLOR_MUTED};
    }}
    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QComboBox:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 11px;
    }}
    QLineEdit:disabled,
    QTextEdit:disabled,
    QPlainTextEdit:disabled,
    QComboBox:disabled,
    QSpinBox:disabled,
    QDoubleSpinBox:disabled {{
        background: {rgba(COLOR_INPUT, 150)};
        color: {rgba(COLOR_MUTED, 150)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
    QSpinBox,
    QDoubleSpinBox {{
        padding-right: 34px;
    }}
    QSpinBox::up-button,
    QDoubleSpinBox::up-button,
    QSpinBox::down-button,
    QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        width: 28px;
        color: {COLOR_TEXT_SECONDARY};
        background: {COLOR_ELEVATED};
        border-left: 1px solid {COLOR_BORDER_STRONG};
    }}
    QSpinBox::up-button,
    QDoubleSpinBox::up-button {{
        subcontrol-position: top right;
        border-bottom: 1px solid {COLOR_BORDER};
        border-top-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::down-button,
    QDoubleSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::up-button:hover,
    QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover,
    QDoubleSpinBox::down-button:hover {{
        background: {rgba(COLOR_FOCUS, 40)};
        border-left-color: {COLOR_FOCUS};
    }}
    QSpinBox::up-arrow,
    QDoubleSpinBox::up-arrow {{
        image: url("{BUNDLED_CHEVRON_UP_PATH}");
        width: 10px;
        height: 7px;
    }}
    QSpinBox::down-arrow,
    QDoubleSpinBox::down-arrow,
    QComboBox::down-arrow {{
        image: url("{BUNDLED_CHEVRON_DOWN_PATH}");
        width: 10px;
        height: 7px;
    }}
    QTextEdit#LogOutput {{
        color: {COLOR_TEXT_SECONDARY};
        font-family: {MONO_FONT_FAMILY};
        font-size: 12px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 34px;
    }}
    QComboBox QAbstractItemView {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        selection-background-color: {rgba(COLOR_ACCENT, 80)};
        selection-color: {COLOR_TEXT};
        padding: 4px;
    }}
    QCheckBox,
    QRadioButton {{
        color: {COLOR_TEXT};
        spacing: 10px;
        font-size: 14px;
    }}
    QCheckBox::indicator,
    QRadioButton::indicator {{
        width: 20px;
        height: 20px;
        border: 2px solid {COLOR_BORDER_STRONG};
        background: {COLOR_INPUT};
    }}
    QCheckBox::indicator {{
        border-radius: 5px;
    }}
    QRadioButton::indicator {{
        border-radius: 11px;
    }}
    QCheckBox::indicator:checked,
    QRadioButton::indicator:checked {{
        background: {COLOR_ACCENT};
        border-color: {COLOR_FOCUS};
    }}
    QCheckBox:focus,
    QRadioButton:focus {{
        color: {COLOR_FOCUS};
    }}
    QPushButton {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {rgba(COLOR_FOCUS, 28)};
        border-color: {COLOR_MUTED};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 15px;
    }}
    QPushButton:pressed {{
        background: {rgba(COLOR_FOCUS, 45)};
    }}
    QPushButton:disabled {{
        color: {rgba(COLOR_MUTED, 135)};
        background: {rgba(COLOR_ELEVATED, 145)};
        border-color: {rgba(COLOR_BORDER, 145)};
    }}
    QPushButton#PrimaryButton {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 15px;
        color: {PALETTE['on_primary']};
        border-color: {COLOR_ACCENT};
        font-weight: 700;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {PALETTE['primary_hover']};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton#CloseButton {{
        background: transparent;
        color: {COLOR_MUTED};
        border-color: transparent;
        border-radius: 10px;
        font-size: 17px;
        padding: 0;
    }}
    QPushButton#CloseButton:hover {{
        background: {rgba(COLOR_ERR, 38)};
        color: {COLOR_ERR};
        border-color: {rgba(COLOR_ERR, 90)};
    }}
    QPushButton#CloseButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QProgressBar {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 4px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        border-radius: 3px;
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {COLOR_BORDER};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        border-radius: 3px;
    }}
    QSlider::add-page:horizontal {{
        background: {COLOR_BORDER};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        width: 20px;
        margin: -8px 0;
        background: {COLOR_TEXT};
        border: 3px solid {COLOR_ACCENT};
        border-radius: 10px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {COLOR_FOCUS};
        border-color: {COLOR_FOCUS};
    }}
    QSlider::handle:horizontal:pressed {{
        background: {COLOR_ACCENT};
    }}
    QTabWidget#ConfigurationTabs {{
        background: transparent;
        border: none;
    }}
    QTabWidget#ConfigurationTabs::pane {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
        top: -1px;
        margin: 0 18px 10px 18px;
    }}
    QTabBar::tab {{
        min-width: 112px;
        min-height: 28px;
        padding: 8px 16px;
        margin: 0 4px 0 0;
        color: {COLOR_TEXT_SECONDARY};
        background: transparent;
        border: 1px solid transparent;
        border-top-left-radius: {RADIUS_SMALL}px;
        border-top-right-radius: {RADIUS_SMALL}px;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{
        color: {COLOR_TEXT};
        background: {rgba(COLOR_FOCUS, 22)};
    }}
    QTabBar::tab:selected {{
        color: {COLOR_TEXT};
        background: {COLOR_CARD};
        border-color: {COLOR_BORDER};
        border-bottom-color: {COLOR_CARD};
        font-weight: 700;
    }}
    QTabBar::tab:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QWidget#ConfigurationTabContent {{
        background: transparent;
    }}
    QScrollArea#ConfigurationTabScroll {{
        background: transparent;
        border: none;
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {COLOR_BORDER_STRONG};
        border-radius: 4px;
        min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {COLOR_MUTED};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QToolTip {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        padding: 6px 8px;
    }}
"""


def set_status(widget, status: str, text: str | None = None) -> None:
    """给状态标签设置语义属性，并触发 QSS 重绘。"""
    if text is not None:
        widget.setText(text)
    if not hasattr(widget, "setProperty"):
        fallback_colors = {
            "success": COLOR_OK,
            "warning": COLOR_WARN,
            "error": COLOR_ERR,
            "muted": COLOR_MUTED,
        }
        if hasattr(widget, "setStyleSheet"):
            widget.setStyleSheet(f"color: {fallback_colors.get(status, COLOR_TEXT)};")
        return

    widget.setProperty("status", status)
    if hasattr(widget, "style"):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    if hasattr(widget, "update"):
        widget.update()


def prepare_accessible_page(root) -> None:
    """统一表单交互尺寸、焦点策略与可访问名称。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QSlider,
        QTextEdit,
    )

    for button in root.findChildren(QAbstractButton):
        if button.maximumWidth() < MIN_TARGET_SIZE:
            button.setMaximumWidth(MIN_TARGET_SIZE)
        button.setMinimumSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        button.setFocusPolicy(Qt.StrongFocus)
        if not button.accessibleName():
            label = _plain_accessible_text(button.text())
            button.setAccessibleName(label or button.toolTip() or "操作按钮")

    text_controls = (
        root.findChildren(QLineEdit)
        + root.findChildren(QTextEdit)
        + root.findChildren(QPlainTextEdit)
    )
    for control in text_controls:
        control.setMinimumHeight(MIN_TARGET_SIZE)
        control.setFocusPolicy(Qt.StrongFocus)
        if not control.accessibleName():
            label = control.placeholderText() if hasattr(control, "placeholderText") else ""
            control.setAccessibleName(label or control.objectName() or "配置输入")
        if not control.accessibleDescription() and hasattr(control, "placeholderText"):
            control.setAccessibleDescription(control.placeholderText())

    for combo in root.findChildren(QComboBox):
        combo.setMinimumHeight(MIN_TARGET_SIZE)
        combo.setFocusPolicy(Qt.StrongFocus)
        if not combo.accessibleName():
            combo.setAccessibleName(combo.objectName() or "配置选项")

    for spin_box in root.findChildren(QAbstractSpinBox):
        spin_box.setMinimumHeight(MIN_TARGET_SIZE)
        spin_box.setFocusPolicy(Qt.StrongFocus)
        if not spin_box.accessibleName():
            spin_box.setAccessibleName(spin_box.objectName() or "数值设置")

    for slider in root.findChildren(QSlider):
        slider.setMinimumHeight(MIN_TARGET_SIZE)
        slider.setFocusPolicy(Qt.StrongFocus)
        if not slider.accessibleName():
            slider.setAccessibleName(slider.objectName() or "数值调节")


def _plain_accessible_text(text: str) -> str:
    """去掉按钮文字首尾的装饰符号，同时保留中文和英文标签。"""
    value = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or ""))
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff）)]+$", "", value)
    return value.strip()
