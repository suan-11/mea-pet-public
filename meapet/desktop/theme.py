"""桌面浮窗、菜单与对话框的统一 MeaPet 主题。"""

from __future__ import annotations

from meapet.ui_theme import (
    BUNDLED_CHEVRON_DOWN_PATH,
    BUNDLED_CHEVRON_UP_PATH,
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
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
COLOR_TEXT = PALETTE["text_primary"]
COLOR_SECONDARY = PALETTE["text_secondary"]
COLOR_MUTED = PALETTE["text_muted"]
COLOR_BORDER = PALETTE["border"]
COLOR_BORDER_STRONG = PALETTE["border_strong"]
COLOR_FOCUS = PALETTE["focus"]
COLOR_OK = PALETTE["success"]
COLOR_WARN = PALETTE["warning"]
COLOR_ERR = PALETTE["danger"]


MENU_STYLE = f"""
    QMenu {{
        background: {rgba(COLOR_CARD, 252)};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 5px;
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}
    QMenu::item {{
        min-height: 32px;
        padding: 6px 26px 6px 12px;
        border: 1px solid transparent;
        border-radius: 6px;
        margin: 1px 0;
    }}
    QMenu::item:selected {{
        background: {rgba(COLOR_FOCUS, 35)};
        border-color: {rgba(COLOR_FOCUS, 90)};
        color: {COLOR_TEXT};
    }}
    QMenu::item:disabled {{
        color: {rgba(COLOR_MUTED, 135)};
    }}
    QMenu::separator {{
        height: 1px;
        background: {COLOR_BORDER};
        margin: 4px 8px;
    }}
    QMenu::indicator {{
        width: 14px;
        height: 14px;
        left: 7px;
    }}
"""


DIALOG_STYLE = f"""
    QDialog {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
    }}
    QFrame#SizeDialogCard {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QLabel {{
        color: {COLOR_TEXT};
        background: transparent;
        border: none;
    }}
    QLabel#ScaleValue {{
        color: {COLOR_ACCENT};
        font-size: 24px;
        font-weight: 750;
    }}
    QPushButton {{
        min-height: 44px;
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 8px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {rgba(COLOR_FOCUS, 28)};
        border-color: {COLOR_MUTED};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 7px 15px;
    }}
    QPushButton#PrimaryButton {{
        background: {COLOR_ACCENT};
        color: {PALETTE['on_primary']};
        border-color: {COLOR_ACCENT};
    }}
    QPushButton#PrimaryButton:hover {{
        background: {PALETTE['primary_hover']};
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {COLOR_BORDER};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {COLOR_ACCENT};
        border: 2px solid {COLOR_FOCUS};
        width: 20px;
        margin: -8px 0;
        border-radius: 11px;
    }}
    QSlider::sub-page:horizontal {{
        background: {COLOR_ACCENT};
        border-radius: 3px;
    }}
    QSlider:focus {{
        border: 1px solid {COLOR_FOCUS};
        border-radius: 5px;
    }}
"""


CONSENT_DIALOG_STYLE = f"""
    QDialog#CloudConsentRoot,
    QDialog#CaptureScopeConsentRoot {{
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        background: transparent;
    }}
    QFrame#CloudConsentCard {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_LARGE}px;
    }}
    QFrame#SectionCard {{
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_SMALL}px;
    }}
    QLabel {{
        color: {COLOR_TEXT};
        background: transparent;
        border: none;
    }}
    QLabel#ConsentEyebrow {{
        color: {COLOR_WARN};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#ConsentTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 20px;
        font-weight: 750;
    }}
    QLabel#ConsentBody {{
        color: {COLOR_SECONDARY};
        font-size: 13px;
    }}
    QLabel#FieldLabel {{
        color: {COLOR_SECONDARY};
        font-size: 12px;
        font-weight: 650;
    }}
    QLabel#HelperText {{
        color: {COLOR_MUTED};
        font-size: 11px;
    }}
    QLabel#ConsentValidation {{
        color: {COLOR_ERR};
        font-size: 12px;
        font-weight: 650;
        padding: 5px 8px;
        background: {rgba(COLOR_ERR, 18)};
        border: 1px solid {rgba(COLOR_ERR, 75)};
        border-radius: {RADIUS_SMALL}px;
    }}
    QLabel#ConsentCountdown {{
        color: {COLOR_WARN};
        font-size: 12px;
        font-weight: 650;
        padding: 6px 10px;
        background: {rgba(COLOR_WARN, 18)};
        border: 1px solid {rgba(COLOR_WARN, 70)};
        border-radius: {RADIUS_SMALL}px;
    }}
    QComboBox,
    QLineEdit,
    QSpinBox {{
        min-height: 42px;
        color: {COLOR_TEXT};
        background: {COLOR_INPUT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 0 12px;
        selection-background-color: {rgba(COLOR_ACCENT, 105)};
    }}
    QComboBox:hover,
    QLineEdit:hover,
    QSpinBox:hover {{
        border-color: {COLOR_MUTED};
    }}
    QComboBox:focus,
    QLineEdit:focus,
    QSpinBox:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 32px;
        background: {COLOR_ELEVATED};
        border: none;
        border-left: 1px solid {COLOR_BORDER_STRONG};
        border-top-right-radius: {RADIUS_SMALL - 1}px;
        border-bottom-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QComboBox::down-arrow {{
        image: url("{BUNDLED_CHEVRON_DOWN_PATH}");
        width: 10px;
        height: 7px;
    }}
    QComboBox QAbstractItemView {{
        color: {COLOR_TEXT};
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER_STRONG};
        selection-color: {COLOR_TEXT};
        selection-background-color: {rgba(COLOR_FOCUS, 45)};
        padding: 4px;
    }}
    QSpinBox {{
        padding-right: 32px;
    }}
    QSpinBox::up-button,
    QSpinBox::down-button {{
        subcontrol-origin: border;
        width: 26px;
        color: {COLOR_SECONDARY};
        background: {COLOR_CARD};
        border-left: 1px solid {COLOR_BORDER_STRONG};
    }}
    QSpinBox::up-button {{
        subcontrol-position: top right;
        border-bottom: 1px solid {COLOR_BORDER};
        border-top-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::up-button:hover,
    QSpinBox::down-button:hover {{
        background: {rgba(COLOR_FOCUS, 40)};
        border-left-color: {COLOR_FOCUS};
    }}
    QSpinBox::up-arrow {{
        image: url("{BUNDLED_CHEVRON_UP_PATH}");
        width: 10px;
        height: 7px;
    }}
    QSpinBox::down-arrow {{
        image: url("{BUNDLED_CHEVRON_DOWN_PATH}");
        width: 10px;
        height: 7px;
    }}
    QPushButton {{
        min-width: 112px;
        min-height: 44px;
        padding: 0 16px;
        color: {COLOR_TEXT};
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        font-weight: 650;
    }}
    QPushButton:hover {{
        background: {rgba(COLOR_FOCUS, 28)};
        border-color: {COLOR_MUTED};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QPushButton#AllowUploadButton {{
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 14px;
        color: {PALETTE['on_primary']};
        background: {COLOR_ACCENT};
        border-color: {COLOR_ACCENT};
    }}
    QPushButton#AllowUploadButton:hover {{
        background: {PALETTE['primary_hover']};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton#CancelUploadButton:default {{
        color: {COLOR_TEXT};
        border: 2px solid {COLOR_FOCUS};
        background: {rgba(COLOR_FOCUS, 24)};
    }}
"""


CHAT_COMPOSER_STYLE = f"""
    QWidget#ChatComposerRoot {{
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        background: transparent;
    }}
    QFrame#ChatComposer {{
        background: {rgba(COLOR_CARD, 250)};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QLabel#ComposerTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#ComposerHint {{
        color: {COLOR_MUTED};
        font-size: 11px;
    }}
    QLabel#ComposerFeedback {{
        color: {COLOR_ERR};
        font-size: 11px;
    }}
    QLineEdit {{
        min-height: 44px;
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 0 14px;
        font-size: 14px;
        selection-background-color: {rgba(COLOR_ACCENT, 100)};
    }}
    QLineEdit:hover {{
        border-color: {COLOR_MUTED};
    }}
    QLineEdit:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 0 13px;
    }}
    QPushButton {{
        min-height: 44px;
        min-width: 44px;
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 0 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {rgba(COLOR_FOCUS, 28)};
        border-color: {COLOR_MUTED};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QPushButton#SendButton {{
        min-width: 80px;
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 15px;
        background: {COLOR_ACCENT};
        color: {PALETTE['on_primary']};
        border-color: {COLOR_ACCENT};
    }}
    QPushButton#SendButton:hover {{
        background: {PALETTE['primary_hover']};
    }}
    QPushButton#ComposerCloseButton {{
        background: transparent;
        color: {COLOR_MUTED};
        border-color: transparent;
        padding: 0;
        font-size: 18px;
    }}
    QPushButton#ComposerCloseButton:hover {{
        background: {rgba(COLOR_ERR, 35)};
        color: {COLOR_ERR};
        border-color: {rgba(COLOR_ERR, 90)};
    }}
"""


DIALOGUE_STYLE = f"""
    QFrame#DialogueBubble {{
        background: transparent;
        border: none;
    }}
    QLabel#DialogueText {{
        background: transparent;
        color: {COLOR_TEXT};
        border: none;
        padding: 0;
        font-family: {FONT_FAMILY};
        font-size: 15px;
        font-weight: 500;
    }}
    QScrollArea#DialogueScroll,
    QScrollArea#DialogueScroll > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QScrollArea#DialogueScroll QScrollBar:vertical {{
        width: 8px;
        margin: 4px 2px;
        background: transparent;
    }}
    QScrollArea#DialogueScroll QScrollBar::handle:vertical {{
        min-height: 28px;
        border-radius: 3px;
        background: {rgba(COLOR_ACCENT, 150)};
    }}
    QScrollArea#DialogueScroll QScrollBar::add-line:vertical,
    QScrollArea#DialogueScroll QScrollBar::sub-line:vertical {{
        height: 0;
    }}
"""


STATUS_PANEL_STYLE = f"""
    QWidget#StatusPanelRoot {{
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        background: transparent;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {COLOR_TEXT};
    }}
    QLabel#PanelEyebrow {{
        color: {COLOR_ACCENT};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#PanelTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 22px;
        font-weight: 700;
    }}
    QLabel#TierLabel {{
        color: {COLOR_WARN};
        font-size: 17px;
        font-weight: 700;
    }}
    QLabel#QuoteLabel {{
        color: {COLOR_SECONDARY};
        font-size: 13px;
        font-style: italic;
    }}
    QLabel#StatsLabel {{
        color: {COLOR_SECONDARY};
        font-size: 13px;
    }}
    QLabel#MemoryLabel {{
        color: {COLOR_MUTED};
        font-size: 12px;
    }}
    QLabel#PanelHint {{
        color: {COLOR_MUTED};
        font-size: 11px;
    }}
    QFrame#StatusCard {{
        background: {rgba(COLOR_CARD, 230)};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QPushButton#PanelCloseButton {{
        min-width: 44px;
        min-height: 44px;
        background: {rgba(COLOR_CARD, 225)};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: 10px;
        font-weight: 600;
    }}
    QPushButton#PanelCloseButton:hover {{
        background: {rgba(COLOR_ERR, 45)};
        color: {COLOR_ERR};
        border-color: {COLOR_ERR};
    }}
    QPushButton#PanelCloseButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QProgressBar {{
        min-height: 20px;
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: 6px;
        text-align: center;
        font-weight: 700;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        border-radius: 5px;
    }}
"""


SPLASH_STYLE = f"""
    QWidget#SplashRoot {{
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        background: transparent;
    }}
    QFrame#SplashCard {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_LARGE}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QLabel#SplashMark {{
        background: {COLOR_ACCENT};
        color: {PALETTE['on_primary']};
        border-radius: 18px;
        font-size: 18px;
        font-weight: 800;
    }}
    QLabel#SplashTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 26px;
        font-weight: 750;
    }}
    QLabel#SplashSubtitle {{
        color: {COLOR_SECONDARY};
        font-size: 13px;
    }}
    QLabel#SplashStatus {{
        color: {COLOR_TEXT};
        font-size: 14px;
        font-weight: 600;
    }}
    QLabel#SplashStatus[status="success"] {{
        color: {COLOR_OK};
    }}
    QLabel#SplashStatus[status="error"] {{
        color: {COLOR_ERR};
    }}
    QLabel#SplashDetail,
    QLabel#SplashHint {{
        color: {COLOR_MUTED};
        font-size: 11px;
    }}
    QProgressBar {{
        background: {COLOR_ELEVATED};
        border: 1px solid {COLOR_BORDER};
        border-radius: 4px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLOR_ACCENT}, stop:1 {COLOR_ACCENT_2});
        border-radius: 3px;
    }}
"""
