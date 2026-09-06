"""Decoding the value graph a streaming server framework sends.

Some sites do not put their page data in the HTML as JSON. They send a flat
array of interned values instead, and describe the object graph as indexes into
it, so a string used forty times is written once. This module turns that back
into ordinary Python.

The encoding has three rules. A scalar is itself. An array is a list of indexes.
An object is written ``{"_<keyIndex>": valueIndex}``, so both its keys and its
values are indexes into the same flat array.

Nothing here knows anything about auctions; it is the envelope, not the letter.
"""

from __future__ import annotations

import json
from typing import Any

# The one negative marker seen in the wild, standing in for null. Any other
# marker is refused rather than guessed at: a wrong guess here silently becomes
# a wrong price, which is the one kind of mistake this project cannot make.
NULL_MARKER = -5


def decode(payload: str) -> Any:
    """Read one streamed payload into the value it describes."""
    values = json.loads(payload.splitlines()[0])
    if not isinstance(values, list) or not values:
        raise ValueError("a streamed payload must be a non-empty array of values")
    return _resolve(0, values, seen=frozenset())


def _resolve(index: int, values: list[Any], *, seen: frozenset[int]) -> Any:
    """Follow one index, refusing a graph that points back at itself."""
    if index < 0:
        return _marker(index)
    if index in seen:
        raise ValueError(f"streamed payload refers to itself at index {index}")
    if index >= len(values):
        raise ValueError(f"streamed payload refers to missing index {index}")

    node = values[index]
    deeper = seen | {index}
    if isinstance(node, dict):
        return {
            _resolve(_key_index(key), values, seen=deeper): _resolve(
                value, values, seen=deeper
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_resolve(item, values, seen=deeper) for item in node]
    return node


def _key_index(key: str) -> int:
    """Object keys are written as an underscore and the index of the key text."""
    if not key.startswith("_") or not key[1:].lstrip("-").isdigit():
        raise ValueError(f"streamed object key {key!r} is not an index")
    return int(key[1:])


def _marker(index: int) -> None:
    if index == NULL_MARKER:
        return None
    raise ValueError(
        f"streamed payload used unknown marker {index}; "
        "decode it deliberately rather than guessing what it means"
    )
