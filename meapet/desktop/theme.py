"""桌面浮窗、菜单与对话框的统一 MeaPet 主题（曜石夜/晨白双主题）。"""

from __future__ import annotations

from meapet import ui_theme as _theme
from meapet import ui_theme as _ui_theme_mod
from meapet.ui_theme import (
    BUNDLED_CHEVRON_DOWN_PATH,
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
    PALETTE,
    RADIUS_LARGE,
    RADIUS_MEDIUM,
    RADIUS_SMALL,
    rgba,
    seam_highlight,
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


def rebuild_styles() -> None:
    """按当前活动调色板重建桌面端全部命名 QSS。"""
    global COLOR_BG, COLOR_CARD, COLOR_ELEVATED, COLOR_INPUT, COLOR_ACCENT
    global COLOR_ACCENT_2, COLOR_TEXT, COLOR_SECONDARY, COLOR_MUTED
    global COLOR_BORDER, COLOR_BORDER_STRONG, COLOR_FOCUS
    global COLOR_OK, COLOR_WARN, COLOR_ERR

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

    global MENU_STYLE
    global PET_MENU_WINDOW_STYLE
    global DIALOG_STYLE
    global CONSENT_DIALOG_STYLE
    global CHAT_COMPOSER_STYLE
    global DIALOGUE_STYLE
    global STATUS_PANEL_STYLE
    global SPLASH_STYLE

    MENU_STYLE = f"""
        QMenu {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba({_theme.rgb_triplet(PALETTE['card_grad_top'])}, 252),
                stop:0.5 rgba({_theme.rgb_triplet(PALETTE['surface'])}, 252),
                stop:1 rgba({_theme.rgb_triplet(PALETTE['card_grad_bottom'])}, 252));
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: 12px;
            padding: 6px;
            font-family: {FONT_FAMILY};
            font-size: 14px;
        }}
        QMenu::item {{
            min-height: 34px;
            padding: 8px 28px 8px 14px;
            border: 1px solid transparent;
            border-radius: 8px;
            margin: 2px 4px;
        }}
        QMenu::item:selected {{
            background: {rgba(COLOR_TEXT, 24)};
            border-color: transparent;
            color: {COLOR_TEXT};
        }}
        QMenu::item:pressed {{
            background: {rgba(COLOR_TEXT, 38)};
        }}
        QMenu::item:disabled {{
            color: {rgba(COLOR_MUTED, 130)};
        }}
        QMenu::separator {{
            height: 1px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {COLOR_BORDER},
                stop:0.7 {rgba(COLOR_BORDER, 120)},
                stop:1 {rgba(COLOR_BORDER, 0)});
            margin: 5px 12px;
        }}
        QMenu::indicator {{
            width: 14px;
            height: 14px;
            left: 8px;
        }}
        QMenu::icon {{
            left: 10px;
        }}
    """


    PET_MENU_WINDOW_STYLE = f"""
        QWidget#PetMenuWindowRoot,
        QWidget#PetMenuSubmenuRoot {{
            background: transparent;
            font-family: {FONT_FAMILY};
        }}
        QFrame#PetMenuCard {{
            background: {rgba(COLOR_CARD, 252)};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_MEDIUM}px;
        }}
        QLabel#PetMenuTitle {{
            background: transparent;
            color: {COLOR_TEXT};
            font-size: 13px;
            font-weight: 600;
        }}
        QLabel#PetMenuHint {{
            background: transparent;
            color: {rgba(COLOR_MUTED, 170)};
            font-size: 11px;
        }}
        QLabel#PetMenuSubmenuTitle {{
            background: transparent;
            color: {COLOR_TEXT};
            font-size: 13px;
            font-weight: 700;
        }}
        QLabel#PetMenuSubmenuHint {{
            background: transparent;
            color: {rgba(COLOR_MUTED, 165)};
            font-size: 10px;
        }}
        QWidget#PetMenuHeader {{
            background: transparent;
        }}
        QWidget#PetMenuSubmenuHeader {{
            background: {rgba(COLOR_TEXT, 14)};
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
        }}
        QPushButton#PetMenuCloseButton {{
            background: transparent;
            color: {COLOR_SECONDARY};
            border: 1px solid transparent;
            border-radius: 8px;
            min-width: 28px;
            min-height: 28px;
            font-size: 14px;
            font-weight: 700;
        }}
        QPushButton#PetMenuCloseButton:hover {{
            background: {rgba(COLOR_ERR, 45)};
            border-color: {rgba(COLOR_ERR, 120)};
            color: {COLOR_ERR};
        }}
        QPushButton#PetMenuItem, QPushButton#PetMenuGroup {{
            background: transparent;
            color: {COLOR_TEXT};
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 8px 14px;
            min-height: 34px;
            font-family: {FONT_FAMILY};
            font-size: 14px;
            text-align: left;
        }}
        QPushButton#PetMenuGroup {{
            color: {COLOR_SECONDARY};
            font-weight: 600;
        }}
        QPushButton#PetMenuGroup[expanded="true"] {{
            background: {rgba(COLOR_TEXT, 18)};
            border-color: {COLOR_BORDER};
            color: {COLOR_TEXT};
        }}
        QPushButton#PetMenuItem:hover, QPushButton#PetMenuGroup:hover {{
            background: {rgba(COLOR_TEXT, 20)};
            border-color: transparent;
            color: {COLOR_TEXT};
        }}
        QPushButton#PetMenuItem:pressed, QPushButton#PetMenuGroup:pressed {{
            background: {rgba(COLOR_TEXT, 34)};
        }}
        QPushButton#PetMenuItem:disabled {{
            color: {rgba(COLOR_MUTED, 135)};
            background: transparent;
        }}
        QPushButton#PetMenuItem[danger="true"] {{
            color: {COLOR_ERR};
        }}
        QPushButton#PetMenuItem[danger="true"]:hover {{
            background: {rgba(COLOR_ERR, 40)};
            border-color: {rgba(COLOR_ERR, 120)};
            color: {COLOR_ERR};
        }}
        QFrame#PetMenuSeparator {{
            background: {COLOR_BORDER};
            border: none;
            max-height: 1px;
            min-height: 1px;
        }}
        QScrollArea#PetMenuScroll {{
            background: transparent;
            border: none;
        }}
        QScrollArea#PetMenuScroll > QWidget > QWidget {{
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {rgba(COLOR_MUTED, 110)};
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """


    DIALOG_STYLE = f"""
        QDialog {{
            background: {COLOR_BG};
            color: {COLOR_TEXT};
            font-family: {FONT_FAMILY};
            font-size: 14px;
        }}
        QFrame#SizeDialogCard,
        QFrame#TimelineCard {{
            background: {_theme.GRADIENT_PAPER};
            border: 1px solid {COLOR_BORDER};
            border-top-color: {seam_highlight(80)};
            border-radius: {RADIUS_MEDIUM}px;
        }}
        QFrame#TurnCard {{
            background: {_theme.GRADIENT_RAISED};
            border: 1px solid {COLOR_BORDER};
            border-left: 3px solid {rgba(COLOR_ACCENT, 110)};
            border-radius: 12px;
        }}
        QLabel {{
            color: {COLOR_TEXT};
            background: transparent;
            border: none;
        }}
        QLabel#PageTitle {{
            color: {COLOR_TEXT};
            font-family: {DISPLAY_FONT_FAMILY};
            font-size: 20px;
            font-weight: 700;
        }}
        QLabel#HelperText {{
            color: {COLOR_MUTED};
            font-size: 12px;
        }}
        QLabel#FieldLabel {{
            color: {COLOR_SECONDARY};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel#TurnMeta {{
            color: {COLOR_MUTED};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel#TurnPreview {{
            color: {COLOR_TEXT};
            font-size: 13px;
        }}
        QLabel#TurnUser {{
            color: {COLOR_SECONDARY};
            font-size: 12px;
        }}
        QLabel#ScaleValue {{
            color: {COLOR_ACCENT};
            font-size: 26px;
            font-weight: 700;
        }}
        QPlainTextEdit,
        QTextEdit {{
            background: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 10px 12px;
            font-size: 13px;
            selection-background-color: {rgba(COLOR_ACCENT, 200)};
            selection-color: {PALETTE['on_primary']};
        }}
        QPlainTextEdit:hover,
        QTextEdit:hover {{
            border-color: {COLOR_FOCUS};
        }}
        QPlainTextEdit:focus,
        QTextEdit:focus {{
            border: 2px solid {COLOR_FOCUS};
            padding: 9px 11px;
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
            background: {rgba(COLOR_BORDER_STRONG, 170)};
            border-radius: 4px;
            min-height: 28px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {COLOR_MUTED};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QPushButton {{
            min-height: 44px;
            background: {_theme.BUTTON_SECONDARY_BG};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 8px 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {_theme.BUTTON_SECONDARY_BG_HOVER};
            border-color: {COLOR_FOCUS};
        }}
        QPushButton:pressed {{
            background: {_theme.BUTTON_SECONDARY_BG_PRESSED};
        }}
        QPushButton:focus {{
            border: 2px solid {COLOR_FOCUS};
            padding: 7px 17px;
        }}
        QPushButton:disabled {{
            background: {rgba(COLOR_ELEVATED, 150)};
            color: {rgba(COLOR_MUTED, 120)};
            border-color: {rgba(COLOR_BORDER, 150)};
        }}
        QPushButton#PrimaryButton {{
            background: {_theme.GRADIENT_PRIMARY};
            color: {PALETTE['on_primary']};
            border-color: {COLOR_ACCENT};
            font-weight: 700;
        }}
        QPushButton#PrimaryButton:hover {{
            background: {_theme.GRADIENT_PRIMARY_HOVER};
            border-color: {PALETTE['primary_hover']};
        }}
        QPushButton#PrimaryButton:pressed {{
            background: {COLOR_ACCENT};
        }}
        QPushButton#GhostButton {{
            background: transparent;
            border-color: transparent;
            color: {COLOR_SECONDARY};
        }}
        QPushButton#GhostButton:hover {{
            background: {rgba(PALETTE['accent'], 26)};
            color: {COLOR_TEXT};
            border-color: {rgba(PALETTE['accent'], 60)};
        }}
        QPushButton#GhostButton:pressed {{
            background: {rgba(PALETTE['accent'], 44)};
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {COLOR_INPUT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {_theme.GRADIENT_PROGRESS};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {COLOR_TEXT};
            border: 3px solid {COLOR_ACCENT};
            width: 20px;
            margin: -8px 0;
            border-radius: 11px;
        }}
        QSlider::handle:horizontal:hover {{
            border-color: {COLOR_FOCUS};
        }}
        QSlider::handle:horizontal:pressed {{
            background: {PALETTE['primary_hover']};
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
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #2C2C2C, stop:0.42 {COLOR_CARD}, stop:1 #181818);
            border: 1px solid {COLOR_BORDER_STRONG};
            border-top-color: {seam_highlight(100)};
            border-radius: {RADIUS_LARGE}px;
        }}
        QFrame#SectionCard {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #2A2A2A, stop:1 #222222);
            border: 1px solid {COLOR_BORDER};
            border-left: 3px solid {rgba(COLOR_ACCENT, 100)};
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
            font-weight: 700;
        }}
        QLabel#ConsentBody {{
            color: {COLOR_SECONDARY};
            font-size: 13px;
        }}
        QLabel#FieldLabel {{
            color: {COLOR_SECONDARY};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel#HelperText {{
            color: {COLOR_MUTED};
            font-size: 11px;
        }}
        QLabel#SelectionSummary {{
            color: {COLOR_SECONDARY};
            font-size: 12px;
            padding: 7px 9px;
            background: {COLOR_INPUT};
            border: 1px solid {COLOR_BORDER};
            border-radius: {RADIUS_SMALL}px;
        }}
        QLabel#ConsentValidation {{
            color: {COLOR_ERR};
            font-size: 12px;
            font-weight: 600;
            padding: 5px 8px;
            background: {rgba(COLOR_ERR, 26)};
            border: 1px solid {rgba(COLOR_ERR, 90)};
            border-radius: {RADIUS_SMALL}px;
        }}
        QLabel#ConsentCountdown {{
            color: {COLOR_WARN};
            font-size: 12px;
            font-weight: 600;
            padding: 6px 10px;
            background: {rgba(COLOR_WARN, 24)};
            border: 1px solid {rgba(COLOR_WARN, 85)};
            border-radius: {RADIUS_SMALL}px;
        }}
        QComboBox {{
            min-height: 42px;
            color: {COLOR_TEXT};
            background: {COLOR_INPUT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 0 12px;
            selection-background-color: {rgba(COLOR_ACCENT, 200)};
            selection-color: {PALETTE['on_primary']};
        }}
        QComboBox:hover {{
            border-color: {COLOR_FOCUS};
        }}
        QComboBox:focus {{
            border: 2px solid {COLOR_FOCUS};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 32px;
            background: {rgba(COLOR_ELEVATED, 200)};
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
            border-radius: {RADIUS_SMALL}px;
            selection-color: {COLOR_TEXT};
            selection-background-color: {rgba(COLOR_ACCENT, 70)};
            padding: 4px;
            outline: 0;
        }}
        QPushButton {{
            min-width: 112px;
            min-height: 44px;
            padding: 0 16px;
            color: {COLOR_TEXT};
            background: {_theme.BUTTON_SECONDARY_BG};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {_theme.BUTTON_SECONDARY_BG_HOVER};
            border-color: {COLOR_FOCUS};
        }}
        QPushButton:focus {{
            border: 2px solid {COLOR_FOCUS};
        }}
        QPushButton#SelectRegionButton,
        QPushButton#RefreshWindowsButton {{
            color: {COLOR_TEXT};
            background: {rgba(PALETTE['accent'], 26)};
            border-color: {COLOR_BORDER_STRONG};
        }}
        QPushButton#SelectRegionButton:hover,
        QPushButton#RefreshWindowsButton:hover {{
            background: {rgba(PALETTE['accent'], 52)};
            border-color: {COLOR_FOCUS};
        }}
        QPushButton#RefreshWindowsButton {{
            min-width: 72px;
            padding-left: 10px;
            padding-right: 10px;
        }}
        QPushButton#AllowUploadButton {{
            font-family: {DISPLAY_FONT_FAMILY};
            font-size: 14px;
            font-weight: 700;
            color: {PALETTE['on_primary']};
            background: {_theme.GRADIENT_PRIMARY};
            border-color: {COLOR_ACCENT};
        }}
        QPushButton#AllowUploadButton:hover {{
            background: {_theme.GRADIENT_PRIMARY_HOVER};
            border-color: {PALETTE['primary_hover']};
        }}
        QPushButton#CancelUploadButton:default {{
            color: {COLOR_TEXT};
            border: 2px solid {COLOR_FOCUS};
            background: {rgba(COLOR_FOCUS, 30)};
        }}
    """


    CHAT_COMPOSER_STYLE = f"""
        QWidget#ChatComposerRoot {{
            color: {COLOR_TEXT};
            font-family: {FONT_FAMILY};
            background: transparent;
        }}
        QFrame#ChatComposer {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba({_theme.rgb_triplet(PALETTE['paper_top'])}, 252),
                stop:0.45 rgba({_theme.rgb_triplet(PALETTE['surface'])}, 251),
                stop:1 rgba({_theme.rgb_triplet(PALETTE['paper_bottom'])}, 251));
            border: 1px solid {COLOR_BORDER_STRONG};
            border-top-color: {seam_highlight(100)};
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
            font-weight: 600;
        }}
        QLineEdit {{
            min-height: 44px;
            background: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 0 14px;
            font-size: 14px;
            selection-background-color: {rgba(COLOR_ACCENT, 200)};
            selection-color: {PALETTE['on_primary']};
        }}
        QLineEdit:hover {{
            border-color: {COLOR_FOCUS};
        }}
        QLineEdit:focus {{
            border: 2px solid {COLOR_FOCUS};
            padding: 0 13px;
        }}
        QPushButton {{
            min-height: 44px;
            min-width: 44px;
            background: {_theme.BUTTON_SECONDARY_BG};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 0 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {_theme.BUTTON_SECONDARY_BG_HOVER};
            border-color: {COLOR_FOCUS};
        }}
        QPushButton:pressed {{
            background: {_theme.BUTTON_SECONDARY_BG_PRESSED};
        }}
        QPushButton:focus {{
            border: 2px solid {COLOR_FOCUS};
        }}
        QPushButton#SendButton {{
            min-width: 80px;
            font-family: {DISPLAY_FONT_FAMILY};
            font-size: 15px;
            font-weight: 700;
            background: {_theme.GRADIENT_PRIMARY};
            color: {PALETTE['on_primary']};
            border-color: {COLOR_ACCENT};
        }}
        QPushButton#SendButton:hover {{
            background: {_theme.GRADIENT_PRIMARY_HOVER};
            border-color: {PALETTE['primary_hover']};
        }}
        QPushButton#SendButton:pressed {{
            background: {COLOR_ACCENT};
        }}
        QPushButton#SendButton:disabled {{
            background: {rgba(COLOR_ELEVATED, 150)};
            color: {rgba(COLOR_MUTED, 120)};
            border-color: {rgba(COLOR_BORDER, 150)};
        }}
        QPushButton#ComposerCloseButton {{
            background: transparent;
            color: {COLOR_MUTED};
            border-color: transparent;
            padding: 0;
            font-size: 18px;
        }}
        QPushButton#ComposerCloseButton:hover {{
            background: {rgba(COLOR_ERR, 40)};
            color: {COLOR_ERR};
            border-color: {rgba(COLOR_ERR, 110)};
        }}
        QWidget#AttachArea {{
            background: transparent;
        }}
        QScrollArea#AttachChipRow,
        QScrollArea#AttachChipRow > QWidget > QWidget {{
            background: transparent;
            border: none;
        }}
        QScrollArea#AttachChipRow QScrollBar:horizontal {{
            height: 8px;
            background: transparent;
            margin: 0;
        }}
        QScrollArea#AttachChipRow QScrollBar::handle:horizontal {{
            background: {rgba(COLOR_MUTED, 90)};
            border-radius: 4px;
            min-width: 24px;
        }}
        QScrollArea#AttachChipRow QScrollBar::add-line:horizontal,
        QScrollArea#AttachChipRow QScrollBar::sub-line:horizontal {{
            width: 0;
            height: 0;
        }}
        QFrame#AttachChip {{
            background: {rgba(COLOR_ELEVATED, 120)};
            border: 1px solid {COLOR_BORDER};
            border-radius: {RADIUS_SMALL}px;
        }}
        QLabel#ChipName {{
            color: {COLOR_TEXT};
            font-size: 12px;
            background: transparent;
            border: none;
        }}
        QLabel#ChipTokenHint {{
            color: {COLOR_MUTED};
            font-size: 11px;
            background: transparent;
            border: none;
        }}
        QPushButton#ChipRemove {{
            background: transparent;
            color: {COLOR_MUTED};
            border-color: transparent;
            padding: 0;
            font-size: 15px;
            border-radius: {RADIUS_SMALL}px;
        }}
        QPushButton#ChipRemove:hover {{
            background: {rgba(COLOR_ERR, 40)};
            color: {COLOR_ERR};
        }}
        QPushButton#VoiceButton {{
            background: {rgba(COLOR_ACCENT, 40)};
            color: {COLOR_ACCENT};
            border-color: {rgba(COLOR_ACCENT, 120)};
            border-width: 1px;
            border-style: solid;
            padding: 0;
            font-size: 16px;
            border-radius: 4px;
        }}
        QPushButton#VoiceButton:hover {{
            background: {rgba(COLOR_ACCENT, 30)};
            color: {COLOR_ACCENT};
        }}
        QPushButton#VoiceButtonRecording {{
            background: {rgba(COLOR_ERR, 60)};
            color: {COLOR_ERR};
            border-color: {rgba(COLOR_ERR, 130)};
            border-width: 2px;
            border-style: solid;
            padding: 0;
            font-size: 14px;
            border-radius: 4px;
        }}
        QPushButton#VoiceButtonRecordingBright {{
            background: {rgba(COLOR_ERR, 30)};
            color: {COLOR_ERR};
            border-color: {rgba(COLOR_ERR, 60)};
            border-width: 2px;
            border-style: solid;
            padding: 0;
            font-size: 14px;
            border-radius: 4px;
        }}
        QPushButton#VoiceButtonProcessing {{
            background: {rgba(COLOR_ACCENT_2, 50)};
            color: {COLOR_MUTED};
            border-color: {rgba(COLOR_ACCENT_2, 80)};
            border-width: 1px;
            border-style: solid;
            padding: 0;
            font-size: 14px;
            border-radius: 4px;
        }}
        QPushButton#VoiceButtonError {{
            background: {rgba(COLOR_ERR, 40)};
            color: {COLOR_ERR};
            border-color: {rgba(COLOR_ERR, 100)};
            border-width: 1px;
            border-style: solid;
            padding: 0;
            font-size: 14px;
            border-radius: 4px;
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
            font-family: {DISPLAY_FONT_FAMILY};
            font-size: 15px;
            font-weight: 400;
        }}
        QScrollArea#DialogueScroll,
        QScrollArea#DialogueScroll > QWidget > QWidget {{
            background: transparent;
            border: none;
        }}
        QScrollArea#DialogueScroll QScrollBar:vertical {{
            width: 8px;
            margin: 5px 3px;
            background: transparent;
        }}
        QScrollArea#DialogueScroll QScrollBar::handle:vertical {{
            min-height: 28px;
            border-radius: 4px;
            background: {rgba(COLOR_ACCENT, 165)};
        }}
        QScrollArea#DialogueScroll QScrollBar::handle:vertical:hover {{
            background: {rgba(PALETTE['primary_hover'], 210)};
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
            color: {PALETTE['accent']};
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
            color: {COLOR_ACCENT};
            font-size: 18px;
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
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba({_theme.rgb_triplet(PALETTE['paper_top'])}, 222),
                stop:0.45 rgba({_theme.rgb_triplet(PALETTE['surface'])}, 218),
                stop:1 rgba({_theme.rgb_triplet(PALETTE['paper_bottom'])}, 218));
            border: 1px solid {COLOR_BORDER};
            border-top-color: {seam_highlight(80)};
            border-left: 3px solid {rgba(COLOR_ACCENT, 110)};
            border-radius: {RADIUS_MEDIUM}px;
        }}
        QPushButton#PanelCloseButton {{
            min-width: 64px;
            min-height: 44px;
            background: {_theme.BUTTON_SECONDARY_BG};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER_STRONG};
            border-radius: 12px;
            font-weight: 600;
        }}
        QPushButton#PanelCloseButton:hover {{
            background: {rgba(COLOR_ERR, 45)};
            color: {COLOR_ERR};
            border-color: {rgba(COLOR_ERR, 140)};
        }}
        QPushButton#PanelCloseButton:focus {{
            border: 2px solid {COLOR_FOCUS};
        }}
        QProgressBar {{
            min-height: 22px;
            background: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
            text-align: center;
            font-size: 12px;
            font-weight: 700;
        }}
        QProgressBar::chunk {{
            background: {_theme.GRADIENT_PROGRESS};
            border-radius: 7px;
        }}
    """


    SPLASH_STYLE = f"""
        QWidget#SplashRoot {{
            color: {COLOR_TEXT};
            font-family: {FONT_FAMILY};
            background: transparent;
        }}
        QFrame#SplashCard {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #2C2C2C, stop:0.42 {COLOR_CARD}, stop:1 {COLOR_BG});
            border: 1px solid {COLOR_BORDER};
            border-top-color: {seam_highlight(95)};
            border-radius: {RADIUS_LARGE}px;
        }}
        QLabel {{
            background: transparent;
            border: none;
        }}
        QLabel#SplashMark {{
            background: {_theme.GRADIENT_PRIMARY};
            color: {PALETTE['on_primary']};
            border-radius: 18px;
            font-size: 18px;
            font-weight: 700;
        }}
        QLabel#SplashTitle {{
            color: {COLOR_TEXT};
            font-family: {DISPLAY_FONT_FAMILY};
            font-size: 26px;
            font-weight: 700;
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
            background: {COLOR_INPUT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 5px;
        }}
        QProgressBar::chunk {{
            background: {_theme.GRADIENT_PROGRESS};
            border-radius: 4px;
        }}
    """

    NAMED_STYLES.update(
        {
            "MENU_STYLE": MENU_STYLE,
            "PET_MENU_WINDOW_STYLE": PET_MENU_WINDOW_STYLE,
            "DIALOG_STYLE": DIALOG_STYLE,
            "CONSENT_DIALOG_STYLE": CONSENT_DIALOG_STYLE,
            "CHAT_COMPOSER_STYLE": CHAT_COMPOSER_STYLE,
            "DIALOGUE_STYLE": DIALOGUE_STYLE,
            "STATUS_PANEL_STYLE": STATUS_PANEL_STYLE,
            "SPLASH_STYLE": SPLASH_STYLE,
        }
    )


# 样式模块注册表：apply_named_style() 按名解析，主题切换时自动重放。
NAMED_STYLES: dict[str, str] = {}
rebuild_styles()
