"""Private delivery receipts, including their transaction boundary."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime

from auction_lens.notifications import (
    DeliveryChannel,
    DeliveryItem,
    DeliveryRoute,
    ReportKind,
)
from auction_lens.storage import (
    DELIVERY_LOCK_TIMEOUT_SECONDS,
    DeliveryLedger,
)
from support import temporary_directory


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _route(
    *,
    report_kind: ReportKind = ReportKind.FINDINGS,
    channel: DeliveryChannel = DeliveryChannel.EMAIL,
    destination: str = "destination-a",
) -> DeliveryRoute:
    return DeliveryRoute(report_kind, channel, _digest(destination))


ITEM = DeliveryItem("example", "listing-1", "18.00")
SUMMARY = _digest("summary-one")


class DeliveryReadinessTests(unittest.TestCase):
    def test_missing_ledger_is_ready_without_creating_it_or_its_parent(self):
        with temporary_directory() as directory:
            path = directory / "not-created" / "deliveries.sqlite3"
            DeliveryLedger(path).check_ready()
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_a_non_directory_parent_is_not_viable(self):
        with temporary_directory() as directory:
            blocker = directory / "not-a-directory"
            blocker.write_text("synthetic", encoding="utf-8")
            ledger = DeliveryLedger(blocker / "deliveries.sqlite3")
            with self.assertRaisesRegex(ValueError, "is not a directory"):
                ledger.check_ready()

    def test_corrupt_existing_file_is_refused(self):
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            path.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(ValueError, "not a valid SQLite database"):
                DeliveryLedger(path).check_ready()

    def test_future_schema_is_refused_with_an_upgrade_message(self):
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("PRAGMA user_version = 2")
            with self.assertRaisesRegex(ValueError, "newer than supported.*upgrade"):
                DeliveryLedger(path).check_ready()

    def test_wrong_version_one_schema_is_refused(self):
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("CREATE TABLE delivery_items (wrong TEXT)")
                    connection.execute("PRAGMA user_version = 1")
            with self.assertRaisesRegex(ValueError, "tables do not match version 1"):
                DeliveryLedger(path).check_ready()

    def test_existing_ledger_readiness_check_does_not_change_its_bytes(self):
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            ledger = DeliveryLedger(path)
            with ledger.session(_route()) as delivery:
                delivery.accept([ITEM], SUMMARY)
            before = path.read_bytes()

            ledger.check_ready()

            self.assertEqual(path.read_bytes(), before)


class DeliverySessionTests(unittest.TestCase):
    def test_success_is_visible_inside_transaction_but_commits_on_context_exit(self):
        delivered_at = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            ledger = DeliveryLedger(path)
            with ledger.session(_route()) as delivery:
                self.assertEqual(delivery.revisions([ITEM]), {})
                self.assertTrue(delivery.summary_changed(SUMMARY))

                delivery.accept([ITEM], SUMMARY, delivered_at=delivered_at)

                self.assertEqual(delivery.revisions([ITEM]), {ITEM.key: "18.00"})
                self.assertFalse(delivery.summary_changed(SUMMARY))
                with closing(sqlite3.connect(path)) as outside:
                    count_before_commit = outside.execute(
                        "SELECT COUNT(*) FROM delivery_items"
                    ).fetchone()[0]
                self.assertEqual(count_before_commit, 0)

            with ledger.session(_route()) as delivery:
                self.assertEqual(delivery.revisions([ITEM]), {ITEM.key: "18.00"})
                self.assertFalse(delivery.summary_changed(SUMMARY))
            with closing(sqlite3.connect(path)) as connection:
                stored_at = connection.execute(
                    "SELECT delivered_at FROM delivery_items"
                ).fetchone()[0]
            self.assertEqual(stored_at, delivered_at.isoformat())

    def test_body_failure_rolls_back_even_after_accept(self):
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                with ledger.session(_route()) as delivery:
                    delivery.accept([ITEM], SUMMARY)
                    raise RuntimeError("transport failed")

            with ledger.session(_route()) as delivery:
                self.assertEqual(delivery.revisions([ITEM]), {})
                self.assertTrue(delivery.summary_changed(SUMMARY))

    def test_report_channel_and_destination_are_independent_routes(self):
        routes = (
            _route(),
            _route(report_kind=ReportKind.WATCHLIST),
            _route(channel=DeliveryChannel.WEBHOOK),
            _route(destination="destination-b"),
        )
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with ledger.session(routes[0]) as delivery:
                delivery.accept([ITEM], SUMMARY)

            for route in routes[1:]:
                with self.subTest(route=route):
                    with ledger.session(route) as delivery:
                        self.assertEqual(delivery.revisions([ITEM]), {})
                        self.assertTrue(delivery.summary_changed(SUMMARY))

    def test_accept_records_only_the_selected_events(self):
        omitted = DeliveryItem("example", "listing-2", "9.00")
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with ledger.session(_route()) as delivery:
                delivery.accept([ITEM], SUMMARY)
            with ledger.session(_route()) as delivery:
                self.assertEqual(
                    delivery.revisions([ITEM, omitted]),
                    {ITEM.key: ITEM.revision},
                )

    def test_a_summary_only_report_has_a_receipt_without_a_listing(self):
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with ledger.session(_route()) as delivery:
                delivery.accept([], SUMMARY)
            with ledger.session(_route()) as delivery:
                self.assertEqual(delivery.revisions([ITEM]), {})
                self.assertFalse(delivery.summary_changed(SUMMARY))

    def test_conflicting_duplicate_revisions_are_refused(self):
        changed = DeliveryItem(ITEM.source, ITEM.listing_id, "19.00")
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with ledger.session(_route()) as delivery:
                with self.assertRaisesRegex(ValueError, "conflicting revisions"):
                    delivery.revisions([ITEM, changed])
                with self.assertRaisesRegex(ValueError, "conflicting revisions"):
                    delivery.accept([ITEM, changed], SUMMARY)

    def test_invalid_summary_and_timestamp_are_refused(self):
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            with ledger.session(_route()) as delivery:
                with self.assertRaisesRegex(ValueError, "full lowercase SHA-256"):
                    delivery.summary_changed("report outcome text")
                with self.assertRaisesRegex(ValueError, "timezone-aware"):
                    delivery.accept([ITEM], SUMMARY, datetime(2026, 9, 10))
                with self.assertRaisesRegex(TypeError, "iterable of DeliveryItem"):
                    delivery.revisions("not delivery items")

    def test_overlapping_sessions_wait_and_then_see_the_first_receipt(self):
        self.assertGreater(DELIVERY_LOCK_TIMEOUT_SECONDS, 30)
        with temporary_directory() as directory:
            ledger = DeliveryLedger(directory / "deliveries.sqlite3")
            worker_started = threading.Event()
            worker_entered = threading.Event()
            worker_errors: list[BaseException] = []
            worker_revisions: dict[tuple[str, str], str] = {}

            def open_overlapping_session() -> None:
                worker_started.set()
                try:
                    with ledger.session(_route()) as delivery:
                        worker_revisions.update(delivery.revisions([ITEM]))
                        worker_entered.set()
                except BaseException as error:
                    worker_errors.append(error)
                    worker_entered.set()

            with ledger.session(_route()) as first:
                first.accept([ITEM], SUMMARY)
                worker = threading.Thread(target=open_overlapping_session)
                worker.start()
                self.assertTrue(worker_started.wait(1))
                self.assertFalse(worker_entered.wait(0.2))

            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker_errors, [])
            self.assertEqual(worker_revisions, {ITEM.key: ITEM.revision})

    def test_schema_and_file_contain_only_opaque_delivery_facts(self):
        raw_destination = "synthetic-recipient-marker"
        with temporary_directory() as directory:
            path = directory / "deliveries.sqlite3"
            ledger = DeliveryLedger(path)
            with ledger.session(_route(destination=raw_destination)) as delivery:
                delivery.accept([ITEM], SUMMARY)

            with closing(sqlite3.connect(path)) as connection:
                columns = {
                    row[1]
                    for table in ("delivery_items", "delivery_summaries")
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                destinations = {
                    row[0]
                    for row in connection.execute(
                        "SELECT destination_hash FROM delivery_items "
                        "UNION SELECT destination_hash FROM delivery_summaries"
                    )
                }
            self.assertTrue(
                {"recipient", "webhook", "title", "url", "photo"}.isdisjoint(columns)
            )
            self.assertEqual(destinations, {_digest(raw_destination)})
            self.assertNotIn(raw_destination.encode("utf-8"), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
