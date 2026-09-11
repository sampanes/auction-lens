"""The command line, exercised the way a scheduler would call it."""

from __future__ import annotations

import io
import json
import os
import tomllib
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from getpass import GetPassWarning
from unittest.mock import patch

from auction_lens import __version__
from auction_lens.acquisition import SearchCapture
from auction_lens.cli import build_parser, console, main
from auction_lens.config import load_config
from auction_lens.env_file import load_env_file
from auction_lens.ingest import load_listings
from auction_lens.models import InterestRef, Verdict, WatchedItem
from auction_lens.storage import Database, ObservationStore, WatchlistStore
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


def _enable_email(config) -> None:
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace(
            "[reports.email]\nenabled = false",
            "[reports.email]\nenabled = true",
        ),
        encoding="utf-8",
    )


def _enable_webhook(config) -> None:
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace(
            "[reports.webhook]\nenabled = false",
            "[reports.webhook]\nenabled = true",
        ),
        encoding="utf-8",
    )


def _config_copy(directory, *, email_enabled=False, webhook_enabled=False):
    config = directory / "config.toml"
    config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    if email_enabled:
        _enable_email(config)
    if webhook_enabled:
        _enable_webhook(config)
    return config


def _ready_config(directory, *, email_enabled=False, development=False):
    config = _config_copy(directory, email_enabled=email_enabled)
    text = config.read_text(encoding="utf-8")
    text = text.replace("authorization_confirmed = false", "authorization_confirmed = true")
    text = text.replace(
        "[provider.acquisition]\n",
        '[provider.acquisition]\nsearch_url_template = '
        '"https://auctions.example.invalid/search?query={query}"\n',
    )
    if development:
        text = text.replace(
            "[provider.acquisition]\n",
            '[provider.acquisition]\nrun_mode = "development"\n',
        )
    config.write_text(text, encoding="utf-8")
    return config


def _loaded_values(env_file) -> dict[str, str]:
    with patch.dict(os.environ, {}, clear=True):
        load_env_file(env_file)
        return dict(os.environ)


class RunCommandTests(unittest.TestCase):
    def test_run_prints_a_report_and_creates_the_database(self):
        with temporary_directory() as directory:
            database = directory / "observations.sqlite3"
            output = run_cli(self._run_argv(directory, database))
            self.assertTrue(database.exists())
        self.assertIn("Auction Lens found", output)

    def test_email_is_refused_when_the_configuration_disables_it(self):
        with temporary_directory() as directory:
            database = directory / "observations.sqlite3"
            argv = self._run_argv(directory, database) + [
                "--delivery-ledger",
                str(directory / "deliveries.sqlite3"),
                "--email",
            ]
            with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                run_cli(argv)
            self.assertFalse(database.exists())

    def test_missing_mail_settings_are_refused_before_state_is_created(self):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            database = directory / "observations.sqlite3"
            argv = self._run_argv(directory, database)
            argv[argv.index(str(EXAMPLE_CONFIG))] = str(config)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "missing email environment"):
                    run_cli(
                        argv
                        + [
                            "--delivery-ledger",
                            str(directory / "deliveries.sqlite3"),
                            "--email",
                        ]
                    )
            self.assertFalse(database.exists())

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

    def test_a_finite_interest_retires_and_reopens_through_the_real_commands(self):
        with temporary_directory() as directory:
            database = directory / "observations.sqlite3"
            watchlist = directory / "watchlist.json"
            argv = self._run_argv(directory, database)

            first = run_cli(argv)
            self.assertIn("matches use interest 'soundbar'", first)

            assigned = run_cli(
                [
                    "watch",
                    "--watchlist",
                    str(watchlist),
                    "--key",
                    "nellis/synthetic-001",
                    "--verdict",
                    "won",
                    "--fulfills",
                    "soundbar",
                ]
            )
            retired = run_cli(argv)

            self.assertIn("Fulfills: soundbar", assigned)
            self.assertIn("soundbar: 1/1 fulfilled; retired", retired)
            self.assertNotIn("matches use interest 'soundbar'", retired)

            run_cli(
                [
                    "watch",
                    "--watchlist",
                    str(watchlist),
                    "--key",
                    "nellis/synthetic-001",
                    "--verdict",
                    "passed",
                    "--clear-fulfillments",
                ]
            )
            reopened = run_cli(argv)

        self.assertIn("soundbar: 0/1 fulfilled; 1 remaining", reopened)
        self.assertIn("matches use interest 'soundbar'", reopened)

    def _run_argv(self, directory, database) -> list[str]:
        return [
            "run",
            "--input",
            str(SYNTHETIC_LISTINGS),
            "--config",
            str(EXAMPLE_CONFIG),
            "--database",
            str(database),
            "--watchlist",
            str(directory / "watchlist.json"),
            "--env-file",
            str(directory / "absent.env"),
        ]


class DeliveryLedgerCommandTests(unittest.TestCase):
    """The CLI records accepted destinations, not merely attempted reports."""

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_email")
    def test_first_email_sends_and_an_unchanged_run_is_suppressed(
        self, send_email, email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, ledger = self._run_argv(directory, config)

            first = run_cli([*argv, "--email"])
            second = run_cli([*argv, "--email"])

            self.assertTrue(ledger.exists())

        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(email_destination.call_count, 2)
        self.assertIn("Emailed", first)
        self.assertIn("Email report is up to date", second)
        self.assertIn("already delivered", second)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_email")
    def test_an_empty_first_report_establishes_one_quiet_baseline(
        self, send_email, _email_destination
    ):
        with temporary_directory() as directory:
            empty = directory / "empty.json"
            empty.write_text('{"listings": []}', encoding="utf-8")
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config, input_path=empty)

            first = run_cli([*argv, "--email"])
            second = run_cli([*argv, "--email"])

        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(send_email.call_args.args[0], [])
        self.assertIn("Emailed 0 match", first)
        self.assertIn("outcome summary is unchanged", second)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch(
        "auction_lens.cli.commands.send_email",
        side_effect=(OSError("synthetic SMTP failure"), None),
    )
    def test_failed_email_records_nothing_and_the_retry_sends(
        self, send_email, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config)

            with self.assertRaisesRegex(
                RuntimeError, "no receipt was saved.*retry may repeat"
            ):
                run_cli([*argv, "--email"])
            retry = run_cli([*argv, "--email"])

        self.assertEqual(send_email.call_count, 2)
        self.assertIn("Emailed", retry)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch(
        "auction_lens.cli.commands.send_email",
        side_effect=OSError(
            "synthetic transport leaked recipient@example.invalid and secret-token"
        ),
    )
    def test_transport_errors_do_not_copy_destination_details_into_logs(
        self, _send_email, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config)

            with self.assertRaises(RuntimeError) as failed:
                run_cli([*argv, "--email"])

        message = str(failed.exception)
        self.assertIn("a retry may repeat it", message)
        self.assertIn("Check SMTP settings and connectivity", message)
        self.assertIn("[OSError]", message)
        self.assertNotIn("recipient@example.invalid", message)
        self.assertNotIn("secret-token", message)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.follow_candidates", side_effect=(OSError(), 0))
    @patch("auction_lens.cli.commands.send_email")
    def test_acceptance_without_a_saved_receipt_warns_that_retry_can_repeat(
        self, send_email, _follow_candidates, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config)

            with self.assertRaisesRegex(RuntimeError, "accepted.*retry may repeat"):
                run_cli([*argv, "--email"])
            run_cli([*argv, "--email"])

        self.assertEqual(send_email.call_count, 2)

    @patch("auction_lens.cli.commands.webhook_destination", return_value="b" * 64)
    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch(
        "auction_lens.cli.commands.send_webhook",
        side_effect=(OSError("synthetic webhook failure"), None),
    )
    @patch("auction_lens.cli.commands.send_email")
    def test_each_destination_retries_independently(
        self,
        send_email,
        send_webhook,
        _email_destination,
        _webhook_destination,
    ):
        with temporary_directory() as directory:
            config = _config_copy(
                directory,
                email_enabled=True,
                webhook_enabled=True,
            )
            argv, _ledger = self._run_argv(directory, config)

            with self.assertRaisesRegex(
                RuntimeError, "webhook delivery did not finish cleanly"
            ):
                run_cli([*argv, "--email", "--webhook"])
            retry = run_cli([*argv, "--email", "--webhook"])

        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(send_webhook.call_count, 2)
        self.assertIn("Email report is up to date", retry)
        self.assertIn("Posted", retry)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_email")
    def test_a_price_change_is_delivered_again_with_delivery_relative_context(
        self, send_email, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            input_path = directory / "listings.json"
            payload = json.loads(SYNTHETIC_LISTINGS.read_text(encoding="utf-8"))
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            argv, _ledger = self._run_argv(directory, config, input_path=input_path)

            run_cli([*argv, "--email"])
            payload["listings"][0]["current_bid"] = "19.00"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            run_cli([*argv, "--email"])

        self.assertEqual(send_email.call_count, 2)
        resent = send_email.call_args.args[0]
        self.assertEqual(
            {candidate.listing.listing_id for candidate in resent},
            {"synthetic-001"},
        )
        self.assertTrue(all(candidate.change.price_changed for candidate in resent))
        self.assertTrue(
            all(candidate.change.previous_bid == Decimal("18") for candidate in resent)
        )

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_email")
    def test_changed_interest_progress_sends_even_when_every_listing_is_unchanged(
        self, send_email, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config)
            watchlist = directory / "watchlist.json"

            run_cli([*argv, "--email"])
            run_cli(
                [
                    "watch",
                    "--watchlist",
                    str(watchlist),
                    "--key",
                    "nellis/synthetic-001",
                    "--verdict",
                    "won",
                    "--fulfills",
                    "soundbar",
                ]
            )
            run_cli([*argv, "--email"])

        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(send_email.call_args.args[0], [])
        progress = send_email.call_args.args[5]
        soundbar = next(item for item in progress if item.interest.interest_id == "soundbar")
        self.assertTrue(soundbar.is_retired)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_email")
    def test_repeat_delivery_forces_an_unchanged_email(
        self, send_email, _email_destination
    ):
        with temporary_directory() as directory:
            config = _config_copy(directory, email_enabled=True)
            argv, _ledger = self._run_argv(directory, config)

            run_cli([*argv, "--email"])
            repeated = run_cli([*argv, "--email", "--repeat-delivery"])

        self.assertEqual(send_email.call_count, 2)
        self.assertTrue(send_email.call_args.args[-1].repeated)
        self.assertIn("Emailed", repeated)

    def test_repeat_delivery_requires_a_destination_before_creating_state(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            argv, ledger = self._run_argv(directory, config)
            database = directory / "observations.sqlite3"

            with self.assertRaisesRegex(
                ValueError,
                "--repeat-delivery requires --email or --webhook",
            ):
                run_cli([*argv, "--repeat-delivery"])

            self.assertFalse(database.exists())
            self.assertFalse(ledger.exists())

    @staticmethod
    def _run_argv(directory, config, *, input_path=SYNTHETIC_LISTINGS):
        ledger = directory / "deliveries.sqlite3"
        return (
            [
                "run",
                "--input",
                str(input_path),
                "--config",
                str(config),
                "--database",
                str(directory / "observations.sqlite3"),
                "--watchlist",
                str(directory / "watchlist.json"),
                "--env-file",
                str(directory / "absent.env"),
                "--delivery-ledger",
                str(ledger),
            ],
            ledger,
        )


class WatchCommandTests(unittest.TestCase):
    def test_the_report_s_watch_key_identifies_a_lot_in_one_argument(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(self._matched(audio))

            run_cli(
                [
                    "watch",
                    "--watchlist",
                    str(path),
                    "--key",
                    "synthetic/one",
                    "--verdict",
                    "won",
                    "--fulfills",
                    "audio",
                ]
            )
            self.assertEqual(store.get("synthetic", "one").verdict, Verdict.WON)

    def test_a_watch_key_cannot_be_mixed_with_the_two_part_spelling(self):
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            run_cli(
                [
                    "watch",
                    "--key",
                    "synthetic/one",
                    "--source",
                    "synthetic",
                ]
            )

    def test_an_incomplete_watch_identity_says_both_valid_spellings(self):
        with self.assertRaisesRegex(ValueError, r"--key SOURCE/LISTING-ID"):
            run_cli(["watch", "--source", "synthetic"])

    def test_fulfillment_flags_default_to_no_change_and_repeat(self):
        parser = build_parser()
        base = ["watch", "--source", "synthetic", "--listing-id", "one"]

        untouched = parser.parse_args(base)
        assigned = parser.parse_args(
            [*base, "--fulfills", "audio", "--fulfills", "DIY stock"]
        )

        self.assertIsNone(untouched.fulfills)
        self.assertFalse(untouched.clear_fulfillments)
        self.assertEqual(assigned.fulfills, ["audio", "DIY stock"])
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        *base,
                        "--fulfills",
                        "audio",
                        "--clear-fulfillments",
                    ]
                )

    def test_a_fulfillment_replaces_the_old_allocation(self):
        audio = InterestRef("audio", "Home audio")
        diy = InterestRef("diy", "DIY stock")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(
                replace(
                    self._matched(audio, diy),
                    verdict=Verdict.WON,
                    fulfilled_interests=(audio,),
                )
            )

            message = run_cli(
                self._argv(path, "--fulfills", "DIY stock")
            )
            item = store.get("synthetic", "one")

        self.assertEqual(item.fulfilled_interests, (diy,))
        self.assertIn("Fulfills: DIY stock", message)

    def test_multiple_fulfillments_accept_names_and_stable_ids(self):
        audio = InterestRef("audio", "Home audio")
        diy = InterestRef("diy", "DIY stock")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(self._matched(audio, diy))

            run_cli(
                self._argv(
                    path,
                    "--verdict",
                    "won",
                    "--fulfills",
                    "HOME AUDIO",
                    "--fulfills",
                    "DiY",
                )
            )
            item = store.get("synthetic", "one")

        self.assertEqual(item.verdict, Verdict.WON)
        self.assertEqual(item.fulfilled_interests, (audio, diy))
        self.assertTrue(item.fulfillment_reviewed)

    def test_clear_removes_the_allocation_without_changing_other_answers(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(
                replace(
                    self._matched(audio),
                    verdict=Verdict.WON,
                    fulfilled_interests=(audio,),
                    my_estimate=Decimal("80"),
                    note="bring a friend",
                )
            )

            message = run_cli(self._argv(path, "--clear-fulfillments"))
            item = store.get("synthetic", "one")

        self.assertEqual(item.fulfilled_interests, ())
        self.assertTrue(item.fulfillment_reviewed)
        self.assertEqual(item.verdict, Verdict.WON)
        self.assertEqual(item.my_estimate, Decimal("80"))
        self.assertEqual(item.note, "bring a friend")
        self.assertIn("Fulfillment reviewed: fulfills none", message)

    def test_an_unknown_fulfillment_lists_the_recorded_choices(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(replace(self._matched(audio), verdict=Verdict.WON))

            with self.assertRaisesRegex(
                ValueError,
                r"not a recorded match.*audio \(Home audio\)",
            ):
                run_cli(self._argv(path, "--fulfills", "bicycles"))

    def test_an_ambiguous_display_name_lists_the_stable_ids(self):
        speakers = InterestRef("speakers", "Audio")
        receivers = InterestRef("receivers", "Audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(
                replace(self._matched(speakers, receivers), verdict=Verdict.WON)
            )

            with self.assertRaisesRegex(
                ValueError,
                r"ambiguous.*speakers \(Audio\), receivers \(Audio\)",
            ):
                run_cli(self._argv(path, "--fulfills", "audio"))

            run_cli(self._argv(path, "--fulfills", "receivers"))
            item = store.get("synthetic", "one")

        self.assertEqual(item.fulfilled_interests, (receivers,))

    def test_assigning_a_fulfillment_requires_a_won_verdict(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(self._matched(audio))

            with self.assertRaisesRegex(ValueError, r"add --verdict won"):
                run_cli(self._argv(path, "--fulfills", "audio"))

            self.assertEqual(store.get("synthetic", "one").verdict, Verdict.WATCHING)

    def test_a_non_won_verdict_keeps_the_allocation_as_inactive_history(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(
                replace(
                    self._matched(audio),
                    verdict=Verdict.WON,
                    fulfilled_interests=(audio,),
                )
            )

            message = run_cli(self._argv(path, "--verdict", "passed"))
            item = store.get("synthetic", "one")

        self.assertEqual(item.verdict, Verdict.PASSED)
        self.assertEqual(item.fulfilled_interests, (audio,))
        self.assertIn("inactive until the verdict is won", message)

    def test_only_fields_named_on_the_command_line_change(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            original = replace(
                self._matched(audio),
                verdict=Verdict.WON,
                fulfilled_interests=(audio,),
                my_estimate=Decimal("80"),
                note="old note",
            )
            store.save(original)

            run_cli(self._argv(path, "--note", "new note"))
            updated = store.get("synthetic", "one")

        self.assertEqual(updated, replace(original, note="new note"))

    def test_a_won_match_without_an_allocation_is_confirmed_as_unreviewed(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(self._matched(audio))

            message = run_cli(self._argv(path, "--verdict", "won"))

        self.assertIn("Fulfillment unreviewed", message)
        self.assertIn("--fulfills INTEREST", message)
        self.assertIn("--clear-fulfillments", message)

    def test_drop_refuses_fulfillment_flags_instead_of_silently_ignoring_them(self):
        with self.assertRaisesRegex(ValueError, "drop cannot be combined"):
            run_cli(
                [
                    "watch",
                    "--key",
                    "synthetic/one",
                    "--verdict",
                    "drop",
                    "--clear-fulfillments",
                ]
            )

    def test_drop_preserves_a_lot_that_has_a_fulfillment(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            original = replace(
                self._matched(audio),
                verdict=Verdict.WON,
                fulfilled_interests=(audio,),
            )
            store.save(original)
            before = path.read_bytes()

            with self.assertRaisesRegex(
                ValueError,
                r"--clear-fulfillments.*reopens the interest.*--verdict drop",
            ):
                run_cli(self._argv(path, "--verdict", "drop"))

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.get("synthetic", "one"), original)

    def test_clearing_a_fulfillment_allows_an_explicit_followup_drop(self):
        audio = InterestRef("audio", "Home audio")
        with temporary_directory() as directory:
            store, path = self._store(directory)
            store.save(
                replace(
                    self._matched(audio),
                    verdict=Verdict.WON,
                    fulfilled_interests=(audio,),
                )
            )

            clear_message = run_cli(self._argv(path, "--clear-fulfillments"))
            drop_message = run_cli(self._argv(path, "--verdict", "drop"))

            self.assertIn("Fulfillment reviewed: fulfills none", clear_message)
            self.assertEqual(drop_message, "Stopped following.\n")
            self.assertIsNone(store.get("synthetic", "one"))

    def test_drop_keeps_the_existing_behavior_for_safe_and_absent_lots(self):
        with temporary_directory() as directory:
            store, path = self._store(directory)

            absent_message = run_cli(self._argv(path, "--verdict", "drop"))
            self.assertEqual(absent_message, "That lot was not being followed.\n")
            self.assertFalse(path.exists())

            store.save(self._matched())
            safe_message = run_cli(self._argv(path, "--verdict", "drop"))
            self.assertEqual(safe_message, "Stopped following.\n")
            self.assertIsNone(store.get("synthetic", "one"))

    @staticmethod
    def _matched(*references: InterestRef) -> WatchedItem:
        return WatchedItem(
            source="synthetic",
            listing_id="one",
            title="Example lot",
            matched_interests=tuple(references),
        )

    @staticmethod
    def _store(directory):
        path = directory / "watchlist.json"
        return WatchlistStore(path), path

    @staticmethod
    def _argv(path, *changes: str) -> list[str]:
        return [
            "watch",
            "--watchlist",
            str(path),
            "--source",
            "synthetic",
            "--listing-id",
            "one",
            *changes,
        ]


class WatchlistCommandTests(unittest.TestCase):
    def test_email_says_so_when_the_configuration_has_it_switched_off(self):
        # --config now defaults, so the remaining guard is the one that matters:
        # a configuration that never enabled email cannot send any.
        with temporary_directory() as directory:
            watchlist = directory / "watchlist.json"
            WatchlistStore(watchlist).save(
                WatchedItem(source="synthetic", listing_id="one")
            )
            with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                run_cli(
                    [
                        "watchlist",
                        "--watchlist",
                        str(watchlist),
                        "--config",
                        str(EXAMPLE_CONFIG),
                        "--env-file",
                        str(directory / "absent.env"),
                        "--delivery-ledger",
                        str(directory / "deliveries.sqlite3"),
                        "--email",
                    ]
                )

    @patch("auction_lens.cli.commands.send_watchlist_email")
    def test_an_empty_selection_does_not_send_an_email(self, send_watchlist_email):
        with temporary_directory() as directory:
            message = run_cli(
                [
                    "watchlist",
                    "--watchlist",
                    str(directory / "watchlist.json"),
                    "--verdict",
                    "hunting",
                    "--config",
                    str(EXAMPLE_CONFIG),
                    "--env-file",
                    str(directory / "absent.env"),
                    "--delivery-ledger",
                    str(directory / "deliveries.sqlite3"),
                    "--email",
                ]
            )
        send_watchlist_email.assert_not_called()
        self.assertIn("No selected lots; no email sent", message)

    @patch("auction_lens.cli.commands.email_destination", return_value="0" * 64)
    @patch("auction_lens.cli.commands.send_watchlist_email")
    def test_email_sends_only_the_selected_verdict(
        self, send_watchlist_email, email_destination
    ):
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
                    "--delivery-ledger",
                    str(directory / "deliveries.sqlite3"),
                    "--email",
                ]
            )

        selected = send_watchlist_email.call_args.args[0]
        self.assertEqual([item.listing_id for item in selected], ["1"])
        email_destination.assert_called_once()

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    @patch("auction_lens.cli.commands.send_watchlist_email")
    def test_watchlist_email_suppresses_unchanged_items_and_repeat_overrides_it(
        self, send_watchlist_email, _email_destination
    ):
        with temporary_directory() as directory:
            watchlist = directory / "watchlist.json"
            WatchlistStore(watchlist).save(
                WatchedItem(source="synthetic", listing_id="one", verdict="watching")
            )
            config = _config_copy(directory, email_enabled=True)
            argv = [
                "watchlist",
                "--watchlist",
                str(watchlist),
                "--config",
                str(config),
                "--env-file",
                str(directory / "absent.env"),
                "--delivery-ledger",
                str(directory / "deliveries.sqlite3"),
                "--email",
            ]

            first = run_cli(argv)
            unchanged = run_cli(argv)
            repeated = run_cli([*argv, "--repeat-delivery"])

        self.assertEqual(send_watchlist_email.call_count, 2)
        self.assertIn("Emailed 1 selected lot", first)
        self.assertIn("Watchlist email is up to date", unchanged)
        self.assertIn("already delivered", unchanged)
        self.assertIn("Emailed 1 selected lot", repeated)
        self.assertTrue(send_watchlist_email.call_args.args[-1].repeated)


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


class ProfileCommandTests(unittest.TestCase):
    def test_it_reads_only_the_selected_config(self):
        with temporary_directory() as directory:
            config = _config_copy(directory)
            before = config.read_bytes()
            files_before = tuple(directory.iterdir())
            with patch("auction_lens.cli.load_env_file") as load_env:
                with patch("auction_lens.cli.commands.discover_searches") as discover:
                    with patch("auction_lens.cli.commands.fetch_authorized_page") as fetch:
                        message = run_cli(["profile", "--config", str(config)])

            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(tuple(directory.iterdir()), files_before)
        load_env.assert_not_called()
        discover.assert_not_called()
        fetch.assert_not_called()
        self.assertIn("Auction Lens profile", message)
        self.assertIn("TEMPORARY CIRCUMSTANCES", message)


class MailSetupTests(unittest.TestCase):
    """Filling in the five mail variables without ever showing the password."""

    ANSWERS = [
        "smtp.gmail.com",
        "sender@example.invalid",
        "",
        "",
        "  abcd efgh ijkl mnop  ",
    ]

    def _setup_email(self, directory, answers=None, *, email_enabled=True):
        typed = list(self.ANSWERS if answers is None else answers)
        config, env_file = directory / "local.toml", directory / ".env"
        run_cli(["setup", "--config", str(config), "--env-file", str(env_file)])
        if email_enabled:
            _enable_email(config)
        with patch("builtins.input", side_effect=lambda _: typed.pop(0)):
            with patch("auction_lens.cli.commands.getpass", side_effect=lambda _: typed.pop(0)):
                with patch("sys.stdin.isatty", return_value=True):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "setup",
                                "--config",
                                str(config),
                                "--env-file",
                                str(env_file),
                                "--email",
                            ]
                        )
        return config, env_file, buffer.getvalue(), exit_code

    def test_the_answers_reach_the_env_file(self):
        with temporary_directory() as directory:
            _, env_file, _, exit_code = self._setup_email(directory)
            values = _loaded_values(env_file)
        self.assertEqual(exit_code, 0)
        self.assertEqual(values["AUCTION_LENS_SMTP_HOST"], "smtp.gmail.com")
        self.assertEqual(values["AUCTION_LENS_SMTP_USERNAME"], "sender@example.invalid")
        self.assertEqual(values["AUCTION_LENS_EMAIL_FROM"], "sender@example.invalid")

    def test_an_empty_recipient_means_send_it_to_yourself(self):
        with temporary_directory() as directory:
            _, env_file, _, _ = self._setup_email(directory)
            values = _loaded_values(env_file)
        self.assertEqual(values["AUCTION_LENS_EMAIL_TO"], "sender@example.invalid")

    def test_the_spaces_a_provider_displays_are_not_part_of_the_password(self):
        # Google shows an app password in four groups of four and people paste
        # it exactly as shown, which is the usual way this step fails.
        with temporary_directory() as directory:
            _, env_file, _, _ = self._setup_email(directory)
            values = _loaded_values(env_file)
        self.assertEqual(values["AUCTION_LENS_SMTP_PASSWORD"], "abcdefghijklmnop")

    def test_the_password_is_never_printed(self):
        with temporary_directory() as directory:
            _, _, message, _ = self._setup_email(directory)
        self.assertNotIn("abcd", message)
        self.assertIn("neither printed nor logged", message)

    def test_the_comments_in_the_env_file_survive(self):
        with temporary_directory() as directory:
            _, env_file, _, _ = self._setup_email(directory)
            written = env_file.read_text(encoding="utf-8")
        self.assertIn("never commit it", written)
        self.assertIn("AUCTION_LENS_HTTP_USER_AGENT=", written)

    def test_another_hosts_username_sender_and_password_are_preserved(self):
        answers = [
            "mail.example.invalid",
            "account-id",
            "sender@example.invalid",
            "recipient@example.invalid",
            '  secret phrase "as typed"  ',
        ]
        with temporary_directory() as directory:
            _, env_file, _, _ = self._setup_email(directory, answers)
            values = _loaded_values(env_file)
        self.assertEqual(values["AUCTION_LENS_SMTP_HOST"], "mail.example.invalid")
        self.assertEqual(values["AUCTION_LENS_SMTP_USERNAME"], "account-id")
        self.assertEqual(values["AUCTION_LENS_EMAIL_FROM"], "sender@example.invalid")
        self.assertEqual(values["AUCTION_LENS_EMAIL_TO"], "recipient@example.invalid")
        self.assertEqual(values["AUCTION_LENS_SMTP_PASSWORD"], answers[-1])

    def test_configured_environment_variable_names_are_used(self):
        with temporary_directory() as directory:
            config, env_file = directory / "local.toml", directory / ".env"
            run_cli(["setup", "--config", str(config), "--env-file", str(env_file)])
            _enable_email(config)
            replacements = {
                "AUCTION_LENS_SMTP_HOST": "CUSTOM_MAIL_HOST",
                "AUCTION_LENS_SMTP_USERNAME": "CUSTOM_MAIL_USER",
                "AUCTION_LENS_SMTP_PASSWORD": "CUSTOM_MAIL_SECRET",
                "AUCTION_LENS_EMAIL_FROM": "CUSTOM_MAIL_FROM",
                "AUCTION_LENS_EMAIL_TO": "CUSTOM_MAIL_TO",
            }
            text = config.read_text(encoding="utf-8")
            for old, new in replacements.items():
                text = text.replace(f'"{old}"', f'"{new}"')
            config.write_text(text, encoding="utf-8")
            _, env_file, _, _ = self._setup_existing_email(config, env_file)
            values = _loaded_values(env_file)
        self.assertEqual(values["CUSTOM_MAIL_HOST"], "smtp.gmail.com")
        self.assertEqual(values["CUSTOM_MAIL_USER"], "sender@example.invalid")
        self.assertEqual(values["CUSTOM_MAIL_SECRET"], "abcdefghijklmnop")

    def _setup_existing_email(self, config, env_file):
        typed = list(self.ANSWERS)
        with patch("builtins.input", side_effect=lambda _: typed.pop(0)):
            with patch("auction_lens.cli.commands.getpass", side_effect=lambda _: typed.pop(0)):
                with patch("sys.stdin.isatty", return_value=True):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = main(
                            ["setup", "--config", str(config), "--env-file", str(env_file),
                             "--email"]
                        )
        return config, env_file, buffer.getvalue(), exit_code

    def test_it_says_which_line_still_has_to_be_changed_by_hand(self):
        # The example config ships with email off, and this reads the switch
        # with the real loader rather than guessing at the file.
        with temporary_directory() as directory:
            _, _, message, exit_code = self._setup_email(directory, email_enabled=False)
        self.assertIn("enabled = false", message)
        self.assertIn("delivery is not ready", message)
        self.assertIn("doctor --email", message)

    def test_saving_the_settings_is_not_reported_as_a_failure(self):
        # setup did what it was asked. Whether delivery is switched on is a
        # separate question, and doctor is the command that answers it for
        # automation; failing here would make a normal first run look broken.
        with temporary_directory() as directory:
            _, _, _, exit_code = self._setup_email(directory, email_enabled=False)
        self.assertEqual(exit_code, 0)

    def test_noninteractive_setup_fails_before_writing_a_password(self):
        with temporary_directory() as directory:
            config, env_file = directory / "local.toml", directory / ".env"
            run_cli(["setup", "--config", str(config), "--env-file", str(env_file)])
            before = env_file.read_text(encoding="utf-8")
            answers = iter(self.ANSWERS[:-1])
            errors = io.StringIO()
            with patch("builtins.input", side_effect=lambda _: next(answers)):
                with patch("sys.stdin.isatty", return_value=False):
                    with patch("auction_lens.cli.commands.getpass") as hidden_prompt:
                        with redirect_stdout(io.StringIO()):
                            with redirect_stderr(errors):
                                exit_code = console(
                                    ["setup", "--config", str(config), "--env-file",
                                     str(env_file), "--email"]
                                )
            hidden_prompt.assert_not_called()
            self.assertEqual(env_file.read_text(encoding="utf-8"), before)
        self.assertEqual(exit_code, 2)
        self.assertIn("interactive terminal", errors.getvalue())

    def test_an_echoing_password_fallback_is_refused_before_input(self):
        with temporary_directory() as directory:
            config, env_file = directory / "local.toml", directory / ".env"
            run_cli(["setup", "--config", str(config), "--env-file", str(env_file)])
            before = env_file.read_text(encoding="utf-8")
            answers = iter(self.ANSWERS[:-1])
            errors = io.StringIO()

            def insecure_prompt(_):
                warnings.warn("would echo", GetPassWarning, stacklevel=2)
                self.fail("warning should stop the fallback before it reads input")

            with patch("builtins.input", side_effect=lambda _: next(answers)):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("auction_lens.cli.commands.getpass", insecure_prompt):
                        with redirect_stdout(io.StringIO()):
                            with redirect_stderr(errors):
                                exit_code = console(
                                    [
                                        "setup",
                                        "--config",
                                        str(config),
                                        "--env-file",
                                        str(env_file),
                                        "--email",
                                    ]
                                )
            self.assertEqual(env_file.read_text(encoding="utf-8"), before)
        self.assertEqual(exit_code, 2)
        self.assertIn("secure password input is unavailable", errors.getvalue())


class DailyCommandTests(unittest.TestCase):
    @patch("auction_lens.cli.commands._discover")
    def test_report_preflight_happens_before_discovery(self, discover):
        with temporary_directory() as directory:
            output = directory / "listings.json"
            with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                run_cli(
                    [
                        "daily",
                        "--config",
                        str(EXAMPLE_CONFIG),
                        "--output",
                        str(output),
                        "--env-file",
                        str(directory / "absent.env"),
                        "--delivery-ledger",
                        str(directory / "deliveries.sqlite3"),
                        "--email",
                    ]
                )
        discover.assert_not_called()
        self.assertFalse(output.exists())

    @patch("auction_lens.cli.commands._run", return_value=0)
    def test_a_retired_fallback_cannot_use_the_search_cap_before_an_active_one(
        self, _run
    ):
        with temporary_directory() as directory:
            config = self._daily_config(directory)
            watchlist = directory / "watchlist.json"
            WatchlistStore(watchlist).save(self._won_soundbar())
            asked = self._daily_searches(directory, config, watchlist)

        # Soundbar is the first configured interest and has two phrases, but
        # its one wanted item is already won. With a cap of one, the active
        # monitor interest must receive the request.
        self.assertEqual(asked, ["monitor"])
        _run.assert_called_once()

    @patch("auction_lens.cli.commands._run", return_value=0)
    def test_configured_searches_remain_an_operator_override(self, _run):
        with temporary_directory() as directory:
            config = self._daily_config(directory, searches=("operator phrase",))
            watchlist = directory / "watchlist.json"
            WatchlistStore(watchlist).save(self._won_soundbar())
            asked = self._daily_searches(directory, config, watchlist)

        self.assertEqual(asked, ["operator phrase"])
        _run.assert_called_once()

    @patch("auction_lens.cli.commands._run", return_value=0)
    def test_command_line_searches_remain_an_operator_override(self, _run):
        with temporary_directory() as directory:
            config = self._daily_config(directory, searches=("configured phrase",))
            watchlist = directory / "watchlist.json"
            WatchlistStore(watchlist).save(self._won_soundbar())
            asked = self._daily_searches(
                directory,
                config,
                watchlist,
                requested=("one-off phrase",),
            )

        self.assertEqual(asked, ["one-off phrase"])
        _run.assert_called_once()

    @patch("auction_lens.cli.commands.discover_searches")
    def test_all_satisfied_interests_make_a_quiet_report_without_a_request(
        self, discover_searches
    ):
        with temporary_directory() as directory:
            config = self._daily_config(directory)
            text = config.read_text(encoding="utf-8").replace(
                'name = "monitor"\n',
                'name = "monitor"\nid = "monitor"\nwanted = 1\n',
            )
            config.write_text(text, encoding="utf-8")
            watchlist = directory / "watchlist.json"
            store = WatchlistStore(watchlist)
            store.save(self._won_interest("soundbar"))
            store.save(self._won_interest("monitor"))

            message = run_cli(
                [
                    "daily",
                    "--config",
                    str(config),
                    "--output",
                    str(directory / "listings.json"),
                    "--database",
                    str(directory / "observations.sqlite3"),
                    "--watchlist",
                    str(watchlist),
                    "--env-file",
                    str(directory / "absent.env"),
                ]
            )
            payload = json.loads((directory / "listings.json").read_text("utf-8"))

        discover_searches.assert_not_called()
        self.assertEqual(payload, {"listings": []})
        self.assertIn("no provider request was needed", message)
        self.assertIn("soundbar: 1/1 fulfilled; retired", message)
        self.assertIn("monitor: 1/1 fulfilled; retired", message)

    @staticmethod
    def _daily_config(directory, *, searches: tuple[str, ...] = ()):
        config = _config_copy(directory)
        acquisition = "max_searches_per_run = 1\n"
        if searches:
            values = ", ".join(json.dumps(term) for term in searches)
            acquisition += f"searches = [{values}]\n"
        text = config.read_text(encoding="utf-8").replace(
            "[provider.acquisition]\n",
            f"[provider.acquisition]\n{acquisition}",
            1,
        )
        config.write_text(text, encoding="utf-8")
        return config

    @staticmethod
    def _won_soundbar() -> WatchedItem:
        return DailyCommandTests._won_interest("soundbar")

    @staticmethod
    def _won_interest(name: str) -> WatchedItem:
        interest = InterestRef(name, name)
        return WatchedItem(
            source="nellis",
            listing_id=f"synthetic-{name}-win",
            title=f"Synthetic {name}",
            matched_interests=(interest,),
            fulfilled_interests=(interest,),
            verdict=Verdict.WON,
        )

    @staticmethod
    def _daily_searches(
        directory,
        config,
        watchlist,
        *,
        requested: tuple[str, ...] = (),
    ) -> list[str]:
        asked: list[str] = []

        def fake_discovery(_provider, acquisition, terms):
            asked.extend(list(terms)[: acquisition.max_searches_per_run])
            return ()

        argv = [
            "daily",
            "--config",
            str(config),
            "--output",
            str(directory / "listings.json"),
            "--database",
            str(directory / "observations.sqlite3"),
            "--watchlist",
            str(watchlist),
            "--env-file",
            str(directory / "absent.env"),
        ]
        for term in requested:
            argv.extend(("--search", term))
        with patch("auction_lens.cli.commands.discover_searches", fake_discovery):
            run_cli(argv)
        return asked


class DoctorCommandTests(unittest.TestCase):
    def test_it_checks_a_ready_daily_run_without_network_access(self):
        with temporary_directory() as directory:
            config = _ready_config(directory, email_enabled=True)
            env_file = directory / ".env"
            env_file.write_text(
                "AUCTION_LENS_HTTP_USER_AGENT=AuctionLens/1.0 "
                "(contact: operator@auction-lens.dev)\n"
                "AUCTION_LENS_SMTP_HOST=smtp.example.invalid\n"
                "AUCTION_LENS_SMTP_USERNAME=synthetic-user\n"
                "AUCTION_LENS_SMTP_PASSWORD=synthetic-secret\n"
                "AUCTION_LENS_EMAIL_FROM=sender@example.invalid\n"
                "AUCTION_LENS_EMAIL_TO=recipient@example.invalid\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                message = run_cli(
                    [
                        "doctor",
                        "--config",
                        str(config),
                        "--env-file",
                        str(env_file),
                        "--delivery-ledger",
                        str(directory / "deliveries.sqlite3"),
                        "--email",
                    ]
                )
        self.assertIn("discovery is configured and authorized", message)
        self.assertIn("email is enabled", message)
        self.assertIn("no network requests were made", message)

    @patch("auction_lens.cli.commands.email_destination", return_value="a" * 64)
    def test_it_validates_a_missing_ledger_without_creating_it(
        self, email_destination
    ):
        with temporary_directory() as directory:
            config = _ready_config(directory, email_enabled=True)
            env_file = directory / ".env"
            env_file.write_text(
                "AUCTION_LENS_HTTP_USER_AGENT=AuctionLens/1.0 "
                "(contact: operator@auction-lens.dev)\n",
                encoding="utf-8",
            )
            ledger = directory / "missing" / "deliveries.sqlite3"

            with patch.dict(os.environ, {}, clear=True):
                message = run_cli(
                    [
                        "doctor",
                        "--config",
                        str(config),
                        "--env-file",
                        str(env_file),
                        "--delivery-ledger",
                        str(ledger),
                        "--email",
                    ]
                )

            self.assertFalse(ledger.exists())
            self.assertFalse(ledger.parent.exists())

        email_destination.assert_called_once()
        self.assertIn("delivery receipts are ready", message)
        self.assertIn("no network requests were made", message)

    def test_it_refuses_development_pacing_for_an_unattended_run(self):
        with temporary_directory() as directory:
            config = _ready_config(directory, development=True)
            with self.assertRaisesRegex(RuntimeError, 'run_mode = "production"'):
                run_cli(
                    ["doctor", "--config", str(config), "--env-file",
                     str(directory / "absent.env")]
                )

    def test_the_windows_runner_preflights_email_without_requiring_a_webhook(self):
        script = (ROOT / "scripts" / "run-daily.cmd").read_text(encoding="utf-8")
        self.assertIn('"%AUCTION_LENS%" doctor --email', script)
        self.assertIn('"%AUCTION_LENS%" daily --email', script)
        self.assertNotIn("daily --email --webhook", script)


class DiscoverCommandTests(unittest.TestCase):
    def test_a_discovered_lot_is_dated_when_its_page_was_fetched(self):
        # Not when the command ran: a page revalidated from the cache was
        # downloaded by an earlier run, and its prices are that run's prices.
        fetched_at = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
        with temporary_directory() as directory:
            page = directory / "search.html"
            page.write_text(
                (ROOT / "fixtures" / "nellis" / "search-page.html").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            capture = SearchCapture(
                term="soundbar",
                url="https://example.invalid/search?query=soundbar",
                path=page,
                reused_cache=True,
                fetched_at=fetched_at,
            )
            output = directory / "listings.json"
            with patch(
                "auction_lens.cli.commands.discover_searches", return_value=[capture]
            ):
                run_cli(
                    ["discover", "--config", str(EXAMPLE_CONFIG), "--output",
                     str(output), "--search", "soundbar"]
                )
            rows = json.loads(output.read_text(encoding="utf-8"))["listings"]

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["observed_at"], fetched_at.isoformat())


class SoldCommandTests(unittest.TestCase):
    """Reading closing prices back out of whatever the database already holds."""

    CLOSES_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)

    def _database_with_one_closed_lot(self, directory, *, seen_before_close):
        database = Database.at(directory / "observations.sqlite3")
        database.initialize()
        listing = replace(
            load_listings(SYNTHETIC_LISTINGS)[0],
            ends_at=self.CLOSES_AT,
            current_bid=Decimal("159.00"),
            observed_at=self.CLOSES_AT - seen_before_close,
        )
        ObservationStore(database).observe(listing)
        return database.path, listing

    def test_a_closed_lot_is_reported_as_a_floor(self):
        with temporary_directory() as directory:
            path, listing = self._database_with_one_closed_lot(
                directory, seen_before_close=timedelta(minutes=4)
            )
            message = run_cli(
                ["sold", "--config", str(EXAMPLE_CONFIG), "--database", str(path)]
            )

        self.assertIn("at least $159", message)
        self.assertIn("seen 4m before it closed", message)
        self.assertIn(f"{listing.source}/{listing.listing_id}", message)

    def test_a_search_term_narrows_the_answer_to_one_kind_of_thing(self):
        with temporary_directory() as directory:
            path, _ = self._database_with_one_closed_lot(
                directory, seen_before_close=timedelta(minutes=4)
            )
            message = run_cli(
                ["sold", "--config", str(EXAMPLE_CONFIG), "--database", str(path),
                 "--match", "chainsaw"]
            )

        self.assertIn("No lot in the database has closed yet", message)

    def test_a_reading_too_early_to_trust_is_withheld_with_its_reason(self):
        with temporary_directory() as directory:
            path, _ = self._database_with_one_closed_lot(
                directory, seen_before_close=timedelta(hours=6)
            )
            message = run_cli(
                ["sold", "--config", str(EXAMPLE_CONFIG), "--database", str(path)]
            )

        self.assertNotIn("at least $159", message)
        self.assertIn("no price worth quoting", message)

    def test_the_window_can_be_widened_to_take_in_an_earlier_reading(self):
        with temporary_directory() as directory:
            path, _ = self._database_with_one_closed_lot(
                directory, seen_before_close=timedelta(hours=6)
            )
            message = run_cli(
                ["sold", "--config", str(EXAMPLE_CONFIG), "--database", str(path),
                 "--within-minutes", "420"]
            )

        self.assertIn("at least $159", message)


class DefaultsTests(unittest.TestCase):
    def test_the_program_reports_its_release_version(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            build_parser().parse_args(["--version"])

        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue(), "auction-lens 0.6.0\n")

    def test_package_metadata_and_runtime_use_the_same_version(self):
        with (ROOT / "pyproject.toml").open("rb") as project_file:
            declared = tomllib.load(project_file)["project"]["version"]

        self.assertEqual(declared, __version__)

    def test_the_configuration_flag_can_be_left_off(self):
        # One door: the file a person edits is where every command looks.
        for command in (
            "profile", "doctor", "run", "fetch", "discover", "pull", "daily",
            "watchlist", "sold",
        ):
            with self.subTest(command=command):
                self.assertEqual(_parsed_default(command, "config"), "config/local.toml")

    def test_every_delivered_report_shares_one_private_receipt_ledger(self):
        for command in ("doctor", "daily", "run", "watchlist"):
            with self.subTest(command=command):
                self.assertEqual(
                    _parsed_default(command, "delivery_ledger"),
                    "private/deliveries.sqlite3",
                )

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

    def test_a_pulled_lot_is_dated_when_its_page_was_saved(self):
        # A page read back weeks later still describes the prices of the day it
        # was fetched, and the closing-price view depends on saying so.
        saved_at = "2026-09-10T03:00:00+00:00"
        with temporary_directory() as directory:
            page = directory / "search.html"
            page.write_text(
                (ROOT / "fixtures" / "nellis" / "search-page.html").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            (directory / "search.html.metadata.json").write_text(
                json.dumps(
                    {
                        "source_url": "https://example.invalid/search?query=soundbar",
                        "fetched_at": saved_at,
                    }
                ),
                encoding="utf-8",
            )
            output = directory / "listings.json"
            run_cli(
                ["pull", "--config", str(EXAMPLE_CONFIG), "--input", str(directory),
                 "--output", str(output)]
            )
            rows = json.loads(output.read_text(encoding="utf-8"))["listings"]

        self.assertEqual([row["observed_at"] for row in rows], [saved_at, saved_at])

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
