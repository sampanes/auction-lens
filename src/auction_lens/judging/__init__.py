"""Judging: whether a lot is really the thing an interest asked for.

Word matching answers "does this say the word". That is a different question
from "is this the thing", and every attempt to close the gap with more words
-- exclusions, accessory nouns, positional rules -- answered it only for the
cases somebody had already been bitten by. This asks the question directly.
"""

from .model import LocalModel, ModelUnavailable
from .questions import instructions_for, is_judgeable, subject_of
from .verdicts import Verdict, verdict_from
from .vetting import Judgement, VettingOutcome, vet

__all__ = [
    "Judgement",
    "LocalModel",
    "ModelUnavailable",
    "Verdict",
    "VettingOutcome",
    "instructions_for",
    "is_judgeable",
    "subject_of",
    "verdict_from",
    "vet",
]
