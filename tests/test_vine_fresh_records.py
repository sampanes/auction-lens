"""The morning runner stages only the records young enough to mail."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auction_lens.listings.files import unique_lots
from auction_lens.providers.vine import read as vine
from support import temporary_directory

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "vine" / "fresh-records.py"
RUNNER = ROOT / "scripts" / "vine" / "run-vine.cmd"
SPEC = importlib.util.spec_from_file_location("vine_fresh_records", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fresh_records = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh_records)


class FreshRecordStagingTests(unittest.TestCase):
    NOW = datetime(2026, 10, 10, 14, 0, tzinfo=UTC)

    def row(self, name: str, hours_ago: float) -> dict[str, object]:
        return {
            "asin": name,
            "title": f"Synthetic {name}",
            "received_at": (self.NOW - timedelta(hours=hours_ago)).isoformat(),
        }

    def write_records(self, directory: Path, *rows: object) -> None:
        lines = [json.dumps(row) if not isinstance(row, str) else row for row in rows]
        (directory / "queue-2026-10-10.ndjson").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def staged(self, path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_only_rows_inside_the_window_enter_the_stage(self):
        with temporary_directory() as directory:
            recent = self.row("RECENT", 2)
            boundary = self.row("BOUNDARY", fresh_records.MAIL_WINDOW_HOURS)
            stale = self.row("STALE", fresh_records.MAIL_WINDOW_HOURS + 0.01)
            future = self.row("FUTURE", -5)
            unstamped = {"asin": "UNSTAMPED", "title": "Synthetic unstamped"}
            self.write_records(
                directory,
                stale,
                "{not json",
                future,
                unstamped,
                recent,
                boundary,
            )
            output = directory / "fresh.ndjson"

            count, newest = fresh_records.stage_fresh_records(
                output, now=self.NOW, record_dir=directory
            )

            self.assertEqual(count, 2)
            self.assertEqual(
                [row["asin"] for row in self.staged(output)],
                ["RECENT", "BOUNDARY"],
            )
            self.assertEqual(newest, self.NOW - timedelta(hours=2))

    def test_a_recent_row_does_not_carry_a_stale_sibling_into_the_stage(self):
        with temporary_directory() as directory:
            self.write_records(directory, self.row("OLD", 30), self.row("NEW", 1))
            output = directory / "fresh.ndjson"

            fresh_records.stage_fresh_records(output, now=self.NOW, record_dir=directory)

            self.assertEqual([row["asin"] for row in self.staged(output)], ["NEW"])

    def test_newest_duplicate_is_first_for_pulls_first_wins_deduplication(self):
        with temporary_directory() as directory:
            older = self.row("DUPLICATE", 8)
            newer = {**self.row("DUPLICATE", 2), "etv": "37.00"}
            self.write_records(directory, older, newer)
            output = directory / "fresh.ndjson"

            fresh_records.stage_fresh_records(
                output, now=self.NOW, record_dir=directory
            )

            staged = self.staged(output)
            self.assertEqual([row.get("etv") for row in staged], ["37.00", None])
            deduplicated = unique_lots(
                vine.read_records(output.read_text(encoding="utf-8"), source="vine")
            )
            self.assertEqual(len(deduplicated), 1)
            self.assertEqual(deduplicated[0]["estimated_retail"], "37.00")

    def test_only_far_future_rows_leave_no_usable_diagnostic(self):
        with temporary_directory() as directory:
            self.write_records(directory, self.row("FUTURE", -5))
            output = directory / "fresh.ndjson"

            count, newest = fresh_records.stage_fresh_records(
                output, now=self.NOW, record_dir=directory
            )

            self.assertEqual(count, 0)
            self.assertIsNone(newest)
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_no_fresh_rows_atomically_replace_a_stale_stage_with_empty(self):
        with temporary_directory() as directory:
            self.write_records(directory, self.row("OLD", 30))
            output = directory / "fresh.ndjson"
            output.write_text(json.dumps(self.row("LEFTOVER", 1)) + "\n", encoding="utf-8")

            count, _newest = fresh_records.stage_fresh_records(
                output, now=self.NOW, record_dir=directory
            )

            self.assertEqual(count, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_command_requires_an_output_and_reports_the_staged_count(self):
        with temporary_directory() as directory:
            self.write_records(directory, self.row("NEW", 1))
            output = directory / "fresh.ndjson"
            original_dir = fresh_records.RECORD_DIR
            original_now = fresh_records.now_utc
            fresh_records.RECORD_DIR = directory
            fresh_records.now_utc = lambda: self.NOW
            said = io.StringIO()
            try:
                with redirect_stdout(said):
                    status = fresh_records.main(["--output", str(output)])
            finally:
                fresh_records.RECORD_DIR = original_dir
                fresh_records.now_utc = original_now

            self.assertEqual(status, 0)
            self.assertIn("staged 1 record(s)", said.getvalue())


class MorningRunnerContractTests(unittest.TestCase):
    def test_runner_pulls_the_fresh_stage_and_logs_truthful_completion(self):
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn('fresh-records.py" --output "%FRESH_RECORDS%"', runner)
        self.assertIn('pull --config "%CONFIG%" --input "%FRESH_RECORDS%"', runner)
        self.assertIn("fresh-record run completed", runner)
        self.assertNotIn("report sent", runner)


if __name__ == "__main__":
    unittest.main()
