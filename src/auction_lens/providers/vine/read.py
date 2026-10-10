"""Read what the browser companion already saw, and refuse to go looking.

Vine has no API and its queues sit behind a customer login, so the only place
those pages can be read is the operator's own signed-in browser. That reading
is done by the companion in ``scripts/vine/``, which writes one JSON object per
tile it observed. This module turns those lines into canonical rows.

Two of the four adapter callables exist here only to say no. Every other
provider answers "go find me lots" by making requests; this one must not,
because a request from this program would be an unattended process using a
person's shopping session. Refusing inside the adapter rather than leaving the
provider unregistered is what gives the refusal somewhere to explain itself,
and it is why ``doctor`` can report the limit before a scheduled run depends
on it.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Sequence
from html import unescape
from typing import Any

from ...config.provider import AcquisitionConfig, ProviderConfig

ITEM_URL = "https://www.amazon.com/dp/{asin}"

# Nothing is bid on a Vine item, so its cost is never a price. What it does
# cost is income tax on the value Amazon declares, which is why the row sets
# the flag that makes the cost estimator ask for that rate.
NO_BID = "0"

# Every item in these queues is offered as new and unused. Saying so lets the
# configured condition policies judge a Vine lot by the same words they judge
# everything else by, rather than treating an empty condition as unknown.
VINE_CONDITIONS = ("new",)

REFUSAL = (
    "the vine provider cannot discover lots: Vine sits behind a personal "
    "shopping login, so this program never requests one of its pages. Run the "
    "companion in scripts/vine/, then read its records with "
    "`pull --input private/vine`."
)


def discover_searches(
    provider: ProviderConfig,
    config: AcquisitionConfig,
    terms: Iterable[str],
) -> Sequence[object]:
    """Refuse to request anything, which is this provider's whole contract."""
    raise ValueError(REFUSAL)


def check_discovery_ready(
    provider: ProviderConfig,
    config: AcquisitionConfig,
    terms: Iterable[str],
) -> None:
    """Tell an unattended run it cannot collect here, before it tries.

    ``doctor`` exists so a scheduler can refuse to go on. A provider that can
    never discover has to fail that check, or a nightly run would be installed
    against a provider it can only ever read from disk.
    """
    raise ValueError(REFUSAL)


def read_records(text: str, *, source: str, page_url: str = "") -> list[dict[str, Any]]:
    """Read one NDJSON record file into canonical rows.

    The first argument is a page's text everywhere else in the adapter, and
    here it is the file the companion wrote; the shape of the seam is the same.
    ``page_url`` is unused because every row already names an absolute address
    built from its own identifier, rather than a link relative to a page.

    One line that cannot be read does not lose the file. A record missing what
    names it is skipped, because a tile with no identifier cannot become a lot
    and the alternative is refusing a day of observations over one bad line.
    Nothing here deduplicates: a day's file holds every sighting, and
    ``listings/files.py`` already owns which one survives.
    """
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        row = _row(record, source=source)
        if row is not None:
            rows.append(row)
    if not rows:
        raise ValueError("no Vine records found; this is not a companion record file")
    return rows


def _row(record: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    """Turn one observed tile into a canonical row, or decline it."""
    asin = str(record.get("asin") or "").strip()
    title = _decoded(record.get("title"))
    if not asin or not title:
        return None
    return {
        "source": source,
        "listing_id": asin,
        "title": title,
        "url": ITEM_URL.format(asin=asin),
        "current_bid": NO_BID,
        "estimated_retail": record.get("etv"),
        "value_is_taxable_income": True,
        "conditions": VINE_CONDITIONS,
        "photo_urls": _photos(record),
    }


# What a Vine title says in typography a report cannot print. These listings
# are written by sellers worldwide and arrive carrying fullwidth commas,
# en dashes, non-breaking hyphens, multiplication signs and direction marks.
# A report is printed, mailed and posted as text, and `print` of a character
# outside the console encoding ends the whole run: measured on a matched lot
# whose title held one, `run` stopped with "'charmap' codec can't encode
# character U+FF0C" and produced no report at all. Thirteen of the first
# five hundred records carried such a character, and the unattended morning
# mail would have turned that into a line in a log.
#
# The substitutions matter more than the stripping. A picture frame sold as
# 24" states its size with a right quotation mark, and a rule asking about a
# dimension is looking for exactly that character; dropping it would lose the
# titles the rule was written for, which is the same fault this module's
# unescaping already exists to prevent.
_FOLDED = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "--", "\u2015": "--", "\u2018": "'", "\u2019": "'",
    "\u201a": "'", "\u201b": "'", "\u201c": '"', "\u201d": '"',
    "\u201e": '"', "\u2032": "'", "\u2033": '"', "\u2044": "/",
    "\u2022": "-", "\u00b7": "-", "\u00d7": "x", "\u00f7": "/",
    "\u3001": ",", "\u3002": ".", "\u00a0": " ", "\u2009": " ",
    "\u202f": " ", "\u200b": "", "\u200c": "", "\u200d": "",
    "\u200e": "", "\u200f": "", "\ufeff": "", "\u00ae": "",
    "\u2122": "", "\u00a9": "",
}


def _ascii_only(text: str) -> str:
    """Fold a title to ASCII, keeping what a rule and a reader need.

    Decomposing first is what turns the fullwidth and superscript forms into
    their plain equivalents, and it is also why the map is applied afterwards:
    a vulgar fraction decomposes to digits around a fraction slash, so the
    half in "1/2 inch" only survives if that slash is mapped after the
    decomposition rather than before it.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    mapped = "".join(_FOLDED.get(character, character) for character in decomposed)
    return mapped.encode("ascii", "ignore").decode("ascii")


def _decoded(value: Any) -> str:
    """Undo the HTML escaping a tile's own markup carried, and fold to ASCII.

    The companion decodes this at the source now, but records written before it
    did still hold `&#34;` where a title states a dimension in inches -- which
    is exactly the character a matching rule about a size is looking for. The
    cost of asking twice is nothing; the cost of trusting the writer is a rule
    that silently misses the titles it was written for.

    Unescaping comes first, because an entity can name a character the fold is
    responsible for.
    """
    return _ascii_only(unescape(str(value or ""))).strip()


def _photos(record: dict[str, Any]) -> tuple[str, ...]:
    """The one image a tile carries, if it carried one."""
    image = str(record.get("image_url") or "").strip()
    return (image,) if image else ()
