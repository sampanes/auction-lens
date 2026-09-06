"""The command line, exercised the way a scheduler would call it."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from auction_lens.cli import console, main
from auction_lens.models import WatchedItem
from auction_lens.storage import WatchlistStore
from support import EXAMPLE_CONFIG, ROOT, SYNTHETIC_LISTINGS, temporary_directory

NELLIS_PRODUCT_PAGE = ROOT / "fixtures" / "nellis" / "product-page.html"


def run_cli(argv: list[str]) -> str:
    """Run one command and return what it printed, failing on a non-zero exit."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(argv)
    if exit_code != 0:
        raise AssertionError(f"command exited with {exit_code}")
    return buffer.getvalue()


class RunCommandTests(unittest.TestCase):
    def test_run_prints_a_report_and_creates_the_database(self):
        with temporary_directory() as directory:
            database = directory / "observations.sqlite3"
            output = run_cli(self._run_argv(directory, database))
            self.assertTrue(database.exists())
        self.assertIn("Auction Lens found", output)

    def test_email_is_refused_when_the_configuration_disables_it(self):
        with temporary_directory() as directory:
            argv = self._run_argv(directory, directory / "observations.sqlite3") + ["--email"]
            with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                run_cli(argv)

    def test_console_reports_bad_input_without_a_traceback(self):
        with temporary_directory() as directory:
            bad_input = directory / "bad-listings.json"
            bad_input.write_text(json.dumps([42]), encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                exit_code = console(
                    [
                        "run",
                        "--input",
                        str(bad_input),
                        "--config",
                        str(EXAMPLE_CONFIG),
                        "--database",
                        str(directory / "observations.sqlite3"),
                        "--env-file",
                        str(directory / "absent.env"),
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertIn("auction-lens: error:", errors.getvalue())
        self.assertIn("listing 1 must be an object", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def _run_argv(self, directory, database) -> list[str]:
        return [
            "run",
            "--input",
            str(SYNTHETIC_LISTINGS),
            "--config",
            str(EXAMPLE_CONFIG),
            "--database",
            str(database),
            "--env-file",
            str(directory / "absent.env"),
        ]


class WatchlistCommandTests(unittest.TestCase):
    def test_email_requires_the_configuration_that_names_the_account(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(RuntimeError, "--config is required"):
                run_cli(
                    [
                        "watchlist",
                        "--watchlist",
                        str(directory / "watchlist.json"),
                        "--env-file",
                        str(directory / "absent.env"),
                        "--email",
                    ]
                )

    @patch("auction_lens.cli.commands.send_watchlist_email")
    def test_email_sends_only_the_selected_verdict(self, send_watchlist_email):
        with temporary_directory() as directory:
            watchlist = directory / "watchlist.json"
            store = WatchlistStore(watchlist)
            store.save(WatchedItem(source="nellis", listing_id="1", verdict="hunting"))
            store.save(WatchedItem(source="nellis", listing_id="2", verdict="watching"))
            config = directory / "config.toml"
            source = EXAMPLE_CONFIG.read_text(encoding="utf-8")
            config.write_text(
                source.replace(
                    '[reports.email]\nenabled = false',
                    '[reports.email]\nenabled = true',
                ),
                encoding="utf-8",
            )
            run_cli(
                [
                    "watchlist",
                    "--watchlist",
                    str(watchlist),
                    "--verdict",
                    "hunting",
                    "--config",
                    str(config),
                    "--env-file",
                    str(directory / "absent.env"),
                    "--email",
                ]
            )

        selected = send_watchlist_email.call_args.args[0]
        self.assertEqual([item.listing_id for item in selected], ["1"])


class LogisticsCommandTests(unittest.TestCase):
    def test_a_decision_is_saved_and_then_cleared(self):
        with temporary_directory() as directory:
            database = str(directory / "observations.sqlite3")
            saved = run_cli(
                self._logistics_argv(database)
                + ["--status", "feasible", "--added-cost", "25", "--note", "Handling arranged"]
            )
            cleared = run_cli(self._logistics_argv(database) + ["--status", "clear"])
        self.assertIn("saved as feasible", saved)
        self.assertIn("$25.00", saved)
        self.assertIn("cleared", cleared)

    def _logistics_argv(self, database: str) -> list[str]:
        return [
            "logistics",
            "--database",
            database,
            "--source",
            "nellis",
            "--listing-id",
            "synthetic-001",
        ]


class PullCommandTests(unittest.TestCase):
    def test_a_saved_product_page_is_ready_for_the_run_command(self):
        with temporary_directory() as directory:
            output = directory / "listings.json"
            message = run_cli(
                [
                    "pull",
                    "--config",
                    str(EXAMPLE_CONFIG),
                    "--input",
                    str(NELLIS_PRODUCT_PAGE),
                    "--output",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("Read 1 of 1 saved page(s)", message)
        self.assertEqual(payload["listings"][0]["grade"]["condition"], "Used")

    def test_a_page_the_provider_has_changed_is_named_and_the_batch_survives(self):
        with temporary_directory() as directory:
            pages = directory / "pages"
            pages.mkdir()
            (pages / "good.html").write_text(
                NELLIS_PRODUCT_PAGE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (pages / "changed.html").write_text("<html>nothing</html>", encoding="utf-8")
            output = directory / "listings.json"
            message = run_cli(
                ["pull", "--config", str(EXAMPLE_CONFIG), "--input", str(pages),
                 "--output", str(output)]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("Read 1 of 2 saved page(s)", message)
        self.assertIn("changed.html", message)
        self.assertEqual(len(payload["listings"]), 1)


if __name__ == "__main__":
    unittest.main()
