"""attach-chips-above-input (b 替换式) 生产实现离屏验证。

覆盖：空态收回固定高、有附件撑高、chip 行置于输入框上方、逐个 × 移除同步本地与
宿主集合、超长文件名省略 + tooltip、溢出单行横滚、合计 token、信号发射。
"""
from __future__ import annotations

import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import (
    QApplication,
    QPushButton,
)

from meapet.agent.base import TextAttachment
from meapet.agent.text_budget import estimate_tokens
from meapet.desktop.chat_flow import PetChatFlowMixin
from meapet.desktop.chat_input import (
    CHAT_COMPOSER_HEIGHT,
    CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS,
    ChatInputBox,
)


def _att(name: str, body: str = "hello world") -> TextAttachment:
    return TextAttachment.from_bytes(name, body.encode("utf-8"))


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class AttachChipComposerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()

    def _make(self) -> ChatInputBox:
        composer = ChatInputBox()
        self.addCleanup(composer.close)
        composer._anim_timer.stop()
        composer.show()
        self.app.processEvents()
        return composer

    def _set_attachments(self, composer: ChatInputBox, atts) -> None:
        composer._text_attachments = list(atts)
        composer._refresh_attachments()
        self.app.processEvents()

    def test_empty_state_collapses_row_and_fixed_height(self) -> None:
        composer = self._make()
        self.assertFalse(composer.attach_area.isVisible())
        self.assertFalse(composer.total_hint.isVisible())
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT)
        self.assertTrue(composer.height() <= 160)

    def test_adding_attachments_grows_height_and_shows_row(self) -> None:
        composer = self._make()
        fired = []
        composer.attachment_row_changed.connect(lambda: fired.append(composer.height()))
        self._set_attachments(composer, [_att("notes.md"), _att("data.csv")])
        self.assertTrue(composer.attach_area.isVisible())
        self.assertTrue(composer.total_hint.isVisible())
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS)
        self.assertGreater(composer.height(), CHAT_COMPOSER_HEIGHT)
        self.assertEqual(fired, [CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS])

    def test_chip_row_sits_above_input_row(self) -> None:
        composer = self._make()
        self._set_attachments(composer, [_att("a.txt"), _att("b.txt")])
        area_bottom = composer.attach_area.mapTo(composer, composer.attach_area.rect().bottomLeft()).y()
        input_top = composer.input.mapTo(composer, composer.input.rect().topLeft()).y()
        self.assertLessEqual(area_bottom, input_top)

    def test_chip_count_matches_attachments(self) -> None:
        composer = self._make()
        atts = [_att(f"f{i}.txt") for i in range(3)]
        self._set_attachments(composer, atts)
        self.assertEqual(len(composer._attach_chips), 3)
        self.assertEqual(
            [a.file_name for a in composer.get_attachments()],
            [f"f{i}.txt" for i in range(3)],
        )

    def test_total_hint_matches_token_estimate(self) -> None:
        composer = self._make()
        atts = [_att("x.txt", "some text content"), _att("y.md", "more text here!!")]
        self._set_attachments(composer, atts)
        expected = sum(estimate_tokens(a.text_content) + 50 for a in atts)
        self.assertEqual(composer.total_hint.text(), f"~合计 {expected} tk")

    def test_remove_button_drops_attachment_and_reflow(self) -> None:
        composer = self._make()
        a, b = _att("first.txt"), _att("second.txt")
        self._set_attachments(composer, [a, b])
        remove = [
            btn for btn in composer._attach_chips[0].findChildren(QPushButton)
            if btn.objectName() == "ChipRemove"
        ][0]
        remove.click()
        self.app.processEvents()
        self.assertEqual(composer.get_attachments(), [b])

    def test_removing_all_collapses_back_to_empty_height(self) -> None:
        composer = self._make()
        only = _att("solo.txt")
        self._set_attachments(composer, [only])
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS)
        btns = [b for b in composer._attach_chips[0].findChildren(QPushButton)
                if b.objectName() == "ChipRemove"]
        btns[0].click()
        self.app.processEvents()
        self.assertEqual(composer.get_attachments(), [])
        self.assertFalse(composer.attach_area.isVisible())
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT)

    def test_clear_attachments_collapses(self) -> None:
        composer = self._make()
        self._set_attachments(composer, [_att("k.txt"), _att("j.txt")])
        composer.clear_attachments()
        self.app.processEvents()
        self.assertEqual(composer.get_attachments(), [])
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT)

    def test_long_filename_elided_with_full_tooltip(self) -> None:
        composer = self._make()
        long_name = "a_really_long_attachment_filename_exceeding_chip_width.md"
        self._set_attachments(composer, [_att(long_name)])
        chip = composer._attach_chips[0]
        from PyQt5.QtWidgets import QLabel
        name_label = [lbl for lbl in chip.findChildren(QLabel)
                      if lbl.objectName() == "ChipName"][0]
        self.assertNotEqual(name_label.text(), long_name)
        self.assertIn("…", name_label.text())
        self.assertTrue(name_label.toolTip().startswith(long_name))

    def test_overflow_keeps_single_row_and_activates_hscroll(self) -> None:
        composer = self._make()
        composer.show()
        self.app.processEvents()
        self._set_attachments(composer, [_att(f"file_number_{i:02d}_longish.txt") for i in range(12)])
        ys = sorted({c.mapTo(composer, c.rect().topLeft()).y() for c in composer._attach_chips})
        self.assertLessEqual(len(ys), 1)
        self.assertEqual(composer.attach_area.height(), 52)
        self.assertGreater(
            composer.chip_scroll.horizontalScrollBar().maximum(), 0,
            "12 枚固定宽 chip 应启用横向滚动",
        )
        self.assertEqual(len(composer._attach_chips), 12)
        # 总高不随文件数增长（恒定撑高）
        self.assertEqual(composer.height(), CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS)


class AttachmentRemovedHostSyncTest(unittest.TestCase):
    """宿主 _on_chat_input_attachment_removed：与 files_attached 并入对称的剔除。"""

    def _host(self, atts):
        host = types.SimpleNamespace(_text_attachments=list(atts))
        return host

    def test_removes_first_match_by_identity(self) -> None:
        a, b, c = _att("a.txt"), _att("b.txt"), _att("c.txt")
        host = self._host([a, b, c])
        PetChatFlowMixin._on_chat_input_attachment_removed(host, b)
        self.assertEqual(host._text_attachments, [a, c])

    def test_removes_by_name_hash_key_when_identity_differs(self) -> None:
        a = _att("dup.txt", "same body")
        clone = _att("dup.txt", "same body")  # 不同对象、相同 (name, sha)
        b = _att("b.txt")
        host = self._host([a, b])
        PetChatFlowMixin._on_chat_input_attachment_removed(host, clone)
        self.assertEqual(host._text_attachments, [b])

    def test_only_first_duplicate_removed(self) -> None:
        # 同名同内容但内容不同则键不同；构造两个不同内容同名
        a1 = _att("n.txt", "one")
        a2 = _att("n.txt", "two distinct body")
        host = self._host([a1, a2])
        PetChatFlowMixin._on_chat_input_attachment_removed(host, a1)
        self.assertEqual(host._text_attachments, [a2])

    def test_none_is_noop(self) -> None:
        a = _att("a.txt")
        host = self._host([a])
        PetChatFlowMixin._on_chat_input_attachment_removed(host, None)
        self.assertEqual(host._text_attachments, [a])


if __name__ == "__main__":
    unittest.main()
