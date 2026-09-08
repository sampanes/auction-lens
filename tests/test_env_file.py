"""Ignored environment settings remain exact and survive interrupted updates."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from auction_lens.env_file import load_env_file, write_settings
from support import temporary_directory


class EnvironmentSettingWriterTests(unittest.TestCase):
    def test_written_values_round_trip_without_changing_whitespace_or_quotes(self):
        exact = '  phrase with "quotes", \\slashes\\, and = signs  '
        with temporary_directory() as directory:
            env_file = directory / ".env"
            env_file.write_text("# kept\nSECRET=old\n", encoding="utf-8")
            write_settings(env_file, {"SECRET": exact})
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_file)
                loaded = os.environ["SECRET"]
            written = env_file.read_text(encoding="utf-8")
        self.assertEqual(loaded, exact)
        self.assertIn("# kept", written)
        self.assertIn("SECRET='", written)

    def test_legacy_double_quoted_backslashes_are_not_reinterpreted(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            env_file.write_text(r'SECRET="one\\two\"three"' + "\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_file)
                loaded = os.environ["SECRET"]
        self.assertEqual(loaded, r"one\\two\"three")

    def test_a_failed_atomic_replace_leaves_the_previous_file_complete(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            original = "SETTING=before\n"
            env_file.write_text(original, encoding="utf-8")
            with patch("auction_lens.env_file.os.replace", side_effect=OSError("stopped")):
                with self.assertRaisesRegex(OSError, "stopped"):
                    write_settings(env_file, {"SETTING": "after"})
            self.assertEqual(env_file.read_text(encoding="utf-8"), original)
            self.assertFalse(list(directory.glob(".env*.tmp")))

    def test_multiline_values_are_refused_before_the_file_is_touched(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            original = "SETTING=before\n"
            env_file.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "single-line"):
                write_settings(env_file, {"SETTING": "one\ntwo"})
            self.assertEqual(env_file.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
