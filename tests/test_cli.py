"""The command line, exercised the way a scheduler would call it."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from auction_lens.cli import build_parser, console, main
from auction_lens.config import load_config
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
    def test_email_says_so_when_the_configuration_has_it_switched_off(self):
        # --config now defaults, so the remaining guard is the one that matters:
        # a configuration that never enabled email cannot send any.
        with temporary_directory() as directory:
            with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                run_cli(
                    [
                        "watchlist",
                        "--watchlist",
                        str(directory / "watchlist.json"),
                        "--config",
                        str(EXAMPLE_CONFIG),
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


class SetupCommandTests(unittest.TestCase):
    """The first command on a machine git could not fully equip."""

    def test_it_creates_the_two_files_a_fresh_clone_lacks(self):
        with temporary_directory() as directory:
            config, env_file = directory / "local.toml", directory / ".env"
            message = run_cli(
                ["setup", "--config", str(config), "--env-file", str(env_file)]
            )
            self.assertTrue(config.exists())
            self.assertTrue(env_file.exists())
            self.assertIn("AUCTION_LENS_HTTP_USER_AGENT", env_file.read_text(encoding="utf-8"))
            self.assertIn("contact address", message)

    def test_the_configuration_it_writes_actually_loads(self):
        with temporary_directory() as directory:
            config = directory / "local.toml"
            run_cli(["setup", "--config", str(config), "--env-file", str(directory / ".env")])
            self.assertTrue(load_config(config).interests)

    def test_running_it_again_never_overwrites_your_answers(self):
        mine = "# mine, do not clobber"
        with temporary_directory() as directory:
            config, env_file = directory / "local.toml", directory / ".env"
            config.write_text(mine, encoding="utf-8")
            env_file.write_text(mine, encoding="utf-8")
            message = run_cli(
                ["setup", "--config", str(config), "--env-file", str(env_file)]
            )
            self.assertEqual(config.read_text(encoding="utf-8"), mine)
            self.assertEqual(env_file.read_text(encoding="utf-8"), mine)
            self.assertIn("left alone", message)


class DefaultsTests(unittest.TestCase):
    def test_the_configuration_flag_can_be_left_off(self):
        # One door: the file a person edits is where every command looks.
        for command in ("run", "fetch", "discover", "pull", "daily", "watchlist"):
            with self.subTest(command=command):
                self.assertEqual(_parsed_default(command, "config"), "config/local.toml")

    def test_daily_writes_where_it_then_reads(self):
        self.assertEqual(_parsed_default("daily", "output"), "data/inbox/listings.json")


def _parsed_default(command: str, option: str):
    action = next(
        sub
        for sub in build_parser()._subparsers._group_actions[0].choices[command]._actions
        if sub.dest == option
    )
    return action.default


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

        self.assertIn("Read 1 lot(s) from 1 saved page(s)", message)
        self.assertEqual(payload["listings"][0]["grade"]["condition"], "Used")

    def test_a_saved_search_page_yields_every_lot_it_lists(self):
        # Discovery caches search pages; if only product pages could be read
        # back, a run cut short would throw away everything it had fetched.
        with temporary_directory() as directory:
            page = directory / "search.html"
            page.write_text(
                (ROOT / "fixtures" / "nellis" / "search-page.html").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (directory / "search.html.metadata.json").write_text(
                json.dumps({"source_url": "https://example.invalid/search?query=soundbar"}),
                encoding="utf-8",
            )
            output = directory / "listings.json"
            message = run_cli(
                ["pull", "--config", str(EXAMPLE_CONFIG), "--input", str(directory),
                 "--output", str(output)]
            )
            rows = json.loads(output.read_text(encoding="utf-8"))["listings"]

        self.assertIn("Read 2 lot(s) from 1 saved page(s)", message)
        self.assertEqual(rows[0]["url"], "https://example.invalid/p/Example-Sound-Bar/900000101")

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

        self.assertIn("Read 1 lot(s) from 2 saved page(s)", message)
        self.assertIn("changed.html", message)
        self.assertEqual(len(payload["listings"]), 1)


if __name__ == "__main__":
    unittest.main()
