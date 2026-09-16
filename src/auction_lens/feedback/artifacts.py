"""Write immutable, deterministic, private proposal artifacts for human review."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from ..files import read_json, write_json_atomically
from .model import FeedbackProposal, ProposalChange

ARTIFACT_VERSION = 1


def save_feedback_proposal(proposal: FeedbackProposal, directory: Path) -> Path:
    """Save one suggestion once; never overwrite a different existing artifact."""
    path = directory / f"{proposal.proposal_id}.json"
    document = _proposal_document(proposal)
    if path.exists():
        if read_json(path, default=None) != document:
            raise FileExistsError(f"refusing to replace immutable proposal: {path}")
        return path
    write_json_atomically(path, document)
    return path


def _proposal_document(proposal: FeedbackProposal) -> dict[str, Any]:
    """Exclude listing titles, notes, links, local paths, and contact details."""
    return {
        "version": ARTIFACT_VERSION,
        "proposal_id": proposal.proposal_id,
        "config_sha256": proposal.config_sha256,
        "target": {
            "kind": proposal.target.kind.value,
            "id": proposal.target.target_id,
            "name": proposal.target.name,
        },
        "changes": [_change_document(change) for change in proposal.changes],
        "evidence_ids": list(proposal.evidence_ids),
        "config_changed": False,
        "notice": proposal.notice,
    }


def _change_document(change: ProposalChange) -> dict[str, Any]:
    return {
        "field": change.field,
        "before": _typed_decimal(change.before),
        "after": _typed_decimal(change.after),
    }


def _typed_decimal(value: Decimal | None) -> dict[str, str | None]:
    return {
        "type": "unset" if value is None else "decimal",
        "value": None if value is None else str(value),
    }
