"""The command-line vocabulary and defaults promised to operators."""

from __future__ import annotations

import io
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from auction_lens.cli import build_parser, console

COMMANDS = (
    "setup",
    "profile",
    "doctor",
    "daily",
    "run",
    "fetch",
    "discover",
    "pull",
    "logistics",
    "watch",
    "watchlist",
    "feedback",
    "sold",
)

# Exact public option names. Their implementation and source files are deliberately
# absent: a refactor may move everything behind this interface without changing it.
OPTIONS = {
    "setup": {"--help", "--config", "--env-file", "--email"},
    "profile": {"--help", "--config", "--edit", "--restore"},
    "doctor": {
        "--help",
        "--config",
        "--env-file",
        "--delivery-ledger",
        "--email",
        "--webhook",
    },
    "daily": {
        "--help",
        "--config",
        "--output",
        "--database",
        "--watchlist",
        "--env-file",
        "--delivery-ledger",
        "--repeat-delivery",
        "--search",
        "--visiting",
        "--email",
        "--webhook",
    },
    "run": {
        "--help",
        "--input",
        "--config",
        "--database",
        "--watchlist",
        "--env-file",
        "--delivery-ledger",
        "--repeat-delivery",
        "--visiting",
        "--email",
        "--webhook",
    },
    "fetch": {"--help", "--config", "--env-file"},
    "discover": {"--help", "--config", "--output", "--search", "--env-file"},
    "pull": {"--help", "--config", "--input", "--output"},
    "logistics": {
        "--help",
        "--database",
        "--key",
        "--source",
        "--listing-id",
        "--status",
        "--added-cost",
        "--note",
    },
    "watch": {
        "--help",
        "--watchlist",
        "--key",
        "--source",
        "--listing-id",
        "--verdict",
        "--estimate",
        "--note",
        "--fulfills",
        "--clear-fulfillments",
    },
    "watchlist": {
        "--help",
        "--watchlist",
        "--verdict",
        "--email",
        "--config",
        "--env-file",
        "--delivery-ledger",
        "--repeat-delivery",
    },
    "feedback": {
        "--help",
        "--key",
        "--source",
        "--listing-id",
        "--watchlist",
        "--config",
        "--feedback-file",
        "--interest",
        "--note",
        "--minimum-evidence",
        "--save",
        "--proposal-dir",
    },
    "sold": {"--help", "--database", "--within-minutes", "--match", "--limit", "--config"},
}

# Enough input to parse each command without running it. Values are intentionally
# synthetic: contract tests must never need a local configuration or private data.
MINIMUM_ARGUMENTS = {
    "setup": [],
    "profile": [],
    "doctor": [],
    "daily": [],
    "run": ["--input", "listings.json"],
    "fetch": [],
    "discover": ["--output", "listings.json"],
    "pull": ["--input", "saved-pages", "--output", "listings.json"],
    "logistics": ["--key", "example/123", "--status", "feasible"],
    "watch": ["--key", "example/123"],
    "watchlist": [],
    "feedback": ["review"],
    "sold": [],
}

PATH_DEFAULTS = {
    "setup": {"config": "config/local.toml", "env_file": ".env"},
    "profile": {"config": "config/local.toml"},
    "doctor": {
        "config": "config/local.toml",
        "env_file": ".env",
        "delivery_ledger": "private/deliveries.sqlite3",
    },
    "daily": {
        "config": "config/local.toml",
        "output": "data/inbox/listings.json",
        "database": "data/auction-lens.sqlite3",
        "watchlist": "private/watchlist.json",
        "env_file": ".env",
        "delivery_ledger": "private/deliveries.sqlite3",
    },
    "run": {
        "config": "config/local.toml",
        "database": "data/auction-lens.sqlite3",
        "watchlist": "private/watchlist.json",
        "env_file": ".env",
        "delivery_ledger": "private/deliveries.sqlite3",
    },
    "fetch": {"config": "config/local.toml", "env_file": ".env"},
    "discover": {"config": "config/local.toml", "env_file": ".env"},
    "pull": {"config": "config/local.toml"},
    "logistics": {"database": "data/auction-lens.sqlite3"},
    "watch": {"watchlist": "private/watchlist.json"},
    "watchlist": {
        "watchlist": "private/watchlist.json",
        "config": "config/local.toml",
        "env_file": ".env",
        "delivery_ledger": "private/deliveries.sqlite3",
    },
    "feedback": {
        "watchlist": "private/watchlist.json",
        "config": "config/local.toml",
        "feedback_file": "private/feedback.json",
        "proposal_dir": "private/proposals",
    },
    "sold": {"database": "data/auction-lens.sqlite3", "config": "config/local.toml"},
}


def _help_for(command: str) -> str:
    """Ask the parser for help exactly as an operator does."""
    output = io.StringIO()
    with redirect_stdout(output), unittest.TestCase().assertRaises(SystemExit) as stopped:
        build_parser().parse_args([command, "--help"])
    if stopped.exception.code != 0:
        raise AssertionError(f"{command} --help exited with {stopped.exception.code}")
    return output.getvalue()


class CommandLineContractTests(unittest.TestCase):
    """Freeze public CLI behavior while allowing its implementation to be replaced."""

    def test_top_level_help_names_the_complete_command_set(self):
        completed = subprocess.run(
            [sys.executable, "-m", "auction_lens.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        choices = re.search(r"\{([^}\n]+)\}", completed.stdout)
        self.assertIsNotNone(choices)
        self.assertEqual(tuple(choices.group(1).split(",")), COMMANDS)
        self.assertNotIn("Traceback", completed.stderr)

    def test_each_command_advertises_exactly_its_supported_options(self):
        for command, expected in OPTIONS.items():
            with self.subTest(command=command):
                help_text = _help_for(command)
                advertised = set(re.findall(r"--[a-z][a-z-]+", help_text))
                self.assertEqual(advertised, expected)
                self.assertIn(f"usage: auction-lens {command}", help_text)

    def test_default_files_are_stable_and_private_files_stay_private(self):
        parser = build_parser()
        for command, expected in PATH_DEFAULTS.items():
            with self.subTest(command=command):
                parsed = parser.parse_args([command, *MINIMUM_ARGUMENTS[command]])
                actual = {name: getattr(parsed, name) for name in expected}
                self.assertEqual(actual, expected)

    def test_commands_with_required_file_flags_reject_omissions(self):
        invalid_examples = (
            ["run"],
            ["discover"],
            ["pull", "--input", "saved-pages"],
            ["pull", "--output", "listings.json"],
            ["logistics", "--key", "example/123"],
        )
        for argv in invalid_examples:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    build_parser().parse_args(argv)
                self.assertEqual(stopped.exception.code, 2)

    def test_an_operator_error_is_concise_and_has_the_documented_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            errors = io.StringIO()
            with redirect_stderr(errors):
                exit_code = console(["profile", "--config", str(missing)])

        self.assertEqual(exit_code, 2)
        self.assertTrue(errors.getvalue().startswith("auction-lens: error:"))
        self.assertIn("missing.toml", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_missing_or_unknown_commands_are_usage_errors(self):
        for argv in ([], ["not-a-command"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    build_parser().parse_args(argv)
                self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
