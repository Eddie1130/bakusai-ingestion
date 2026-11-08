"""Prompt delivery includes an offline size estimate without altering evidence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis import cli, prompt, select, tokens


class TokenEstimateTests(unittest.TestCase):
    def test_empty_prompt_has_zero_tokens(self):
        estimate = tokens.estimate("")
        self.assertEqual((estimate.lower, estimate.upper), (0, 0))

    def test_non_ascii_is_not_treated_as_equivalent_to_ascii(self):
        ascii_text = tokens.estimate("a" * 100)
        japanese = tokens.estimate("猫" * 100)
        emoji = tokens.estimate("🐈" * 100)
        self.assertGreater(japanese.lower, ascii_text.upper)
        self.assertGreater(emoji.upper, japanese.upper)

    def test_estimate_is_explicit_about_its_limits(self):
        description = tokens.estimate("請分析 T1-123 のコメント").describe()
        self.assertIn("非模型 tokenizer 計數", description)
        self.assertIn("可能超出", description)
        self.assertIn("不含對話歷史", description)

    def test_every_prompt_type_measures_the_exact_copied_text(self):
        selected = (select.Selected("T1-123", "2026/11/08 12:00", "黒い服の子🐈"),)
        prompts = [
            prompt.build("店", "ナナ", selected),
            prompt.build_filter("店", "ナナ", selected),
            prompt.build_discovery("店", selected, features="黑衣"),
            prompt.build_discovery("店", selected),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for text in prompts:
                for clipboard_success in (True, False):
                    with self.subTest(prompt=text[:30], clipboard=clipboard_success):
                        copied = []
                        with patch.object(cli.tempfile, "tempdir", directory), patch.object(
                            tokens, "estimate", wraps=tokens.estimate
                        ) as estimate:
                            message = cli.deliver(text, lambda value: copied.append(value) or clipboard_success)
                        estimate.assert_called_once_with(text)
                        self.assertEqual(copied, [text])
                        self.assertIn("預估輸入 tokens", message)
                        self.assertIn(f"{len(text):,} 字元", message)
                        filename = message.split("副本：", 1)[1].strip()
                        self.assertEqual(Path(filename).read_text(encoding="utf-8"), text)
                        if not clipboard_success:
                            self.assertIn("無法寫入剪貼簿", message)


if __name__ == "__main__":
    unittest.main()
