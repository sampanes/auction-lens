"""The feedback command stays local, explicit, and separate from preferences."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from auction_lens.cli import main
from auction_lens.feedback.model import FeedbackAction
from auction_lens.feedback.store import FeedbackStore
from auction_lens.matching.progress import InterestRef
from auction_lens.watchlist.model import PriceReading, WatchedItem
from auction_lens.watchlist.store import WatchlistStore
from support import EXAMPLE_CONFIG, temporary_directory

LABELS = (
    "yes",
    "maybe",
    "no",
    "wrong-item",
    "too-expensive",
    "logistics-impossible",
)


def _run(argv: list[str]) -> str:
    """Run the real command while proving it has no reason to touch a network."""
    output = io.StringIO()
    with (
        patch("urllib.request.urlopen", side_effect=AssertionError("network used")),
        redirect_stdout(output),
    ):
        exit_code = main(argv)
    if exit_code:
        raise AssertionError(f"command exited with {exit_code}")
    return output.getvalue()


def _item(
    listing_id: str = "lot-1",
    *,
    matches: tuple[InterestRef, ...] | None = None,
    total_cost: str = "40",
) -> WatchedItem:
    return WatchedItem(
        source="example",
        listing_id=listing_id,
        inventory_id=f"item-{listing_id}",
        title=f"Synthetic item {listing_id}",
        estimated_retail=Decimal("100"),
        matched_interests=(
            (InterestRef("soundbar", "soundbar"),) if matches is None else matches
        ),
        readings=(
            PriceReading(
                scanned_at=datetime(2030, 1, 1, tzinfo=UTC),
                current_bid=Decimal(total_cost),
                total_cost=Decimal(total_cost),
                listing_id=listing_id,
            ),
        ),
    )


class FeedbackCommandTests(unittest.TestCase):
    def _files(self, directory: Path, item: WatchedItem | None = None):
        config = directory / "config.toml"
        shutil.copyfile(EXAMPLE_CONFIG, config)
        watchlist = directory / "watchlist.json"
        WatchlistStore(watchlist).save(item or _item())
        return config, watchlist, directory / "feedback.json"

    @staticmethod
    def _record_args(
        action: str, config: Path, watchlist: Path, feedback_file: Path
    ) -> list[str]:
        return [
            "feedback",
            action,
            "--key",
            "example/lot-1",
            "--config",
            str(config),
            "--watchlist",
            str(watchlist),
            "--feedback-file",
            str(feedback_file),
        ]

    def test_every_label_and_clear_append_events_without_changing_other_state(self):
        with temporary_directory() as directory:
            config, watchlist, feedback_file = self._files(directory)
            original_config = config.read_bytes()
            original_watchlist = watchlist.read_bytes()

            for label in LABELS:
                output = _run(
                    self._record_args(label, config, watchlist, feedback_file)
                    + ["--note", f"synthetic {label}"]
                )
                self.assertIn(f"Recorded {label}", output)
                self.assertIn("configuration were not changed", output)

            clear_output = _run(
                self._record_args("clear", config, watchlist, feedback_file)
            )
            store = FeedbackStore(feedback_file)
            events = store.events()

            self.assertEqual([event.label for event in events[:-1]], list(LABELS))
            self.assertEqual(events[-1].action, FeedbackAction.CLEAR)
            self.assertEqual(store.current(), ())
            self.assertIn("Cleared feedback", clear_output)
            self.assertEqual(config.read_bytes(), original_config)
            self.assertEqual(watchlist.read_bytes(), original_watchlist)

    def test_ambiguous_interest_is_refused_before_the_feedback_file_is_written(self):
        matches = (
            InterestRef("first", "First interest"),
            InterestRef("second", "Second interest"),
        )
        with temporary_directory() as directory:
            config, watchlist, feedback_file = self._files(
                directory, _item(matches=matches)
            )
            original_config = config.read_bytes()
            original_watchlist = watchlist.read_bytes()
            argv = self._record_args("yes", config, watchlist, feedback_file)

            with self.assertRaisesRegex(ValueError, "more than one interest"):
                _run(argv)
            self.assertFalse(feedback_file.exists())

            _run(argv + ["--interest", "Second interest"])
            event = FeedbackStore(feedback_file).events()[0]

            self.assertEqual(event.target.target_id, "second")
            self.assertEqual(config.read_bytes(), original_config)
            self.assertEqual(watchlist.read_bytes(), original_watchlist)

    def test_unknown_lot_and_clear_note_are_refused_without_writing(self):
        with temporary_directory() as directory:
            config, watchlist, feedback_file = self._files(directory)
            unknown = self._record_args("yes", config, watchlist, feedback_file)
            unknown[unknown.index("example/lot-1")] = "example/missing"

            with self.assertRaisesRegex(ValueError, "known watched item"):
                _run(unknown)
            with self.assertRaisesRegex(ValueError, "clear cannot be combined"):
                _run(
                    self._record_args("clear", config, watchlist, feedback_file)
                    + ["--note", "not allowed"]
                )

            self.assertFalse(feedback_file.exists())

    def test_review_is_read_only_and_rejects_record_only_flags(self):
        with temporary_directory() as directory:
            config, watchlist, feedback_file = self._files(directory)
            _run(self._record_args("yes", config, watchlist, feedback_file))
            original = {
                path: path.read_bytes() for path in (config, watchlist, feedback_file)
            }

            output = _run(
                [
                    "feedback",
                    "review",
                    "--config",
                    str(config),
                    "--feedback-file",
                    str(feedback_file),
                    "--minimum-evidence",
                    "1",
                ]
            )

            self.assertIn("Feedback review", output)
            self.assertIn("No configuration was changed", output)
            self.assertEqual(
                {path: path.read_bytes() for path in original},
                original,
            )
            self.assertFalse((directory / "proposals").exists())

            with self.assertRaisesRegex(ValueError, "record-only flags: --key"):
                _run(
                    [
                        "feedback",
                        "review",
                        "--key",
                        "example/lot-1",
                        "--config",
                        str(config),
                    ]
                )
            with self.assertRaisesRegex(ValueError, "record-only flags: --watchlist"):
                _run(
                    [
                        "feedback",
                        "review",
                        "--watchlist",
                        str(watchlist),
                        "--config",
                        str(config),
                    ]
                )

    def test_review_save_writes_only_proposal_artifacts(self):
        with temporary_directory() as directory:
            config, watchlist, feedback_file = self._files(directory)
            for index in range(1, 4):
                item = _item(
                    f"lot-{index}", total_cost="30" if index == 1 else "40"
                )
                WatchlistStore(watchlist).save(item)
                argv = self._record_args(
                    "yes" if index == 1 else "too-expensive",
                    config,
                    watchlist,
                    feedback_file,
                )
                argv[argv.index("example/lot-1")] = f"example/lot-{index}"
                _run(argv)

            original_config = config.read_bytes()
            original_watchlist = watchlist.read_bytes()
            original_feedback = feedback_file.read_bytes()
            proposals = directory / "proposals"
            output = _run(
                [
                    "feedback",
                    "review",
                    "--config",
                    str(config),
                    "--feedback-file",
                    str(feedback_file),
                    "--proposal-dir",
                    str(proposals),
                    "--save",
                ]
            )

            saved = tuple(proposals.glob("*.json"))
            self.assertTrue(saved, output)
            self.assertIn("Saved proposal:", output)
            self.assertFalse(json.loads(saved[0].read_text(encoding="utf-8"))["config_changed"])
            self.assertEqual(config.read_bytes(), original_config)
            self.assertEqual(watchlist.read_bytes(), original_watchlist)
            self.assertEqual(feedback_file.read_bytes(), original_feedback)


if __name__ == "__main__":
    unittest.main()
