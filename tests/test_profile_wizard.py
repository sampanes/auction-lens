"""The guided profile editor changes nothing until its owner says yes."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from auction_lens.cli import build_parser, console, main
from auction_lens.config import LargeItemPolicy, load_config
from auction_lens.config.editor import profile_snapshot_path
from support import EXAMPLE_CONFIG, temporary_directory

PRIVATE_MARKER = "PRIVATE-SENTINEL-DO-NOT-PRINT"


def _config_copy(directory, *, private_marker: bool = False):
    config = directory / "local.toml"
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    if private_marker:
        text = text.replace(
            'path = "fixtures/synthetic/valuations.xml"',
            'path = "fixtures/synthetic/valuations.xml"\n'
            f'private_token = "{PRIVATE_MARKER}"',
        )
    config.write_text(text, encoding="utf-8")
    return config


def _run_profile(config, answers, *, restore: bool = False):
    remaining = iter(answers)
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        value = next(remaining)
        if isinstance(value, BaseException):
            raise value
        return value

    output = io.StringIO()
    option = "--restore" if restore else "--edit"
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", side_effect=answer):
            with redirect_stdout(output):
                exit_code = main(["profile", "--config", str(config), option])
    if exit_code != 0:
        raise AssertionError(f"command exited with {exit_code}")
    return output.getvalue(), prompts


class ProfileEditorCommandTests(unittest.TestCase):
    def test_a_confirmed_edit_saves_an_exact_backup_without_runtime_reads(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()
            with patch("auction_lens.cli.load_env_file") as load_env:
                with patch("auction_lens.cli.commands.discover_searches") as discover:
                    with patch("auction_lens.cli.commands.fetch_authorized_page") as fetch:
                        with patch("auction_lens.cli.commands.load_listings") as listings:
                            with patch("auction_lens.cli.commands.Database") as database:
                                output, prompts = _run_profile(
                                    config, ["allow", "80.5", "70", "y"]
                                )
            edited = load_config(config)
            snapshot = profile_snapshot_path(config)

            self.assertEqual(snapshot.read_bytes(), original)
            self.assertNotEqual(config.read_bytes(), original)

        load_env.assert_not_called()
        discover.assert_not_called()
        fetch.assert_not_called()
        listings.assert_not_called()
        database.assert_not_called()
        self.assertEqual(edited.logistics.large_item_policy, LargeItemPolicy.ALLOW)
        self.assertEqual(edited.logistics.manual_handling_limit_lb, Decimal("80.5"))
        self.assertEqual(edited.logistics.large_dimension_threshold_in, Decimal("70"))
        self.assertEqual(len(prompts), 4)
        self.assertIn("PROPOSED PROFILE", output)
        self.assertIn("EXACT CONFIGURATION DIFF", output)
        self.assertIn("@@", output)
        self.assertIn("Saved.", output)

    def test_restore_swaps_current_and_previous_so_it_is_reversible(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()
            _run_profile(config, ["reject", "", "", "yes"])
            edited = config.read_bytes()

            output, prompts = _run_profile(config, ["yes"], restore=True)

            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(profile_snapshot_path(config).read_bytes(), edited)

        self.assertEqual(len(prompts), 1)
        self.assertIn("PROPOSED PROFILE", output)
        self.assertIn("EXACT CONFIGURATION DIFF", output)
        self.assertIn("Restored.", output)

    def test_restore_discloses_and_exchanges_lf_and_crlf_bytes(self):
        normalized = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace("\r\n", "\n")
        with temporary_directory() as directory:
            config = directory / "local.toml"
            current = normalized.replace("\n", "\r\n").encode("utf-8")
            previous = normalized.encode("utf-8")
            config.write_bytes(current)
            profile_snapshot_path(config).write_bytes(previous)

            output, _prompts = _run_profile(config, ["yes"], restore=True)

            self.assertEqual(config.read_bytes(), previous)
            self.assertEqual(profile_snapshot_path(config).read_bytes(), current)

        self.assertIn("LINE-ENDING BYTES", output)
        self.assertIn("CRLF", output)
        self.assertIn("LF", output)

    def test_restore_discloses_mixed_endings_and_a_missing_final_newline(self):
        current_text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertTrue(current_text.endswith("\n"))
        previous_text = current_text[:-1].replace("\n", "\r\n", 1)
        with temporary_directory() as directory:
            config = directory / "local.toml"
            current = current_text.encode("utf-8")
            previous = previous_text.encode("utf-8")
            config.write_bytes(current)
            profile_snapshot_path(config).write_bytes(previous)

            output, _prompts = _run_profile(config, ["yes"], restore=True)

            self.assertEqual(config.read_bytes(), previous)
            self.assertEqual(profile_snapshot_path(config).read_bytes(), current)

        self.assertIn("LINE-ENDING BYTES", output)
        self.assertIn("CRLF", output)
        self.assertIn("no terminator", output)

    def test_enter_on_all_three_questions_is_an_exact_no_op(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()

            output, prompts = _run_profile(config, ["", "", ""])

            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(profile_snapshot_path(config).exists())

        self.assertEqual(len(prompts), 3)
        self.assertTrue(output.endswith("No changes requested; no files changed.\n"))
        self.assertNotIn("PROPOSED PROFILE", output)

    def test_any_confirmation_other_than_literal_yes_or_y_cancels(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()

            output, prompts = _run_profile(
                config, ["allow", "", "", "yes please"]
            )

            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(profile_snapshot_path(config).exists())

        self.assertEqual(len(prompts), 4)
        self.assertIn("PROPOSED PROFILE", output)
        self.assertTrue(output.endswith("Cancelled; no files changed.\n"))
        self.assertNotIn("Saved.", output)

    def test_eof_or_control_c_cancels_without_creating_a_file(self):
        interruptions = (
            ("EOF at a question", [EOFError()]),
            ("Control-C at confirmation", ["allow", "", "", KeyboardInterrupt()]),
        )
        for label, answers in interruptions:
            with self.subTest(label=label):
                with temporary_directory() as directory:
                    config = _config_copy(directory)
                    original = config.read_bytes()

                    output, _prompts = _run_profile(config, answers)

                    self.assertEqual(config.read_bytes(), original)
                    self.assertFalse(profile_snapshot_path(config).exists())
                self.assertTrue(output.endswith("Cancelled; no files changed.\n"))

    def test_noninteractive_input_is_refused_before_a_prompt_or_write(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()
            errors = io.StringIO()
            with patch("sys.stdin.isatty", return_value=False):
                with patch("builtins.input") as prompt:
                    with redirect_stderr(errors):
                        exit_code = console(
                            ["profile", "--config", str(config), "--edit"]
                        )

            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(profile_snapshot_path(config).exists())

        prompt.assert_not_called()
        self.assertEqual(exit_code, 2)
        self.assertIn("interactive terminal", errors.getvalue())

    def test_a_non_toml_profile_path_is_refused_before_prompting_or_writing(self):
        with temporary_directory() as directory:
            config = directory / "prefs.conf"
            config.write_bytes(EXAMPLE_CONFIG.read_bytes())
            before = tuple(directory.iterdir())
            errors = io.StringIO()
            with patch("sys.stdin.isatty", return_value=True):
                with patch("builtins.input") as prompt:
                    with redirect_stderr(errors):
                        exit_code = console(
                            ["profile", "--config", str(config), "--edit"]
                        )

            self.assertEqual(tuple(directory.iterdir()), before)

        prompt.assert_not_called()
        self.assertEqual(exit_code, 2)
        self.assertIn("requires a .toml", errors.getvalue())

    def test_invalid_choices_and_numbers_reprompt_the_same_question(self):
        answers = [
            "maybe",
            "allow",
            "NaN",
            "-2",
            "80",
            "Infinity",
            "70",
            "no",
        ]
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()

            output, prompts = _run_profile(config, answers)

            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(profile_snapshot_path(config).exists())

        self.assertEqual(len(prompts), len(answers))
        self.assertEqual(output.count("Choose ask, allow, or reject."), 1)
        self.assertEqual(output.count("Enter a non-negative finite number."), 3)

    def test_default_removes_explicit_settings_and_uses_schema_defaults(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)

            output, _prompts = _run_profile(
                config, ["default", "default", "default", "yes"]
            )
            written = config.read_text(encoding="utf-8")
            effective = load_config(config).logistics

        self.assertNotIn('\nlarge_item_policy = "ask"', written)
        self.assertNotIn("\nmanual_handling_limit_lb = 75", written)
        self.assertNotIn("\nlarge_dimension_threshold_in = 60", written)
        self.assertEqual(effective.large_item_policy, LargeItemPolicy.ASK)
        self.assertEqual(effective.manual_handling_limit_lb, Decimal("75"))
        self.assertEqual(effective.large_dimension_threshold_in, Decimal("60"))
        self.assertIn("Type default", output)

    def test_unrelated_private_values_never_reach_the_profile_or_diff(self):
        with temporary_directory() as directory:
            config = _config_copy(directory, private_marker=True)

            output, _prompts = _run_profile(
                config, ["allow", "", "", "no"]
            )

        self.assertIn("PROPOSED PROFILE", output)
        self.assertIn("EXACT CONFIGURATION DIFF", output)
        self.assertNotIn(PRIVATE_MARKER, output)

    def test_restore_validates_the_snapshot_before_asking_for_confirmation(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()
            snapshot = profile_snapshot_path(config)
            snapshot.write_text("not valid TOML = [", encoding="utf-8")
            before_snapshot = snapshot.read_bytes()

            with patch("sys.stdin.isatty", return_value=True):
                with patch("builtins.input") as prompt:
                    with self.assertRaises(ValueError):
                        main(["profile", "--config", str(config), "--restore"])

            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(snapshot.read_bytes(), before_snapshot)
        prompt.assert_not_called()

    def test_restore_without_a_snapshot_explains_how_to_create_one(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            original = config.read_bytes()
            errors = io.StringIO()
            with patch("sys.stdin.isatty", return_value=True):
                with patch("builtins.input") as prompt:
                    with redirect_stderr(errors):
                        exit_code = console(
                            ["profile", "--config", str(config), "--restore"]
                        )

            self.assertEqual(config.read_bytes(), original)

        prompt.assert_not_called()
        self.assertEqual(exit_code, 2)
        self.assertIn("no profile snapshot to restore", errors.getvalue())
        self.assertIn("profile --edit first", errors.getvalue())

    def test_edit_and_restore_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                build_parser().parse_args(["profile", "--edit", "--restore"])

        self.assertEqual(stopped.exception.code, 2)


class SetupProfileSuggestionTests(unittest.TestCase):
    def test_setup_repeats_a_custom_config_path_without_starting_the_editor(self):
        with temporary_directory() as directory:
            config = directory / "custom folder" / "local.toml"
            env_file = directory / ".env"
            output = io.StringIO()
            with patch("auction_lens.cli.commands.edit_profile") as edit_profile:
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "setup",
                            "--config",
                            str(config),
                            "--env-file",
                            str(env_file),
                        ]
                    )

        edit_profile.assert_not_called()
        self.assertEqual(exit_code, 0)
        self.assertIn(f'profile --config "{config}" --edit', output.getvalue())

    def test_setup_does_not_suggest_backups_for_a_non_toml_custom_path(self):
        with temporary_directory() as directory:
            config = directory / "prefs.conf"
            env_file = directory / ".env"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "setup",
                        "--config",
                        str(config),
                        "--env-file",
                        str(env_file),
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("requires a configuration ending in .toml", output.getvalue())
        self.assertNotIn("profile --config", output.getvalue())


if __name__ == "__main__":
    unittest.main()
