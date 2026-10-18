"""Reading what a closed lot actually sold for, and refusing to guess.

Two things are worth locking down here. The first is the gate: asking for a
page per lot is tens of times the load of a search sweep, so it must refuse
before the first connection unless that was separately agreed. The second is
the honesty rule: a lot that is still open must not be recorded, because
bidding extends in short steps and writing a live bid as a sale would put back
exactly the error settling exists to remove.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from auction_lens.cli import main as cli_main
from auction_lens.config.provider import AcquisitionConfig, AcquisitionMode, ProviderConfig
from auction_lens.history.settlements import SettlementStore
from auction_lens.listings.model import PendingLot, Settlement
from auction_lens.providers.nellis.settle import require_settling_allowed, settle_lots
from auction_lens.providers.registry import resolve_provider
from support import temporary_database, temporary_directory

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nellis"
OPEN_PAGE = FIXTURES / "product-page.html"
CLOSED_PAGE = FIXTURES / "product-page-closed.html"

NOW = datetime(2026, 10, 17, 23, 30, tzinfo=UTC)
ADDRESS = "https://example-auction.test/p/Example-Lot/4242"
# Settling refuses to connect without an address the provider can answer to.
CONTACT_USER_AGENT = "AuctionLens test contact=operator@auction-lens.dev"


class FakeResponse:
    """Just enough of an HTTP response for the one call settling makes."""

    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exception) -> bool:
        return False


def opener_serving(path: Path, status: int = 200):
    """An opener that answers every request with one saved page."""
    body = path.read_text(encoding="utf-8")
    calls: list[str] = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(body, status)

    opener.calls = calls
    return opener


def acquisition(*, ledger_file: str, **overrides) -> AcquisitionConfig:
    settings = {
        "mode": AcquisitionMode.AUTHORIZED_HTTP,
        "authorization_confirmed": True,
        "url": "https://example-auction.test/",
        "settle_authorized": True,
        "seconds_between_settles": Decimal("0"),
        "max_requests_per_day": 20,
        "minimum_interval_minutes": 0,
        "ledger_file": ledger_file,
        **overrides,
    }
    return AcquisitionConfig(**settings)


def enabled_provider() -> ProviderConfig:
    """A provider this program is allowed to contact, so one gate at a time."""
    return ProviderConfig(provider_id="nellis", enabled=True)


def pending(*, listing_id: str = "4242", scheduled: str = "2026-09-07T01:00:00+00:00"):
    return PendingLot(
        source="nellis", listing_id=listing_id, url=ADDRESS, scheduled_close=scheduled
    )


class SettlingGateTests(unittest.TestCase):
    """Permission to poll search pages is not permission to walk every lot."""

    def test_settling_is_refused_unless_separately_authorized(self):
        with self.assertRaises(RuntimeError) as refused:
            require_settling_allowed(acquisition(ledger_file="unused", settle_authorized=False))
        self.assertIn("settle_authorized", str(refused.exception))

    def test_no_request_is_made_when_settling_is_unauthorized(self):
        opener = opener_serving(CLOSED_PAGE)
        with self.assertRaises(RuntimeError):
            settle_lots(
                enabled_provider(),
                acquisition(ledger_file="unused", settle_authorized=False),
                [pending()],
                instant=NOW,
                opener=opener,
            )
        self.assertEqual(opener.calls, [], "the gate must close before connecting")

    def test_the_provider_declares_whether_it_can_settle_at_all(self):
        """A preflight needs the answer without calling a refusal to find out."""
        self.assertTrue(resolve_provider("nellis").settles)
        self.assertFalse(resolve_provider("vine").settles)


class SettlingEnvironmentTests(unittest.TestCase):
    """A request-making command has to load the contact it identifies itself by.

    Settling shipped without this and failed on the identified user agent at the
    first real lot, which an unattended run at five past midnight would have
    reported to nobody. The dispatch table is the one owner of which commands
    need the ignored environment file, so the test asks it rather than the help.
    """

    def test_settling_loads_the_ignored_environment_file(self):
        # The command itself is replaced in the dispatch table, not in its own
        # module: the table holds the function by reference, so patching the
        # module attribute leaves the real command in place, which this test
        # duly ran against the real database before only a later gate stopped
        # it short of asking the provider for anything.
        settled = Mock(return_value=0)
        with patch("auction_lens.cli.load_env_file") as load_env:
            with patch.dict("auction_lens.cli.COMMANDS", {"settle": settled}):
                exit_code = cli_main(["settle", "--limit", "1"])
        self.assertEqual(exit_code, 0)
        settled.assert_called_once()
        load_env.assert_called_once_with(".env")


class SettlingTests(unittest.TestCase):
    def setUp(self):
        self._directory = temporary_directory()
        self.ledger = str(Path(self._directory.__enter__()) / "poll-ledger.json")
        self.addCleanup(self._directory.__exit__, None, None, None)
        identified = patch.dict(
            "os.environ",
            {AcquisitionConfig().user_agent_env: CONTACT_USER_AGENT},
            clear=False,
        )
        identified.start()
        self.addCleanup(identified.stop)

    def test_a_closed_page_is_read_as_a_sale(self):
        outcome = settle_lots(
            enabled_provider(),
            acquisition(ledger_file=self.ledger),
            [pending()],
            instant=NOW,
            opener=opener_serving(CLOSED_PAGE),
        )
        self.assertEqual(len(outcome.settlements), 1)
        sale = outcome.settlements[0]
        self.assertEqual(sale.final_bid, Decimal("8"))
        self.assertEqual(sale.bid_count, 8)
        self.assertEqual(sale.key, "nellis/4242")

    def test_a_lot_still_open_is_left_alone_rather_than_recorded(self):
        """A live bid written as a sale is the error this whole pass removes."""
        outcome = settle_lots(
            enabled_provider(),
            acquisition(ledger_file=self.ledger),
            [pending()],
            instant=NOW,
            opener=opener_serving(OPEN_PAGE),
        )
        self.assertEqual(outcome.settlements, ())
        self.assertEqual(outcome.still_open, 1)
        self.assertEqual(outcome.requested, 1)

    def test_a_close_later_than_the_one_scheduled_is_recorded_as_extended(self):
        """A late bid pushes the close out, and no earlier pass could know."""
        outcome = settle_lots(
            enabled_provider(),
            acquisition(ledger_file=self.ledger),
            [pending(scheduled="2026-09-07T01:00:00+00:00")],
            instant=NOW,
            opener=opener_serving(CLOSED_PAGE),
        )
        self.assertTrue(outcome.settlements[0].extended)

    def test_a_lot_whose_page_has_gone_costs_its_own_lot_and_not_the_run(self):
        outcome = settle_lots(
            enabled_provider(),
            acquisition(ledger_file=self.ledger),
            [pending()],
            instant=NOW,
            opener=opener_serving(CLOSED_PAGE, status=404),
        )
        self.assertEqual(outcome.settlements, ())
        self.assertEqual(outcome.missing, 1)

    def test_nothing_to_settle_makes_no_request_and_is_not_a_failure(self):
        opener = opener_serving(CLOSED_PAGE)
        outcome = settle_lots(
            enabled_provider(),
            acquisition(ledger_file=self.ledger),
            [],
            instant=NOW,
            opener=opener,
        )
        self.assertEqual(outcome.requested, 0)
        self.assertEqual(opener.calls, [])


class SettlementStoreTests(unittest.TestCase):
    def _store(self, database):
        return SettlementStore(database)

    def _listing_row(self, connection, listing_id, ends_at, *, url=ADDRESS):
        connection.execute(
            "INSERT INTO listings (source, listing_id, title, url, current_bid,"
            " estimated_retail, bid_count, ends_at, location, conditions,"
            " image_url, first_seen, last_seen)"
            " VALUES ('nellis', ?, 'Example Lot', ?, '5', '100', 3, ?, 'Example',"
            " '', '', ?, ?)",
            (listing_id, url, ends_at, ends_at, ends_at),
        )

    def _sale(self, listing_id, price):
        return Settlement(
            source="nellis",
            listing_id=listing_id,
            final_bid=Decimal(price),
            bid_count=4,
            closed_at=NOW,
            scheduled_close=NOW - timedelta(minutes=1),
            extended=True,
        )

    def test_a_settled_lot_stops_being_owed_a_reading(self):
        with temporary_database() as database:
            with database.connect() as connection:
                self._listing_row(connection, "1", "2026-10-17T22:00:00+00:00")
            store = self._store(database)
            window = {
                "closed_before": NOW,
                "closed_after": NOW - timedelta(hours=24),
            }
            self.assertEqual(store.pending_count(**window), 1)
            store.record([self._sale("1", "40")], settled_at=NOW)
            self.assertEqual(store.pending_count(**window), 0)
            self.assertEqual(store.pending(**window), ())

    def test_a_lot_outside_the_window_is_not_asked_about(self):
        """Both bounds matter: one protects the answer, one protects the provider."""
        with temporary_database() as database:
            with database.connect() as connection:
                self._listing_row(connection, "soon", "2026-10-17T23:29:00+00:00")
                self._listing_row(connection, "ancient", "2026-09-01T22:00:00+00:00")
            owed = self._store(database).pending(
                closed_before=NOW - timedelta(minutes=15),
                closed_after=NOW - timedelta(hours=24),
            )
            self.assertEqual([lot.listing_id for lot in owed], [])

    def test_lots_are_offered_oldest_close_first(self):
        """A run cut short by a limit should make progress through the backlog."""
        with temporary_database() as database:
            with database.connect() as connection:
                self._listing_row(connection, "later", "2026-10-17T21:00:00+00:00")
                self._listing_row(connection, "earlier", "2026-10-17T19:00:00+00:00")
            owed = self._store(database).pending(
                closed_before=NOW,
                closed_after=NOW - timedelta(hours=24),
                limit=1,
            )
            self.assertEqual([lot.listing_id for lot in owed], ["earlier"])

    def test_a_lot_with_no_address_cannot_be_asked_about(self):
        with temporary_database() as database:
            with database.connect() as connection:
                self._listing_row(connection, "1", "2026-10-17T22:00:00+00:00", url="")
            self.assertEqual(
                self._store(database).pending_count(
                    closed_before=NOW, closed_after=NOW - timedelta(hours=24)
                ),
                0,
            )

    def test_re_reading_a_lot_replaces_its_earlier_answer(self):
        with temporary_database() as database:
            store = self._store(database)
            store.record([self._sale("1", "40")], settled_at=NOW)
            store.record([self._sale("1", "55")], settled_at=NOW)
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT final_bid FROM settled_closes WHERE listing_id = '1'"
                ).fetchall()
            self.assertEqual([row[0] for row in rows], ["55"])


if __name__ == "__main__":
    unittest.main()
