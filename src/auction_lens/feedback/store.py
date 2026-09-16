"""An append-only, atomically written JSON event log for private feedback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..files import read_json, write_json_atomically
from ..values import parse_decimal
from .model import (
    FeedbackAction,
    FeedbackEvent,
    FeedbackEvidence,
    FeedbackLabel,
    FeedbackTarget,
    FeedbackTargetKind,
)

FILE_VERSION = 1
EVENTS_KEY = "events"


@dataclass(frozen=True)
class FeedbackStore:
    """One caller-selected JSON file whose history is never edited in place."""

    path: Path

    def events(self) -> tuple[FeedbackEvent, ...]:
        """Read the complete event history in append order."""
        if not self.path.exists():
            return ()
        document = read_json(self.path, default=None)
        if not isinstance(document, dict):
            raise ValueError(f"{self.path}: feedback log must be an object")
        version = document.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                f"{self.path}: feedback version must be the whole number "
                f"{FILE_VERSION}"
            )
        if version != FILE_VERSION:
            raise ValueError(
                f"{self.path}: unsupported feedback version "
                f"{version}; expected {FILE_VERSION}"
            )
        rows = document.get(EVENTS_KEY)
        if not isinstance(rows, list):
            raise ValueError(f"{self.path}: events must be a list")
        events = tuple(self._read(row, index) for index, row in enumerate(rows))
        ids = [event.event_id for event in events]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.path}: event ids must be unique")
        return events

    def current(self) -> tuple[FeedbackEvent, ...]:
        """Latest non-cleared feedback for each physical item and target."""
        latest: dict[tuple[str, str, str], FeedbackEvent] = {}
        for event in self.events():
            latest[event.current_key] = event
        return tuple(
            event
            for event in latest.values()
            if event.action == FeedbackAction.RECORD
        )

    def append(self, event: FeedbackEvent) -> bool:
        """Append one event, or do nothing when the latest opinion is identical."""
        events = self.events()
        if any(stored.event_id == event.event_id for stored in events):
            if event in events:
                return False
            raise ValueError(f"duplicate feedback event id: {event.event_id}")
        latest = next(
            (stored for stored in reversed(events) if stored.current_key == event.current_key),
            None,
        )
        if latest is not None and latest.has_same_meaning_as(event):
            return False
        write_json_atomically(
            self.path,
            {
                "version": FILE_VERSION,
                EVENTS_KEY: [_event_as_json(stored) for stored in (*events, event)],
            },
        )
        return True

    def _read(self, row: Any, index: int) -> FeedbackEvent:
        if not isinstance(row, dict):
            raise ValueError(f"{self.path}: events[{index}] must be an object")
        try:
            return _event_from_json(row)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{self.path}: events[{index}]: {error}") from error


def _event_as_json(event: FeedbackEvent) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "recorded_at": event.recorded_at.isoformat(),
        "action": event.action.value,
        "item_key": event.item_key,
        "target": {
            "kind": event.target.kind.value,
            "id": event.target.target_id,
            "name": event.target.name,
        },
        "label": None if event.label is None else event.label.value,
        "note": event.note,
        "config_sha256": event.config_sha256,
        "evidence": _evidence_as_json(event.evidence),
    }


def _evidence_as_json(evidence: FeedbackEvidence) -> dict[str, Any]:
    return {
        "item_key": evidence.item_key,
        "source": evidence.source,
        "listing_id": evidence.listing_id,
        "inventory_id": evidence.inventory_id,
        "title": evidence.title,
        "observed_at": (
            None if evidence.observed_at is None else evidence.observed_at.isoformat()
        ),
        "all_in_cost": _decimal_text(evidence.all_in_cost),
        "estimated_retail": _decimal_text(evidence.estimated_retail),
        "condition_labels": list(evidence.condition_labels),
        "quality_rating": evidence.quality_rating,
    }


def _event_from_json(row: dict[str, Any]) -> FeedbackEvent:
    recorded_at = _required_datetime(row.get("recorded_at"), "recorded_at")
    target = _target_from_json(row.get("target"))
    evidence = _evidence_from_json(row.get("evidence"))
    label_value = row.get("label")
    label = None if label_value is None else FeedbackLabel(_text(label_value, "label"))
    return FeedbackEvent(
        event_id=_text(row["id"], "id"),
        recorded_at=recorded_at,
        action=FeedbackAction(_text(row["action"], "action")),
        item_key=_text(row["item_key"], "item_key"),
        target=target,
        label=label,
        note=_text(row.get("note", ""), "note"),
        config_sha256=_text(row["config_sha256"], "config_sha256"),
        evidence=evidence,
    )


def _target_from_json(value: Any) -> FeedbackTarget:
    if not isinstance(value, dict):
        raise ValueError("target must be an object")
    return FeedbackTarget(
        kind=FeedbackTargetKind(_text(value.get("kind"), "target.kind")),
        target_id=_text(value.get("id"), "target.id"),
        name=_text(value.get("name"), "target.name"),
    )


def _evidence_from_json(value: Any) -> FeedbackEvidence:
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    labels = value.get("condition_labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("evidence.condition_labels must be a list of text")
    rating = value.get("quality_rating")
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int)):
        raise ValueError("evidence.quality_rating must be a whole number or null")
    return FeedbackEvidence(
        item_key=_text(value.get("item_key"), "evidence.item_key"),
        source=_text(value.get("source"), "evidence.source"),
        listing_id=_text(value.get("listing_id"), "evidence.listing_id"),
        inventory_id=_text(value.get("inventory_id", ""), "evidence.inventory_id"),
        title=_text(value.get("title", ""), "evidence.title"),
        observed_at=_optional_datetime(value.get("observed_at"), "evidence.observed_at"),
        all_in_cost=_optional_decimal_text(
            value.get("all_in_cost"), "evidence.all_in_cost"
        ),
        estimated_retail=_optional_decimal_text(
            value.get("estimated_retail"), "evidence.estimated_retail"
        ),
        condition_labels=tuple(labels),
        quality_rating=rating,
    )


def _required_datetime(value: Any, field_name: str) -> datetime:
    parsed = _optional_datetime(value, field_name)
    if parsed is None:
        raise ValueError(f"{field_name} is required")
    return parsed


def _optional_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    text = _text(value, field_name).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _optional_decimal_text(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an exact decimal string or null")
    return parse_decimal(value, field_name=field_name)
