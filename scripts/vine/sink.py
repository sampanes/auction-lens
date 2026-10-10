"""Hand the Vine browser companion its term plan and record what it saw.

Vine has no API and its listings sit behind a customer login, so the only place
those pages can be read is the operator's own signed-in browser. The companion
extension does that reading; this sink is the one seam between it and the
repository. It exists so the search terms keep a single owner: the extension is
told what to look for and holds no opinion of its own.

Nothing here talks to Amazon, and nothing here requests a Vine item.

Usage: python scripts/vine/sink.py [--port 8787]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auction_lens.config.load import load_config
from auction_lens.local_files import DEFAULT_CONFIG
from auction_lens.providers.search_terms import search_terms
from auction_lens.providers.vine.rota import Rota, next_window, resume_after
from auction_lens.providers.vine.swept import last_sweep_date, sweep_is_due, today

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD_DIR = ROOT / "private" / "vine"

# Where the rotation left off, and which window was last handed out. The
# arithmetic that uses them lives in the package beside the provider, so it can
# be tested; this file owns only the storage.
OFFSET_FILE = RECORD_DIR / "sweep-offset.json"

# What the sink saw, kept because nothing else keeps it. Both halves of this
# companion log to consoles nobody is watching at three in the morning: the
# service worker's console is behind a link on the extensions page, and this
# process prints to a window that scrolls. A sweep that failed overnight left
# no trace at all, and the same question -- did it run, what stopped it -- cost
# an evening of inference twice. A line per event is cheap.
LOG_FILE = RECORD_DIR / "sink.log"

# Whether a sweep has finished today, which two unrelated readers both need:
# the companion asks so it knows to start one, and the morning mail runner asks
# so it can stay silent rather than send a digest of nothing new. One file, so
# they cannot disagree about it.
#
# The date here is local, not UTC. "Has today's sweep happened" is a question
# about the operator's day; the record filenames are UTC because they are
# storage. Mixing the two deliberately, because they answer different things.
LAST_SWEEP_FILE = RECORD_DIR / "last-sweep.json"

# Refused here rather than fixed in search_terms, because the fallback is
# right where it lives: at an auction a rule's terms are its searches. Only
# this provider inverts that, and only because Amazon does not sell the article
# a Vine rule names, so the rule has to pin it where the fallback cannot see.
NO_SEARCHES = (
    "configuration declares no [provider.acquisition] searches, and this sink "
    "will not fall back to the interest rules' own terms. On Vine a rule pins "
    "its host product in all_terms, which that fallback never reads, so the "
    "sweep would search the fittings and never the thing they fit. Name the "
    "searches explicitly."
)

# Amazon's pacing is not the auction provider's pacing. The provider block in
# config carries a cadence an operator negotiated with that provider for that
# provider, and borrowing it here would quietly claim an authorization nobody
# gave. These two numbers are deliberately the sink's own, and visible.
DEFAULT_SPACING_SECONDS = 6
DEFAULT_MAX_SEARCHES = 12

# One page is 36 tiles, which is enough to judge whether a term is productive.
# Paging deeper costs a request per page out of the same budget, so the default
# stays at the first page and an operator raises it deliberately.
DEFAULT_MAX_PAGES_PER_TERM = 1

# One Vine page holds 36 tiles. A batch far above that is a bug upstream rather
# than a real page, and saying so early beats a surprising file.
MAX_ITEMS_PER_REQUEST = 500

REQUIRED_ITEM_FIELDS = ("asin", "title")
LOCAL_HOST = "127.0.0.1"
EXTENSION_ORIGIN_PREFIX = "chrome-extension://"


def record_file() -> Path:
    """One file per UTC day, so a day of observations stays together."""
    return RECORD_DIR / f"queue-{datetime.now(UTC).date().isoformat()}.ndjson"


def note(line: str) -> None:
    """Say something once, to the console and to the log that outlives it."""
    print(f"[sink] {line}", flush=True)
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(UTC).isoformat()} {line}\n")
    except OSError:
        # A sink that cannot write its log must still serve plans. Losing the
        # record of events is worse than nothing, but it is not worth refusing
        # the night's collecting over.
        pass


def _stored_rota() -> dict[str, object]:
    """Whatever is remembered about the rota, or nothing readable."""
    try:
        stored = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _stored_number(key: str) -> int:
    value = _stored_rota().get(key)
    return value if isinstance(value, int) and value >= 0 else 0


def read_offset() -> int:
    """Where the last sweep stopped, or the start if nothing is remembered."""
    return _stored_number("offset")


def write_offset(offset: int, served_from: int = 0, served: int = 0) -> None:
    """Remember where the next sweep should pick up, and what was lent out."""
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(
        json.dumps({"offset": offset, "served_from": served_from, "served": served}),
        encoding="utf-8",
    )


def read_last_sweep() -> str:
    """The local date a sweep last finished, or empty if none ever has."""
    try:
        stored = json.loads(LAST_SWEEP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return last_sweep_date(stored)


def record_sweep(searches: int) -> str:
    """Remember that a sweep ran today, so neither reader asks for another."""
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = {
        "date": today(),
        "searches": searches,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    LAST_SWEEP_FILE.write_text(json.dumps(stamp), encoding="utf-8")
    return stamp["date"]


def sweep_due() -> bool:
    """True until a sweep has finished today, per the shared reader."""
    return sweep_is_due({"date": read_last_sweep()}, today())


def serve_next_window(terms: list[str], size: int) -> Rota:
    """Hand out the next slice of the rota, assuming one term of it gets used.

    The offset moves by one here rather than by the width of the window, and
    the sweep corrects it upward when it reports what it actually spent. That
    is the safe direction to be wrong in. A sweep is usually throttled after a
    term or two, so advancing by the whole window counted searches that never
    happened -- and because the window and the list shared a factor, the rota
    could only ever start at half the positions in the list, leaving the other
    half unsearched unless a sweep survived two terms. Being wrong the other
    way costs a repeated search, which `pull` collapses anyway.
    """
    rota = next_window(terms, size, read_offset())
    served = len(rota.terms)
    write_offset(
        resume_after(rota.start, served, 1, rota.total), rota.start, served
    )
    return rota


def record_attempt(spent: int, reason: str, total: int, strays: int = 0) -> bool:
    """Take a sweep's account of itself: rewind the rota, claim the day or not.

    Returns whether the day is now claimed. A sweep that spent everything it
    was lent has done what a day's sweep is for; one cut short has not, so
    another is still owed and the terms it never reached are handed back.
    """
    served_from = _stored_number("served_from")
    served = _stored_number("served")
    write_offset(resume_after(served_from, served, spent, total), served_from, served)
    finished = served > 0 and spent >= served
    if finished:
        date = record_sweep(spent)
        note(f"sweep finished for {date} after {spent} search(es); none owed until tomorrow")
    else:
        note(f"sweep ended after {spent} of {served} search(es): {reason}; another is owed")
    if strays:
        # A page that reported while the sweep ran but was not the sweep's own
        # tab. One or two is the operator browsing Vine at the same time. A
        # sweep whose every page is one of these is collecting rows without
        # ever moving, which is what fourteen silent sweeps looked like.
        note(f"  {strays} page(s) reported from a tab the sweep did not open")
    return finished


def append_records(items: list[dict[str, object]]) -> int:
    """Append each observation as one JSON line, stamped with arrival time."""
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    received_at = datetime.now(UTC).isoformat()
    with record_file().open("a", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps({**item, "received_at": received_at}) + "\n")
    return len(items)


def read_items(payload: object) -> list[dict[str, object]]:
    """Check a posted batch completely before any of it is written."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("body must be an object with an 'items' list")
    if len(items) > MAX_ITEMS_PER_REQUEST:
        raise ValueError(f"{len(items)} items exceeds the {MAX_ITEMS_PER_REQUEST} cap")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("every item must be an object")
        missing = [name for name in REQUIRED_ITEM_FIELDS if not item.get(name)]
        if missing:
            raise ValueError(f"item missing {', '.join(missing)}")
    return items


class SinkHandler(BaseHTTPRequestHandler):
    """Two routes: what to search for, and what a search turned up."""

    # Set by ``serve`` so every request answers from one parsed configuration.
    plan: dict[str, object] = {}
    server_version = "auction-lens-vine-sink"
    sys_version = ""

    def do_GET(self) -> None:
        """Answer whether a sweep is owed, or hand out one sweep's terms."""
        # Asking whether a sweep is due has to be free of consequence: the
        # companion asks every half hour while Chrome is open, and /plan moves
        # the rota on. Answering both from one route would walk the whole term
        # list in an afternoon of polling and never search any of it.
        if self.path == "/due":
            due = sweep_due()
            self.send_json(200, {"sweep_due": due, "last_sweep": read_last_sweep()})
            return
        if self.path != "/plan":
            self.send_json(404, {"error": f"unknown path {self.path}"})
            return
        # Built per request rather than once at startup, because each sweep is
        # meant to get the next slice of the rota rather than the same one.
        rota = serve_next_window(list(self.plan["terms"]), int(self.plan["max_searches"]))
        note(f"serving {rota.description()}")
        self.send_json(200, {**self.plan, "terms": rota.terms})

    def do_OPTIONS(self) -> None:
        """Answer the preflight that a JSON post from the extension triggers."""
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        """Append observed tiles, or record that a sweep finished."""
        if self.path not in ("/items", "/swept"):
            self.send_json(404, {"error": f"unknown path {self.path}"})
            return
        # Any page the operator visits can post to a loopback port. Requiring
        # the extension origin is cheap and keeps a stray tab from writing
        # rows into private records; it is not meant to stop a local program.
        origin = self.headers.get("Origin", "")
        if not origin.startswith(EXTENSION_ORIGIN_PREFIX):
            self.send_json(403, {"error": "only the companion extension may post items"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self.send_json(400, {"error": "empty request body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        if self.path == "/swept":
            # Every sweep reports its end now, spent or not. The count is what
            # rewinds the rota and the reason is the only account of a failed
            # night that survives it; refusing to hear about a sweep that
            # searched nothing threw away both.
            spent = payload.get("searches") if isinstance(payload, dict) else 0
            why = payload.get("reason") if isinstance(payload, dict) else ""
            strays = payload.get("stray_reports") if isinstance(payload, dict) else 0
            finished = record_attempt(
                spent if isinstance(spent, int) and spent >= 0 else 0,
                why if isinstance(why, str) and why else "no reason given",
                len(self.plan["terms"]),
                strays if isinstance(strays, int) and strays >= 0 else 0,
            )
            self.send_json(200, {"finished": finished, "date": read_last_sweep()})
            return
        try:
            items = read_items(payload)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        self.send_json(200, {"recorded": append_records(items), "file": record_file().name})

    def log_message(self, fmt: str, *args: object) -> None:
        """Keep the console to one readable line per request."""
        print(f"[sink] {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, body: dict[str, object]) -> None:
        """Answer one JSON object, with the length the client needs."""
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def send_cors_headers(self) -> None:
        """Let the extension read the reply it just asked for."""
        self.send_header("Access-Control-Allow-Origin", "*")


def build_plan(
    config_path: str, spacing: int, max_searches: int, max_pages: int
) -> dict[str, object]:
    """Read the terms the configuration already declares, plus the sink's pacing.

    Every term is kept here rather than truncated to the budget. The budget is
    how many may be spent in one sweep, which is not the same claim as which
    ones exist, and conflating the two is what made a long list a short one.
    """
    config = load_config(config_path)
    if not config.acquisition.searches:
        raise ValueError(NO_SEARCHES)
    return {
        "terms": search_terms(config, []),
        "spacing_seconds": spacing,
        "max_searches": max_searches,
        "max_pages_per_term": max_pages,
    }


def serve(port: int, plan: dict[str, object]) -> None:
    """Answer on loopback only, until interrupted."""
    SinkHandler.plan = plan
    server = ThreadingHTTPServer((LOCAL_HOST, port), SinkHandler)
    terms = plan["terms"]
    spacing = plan["spacing_seconds"]
    # Clamped the same way the rota clamps it, so the banner cannot promise a
    # coverage a negative or zero budget will never deliver.
    budget = max(int(plan["max_searches"]), 0)
    note(f"listening on http://{LOCAL_HOST}:{port}")
    pages = plan["max_pages_per_term"]
    note(f"{len(terms)} terms, {budget} per sweep, {spacing}s apart")
    if budget:
        # The best case, and said as one. The rota now advances by what a sweep
        # actually spent, so a throttled night covers fewer terms than its
        # budget and takes correspondingly longer to come round. Announcing the
        # best case as the plain truth would tell the operator their whole list
        # was searched this week when a quarter of it was.
        sweeps = -(-len(terms) // budget)
        note(f"{pages} page(s) each; {sweeps} full sweep(s) would cover every term")
    else:
        print("[!] a budget of zero searches means a sweep does nothing", flush=True)
    note(f"recording to {RECORD_DIR}")
    last = read_last_sweep()
    owed = "a sweep is owed today" if sweep_due() else f"already swept {last}"
    note(f"{owed}; the companion checks every half hour")
    print("[sink] stop with Ctrl+C or scripts\\vine\\stop-sink.cmd", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[sink] stopped", flush=True)
    finally:
        server.server_close()


def main() -> int:
    """Parse the operator's choices and start serving."""
    parser = argparse.ArgumentParser(description="Vine companion term plan and record sink")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="configuration to read terms")
    parser.add_argument("--port", type=int, default=8787, help="loopback port to listen on")
    parser.add_argument(
        "--spacing",
        type=int,
        default=DEFAULT_SPACING_SECONDS,
        help="seconds the companion waits between searches",
    )
    parser.add_argument(
        "--max-searches",
        type=int,
        default=DEFAULT_MAX_SEARCHES,
        help="most searches the companion may run in one sweep",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES_PER_TERM,
        help="pages deep the companion may follow one term",
    )
    args = parser.parse_args()
    try:
        plan = build_plan(args.config, args.spacing, args.max_searches, args.max_pages)
    except ValueError as error:
        print(f"[X] {error}")
        return 1
    serve(args.port, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
