"""The stage that puts every wanted lot in front of the judge.

Word matching finds candidates; this decides which of them are really the
thing. Splitting it that way is what lets the word lists go back to being
broad: a term no longer has to be precise, only inclusive, because something
downstream is now reading the title rather than scanning it for words.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal

from ..config import InterestRule
from ..models import Candidate, CandidateCategory
from .model import ModelUnavailable
from .questions import instructions_for, is_judgeable, subject_of
from .verdicts import Verdict

# What a set-aside lot's weight is multiplied by.
#
# The judge sinks lots rather than deleting them, and the difference is the
# whole safety of the thing. Measured against a real capture it discards
# something good about one time in fifteen -- a plainly titled metal shed as
# "wrong material", a hedge trimmer as "not a laser level". Deleting on that
# accuracy would reproduce the exact failure it was built to end: a lot gone
# from the report with nobody able to tell it was ever there.
#
# Sinking degrades gently instead. Where a want has plenty of real lots, the
# set-aside ones fall below reports.most_per_interest and are never seen. Where
# it has almost none, they surface -- which is the case where a person would
# rather look at something doubtful than at nothing.
#
# Small enough that no set-aside lot can outrank any kept one. A reported
# candidate has already cleared its interest's minimum_score, so the worst
# real match scores at least 60 at the lightest weight in use (0.4, the
# catch-all) for a priority of 24, while the best possible sunk lot reaches
# 100 at the heaviest weight (1.5) for 15. The gap is what keeps sinking a
# lot indistinguishable from removing it whenever there is anything real to
# show instead.
SET_ASIDE = Decimal("0.1")


@dataclass(frozen=True)
class Judgement:
    """One verdict, kept beside enough of the lot to read it later."""

    rule_name: str
    title: str
    verdict: Verdict


@dataclass(frozen=True)
class VettingOutcome:
    """What one pass of judging kept, and what it pushed to the bottom.

    Counted rather than derived, because the report is capped afterwards and a
    reader should still be able to see "asked about 130, set aside 40" once the
    visible list has been cut to a readable length.
    """

    kept: tuple[Candidate, ...] = ()
    judgements: tuple[Judgement, ...] = ()
    asked: int = 0
    unavailable: str = ""

    @property
    def set_aside(self) -> int:
        """How many lots the judge pushed to the bottom of the report."""
        return sum(1 for judged in self.judgements if not judged.verdict.matches)

    @property
    def ran(self) -> bool:
        """Whether judging happened, as opposed to being skipped or unreachable."""
        return not self.unavailable and self.asked > 0


def vet(
    candidates: list[Candidate],
    rules: tuple[InterestRule, ...],
    judge,
    *,
    workers: int = 4,
) -> VettingOutcome:
    """Sink the candidates the judge rejects, and say why it rejected them.

    A candidate is left alone when nothing about it can be judged: a lot
    reported on price rather than on want has no sentence to be measured
    against, and neither has an interest that never wrote one down.
    """
    wants = {rule.interest_id: rule for rule in rules if is_judgeable(rule)}
    askable = [candidate for candidate in candidates if _is_askable(candidate, wants)]
    if not askable:
        return VettingOutcome(kept=tuple(candidates))

    try:
        verdicts = _ask_about_all(askable, wants, judge, workers)
    except ModelUnavailable as problem:
        # Nothing is touched. A judge that cannot be reached must not be able
        # to reorder a report, because a silently unranked report looks exactly
        # like a ranked one.
        return VettingOutcome(kept=tuple(candidates), unavailable=str(problem))

    answered = dict(zip((id(c) for c in askable), verdicts, strict=True))
    return VettingOutcome(
        kept=tuple(
            _settled(candidate, answered.get(id(candidate)))
            for candidate in candidates
        ),
        judgements=tuple(
            Judgement(candidate.rule_name, candidate.listing.title, verdict)
            for candidate, verdict in zip(askable, verdicts, strict=True)
        ),
        asked=len(askable),
    )


def _settled(candidate: Candidate, verdict: Verdict | None) -> Candidate:
    """The candidate as the judge left it: untouched, or sunk and labelled.

    The reason travels with the lot. A word list that wrongly excluded
    something said nothing at all, so nobody could tell a mistake from an
    absence; a sunk lot arrives in the report saying exactly what it was
    accused of, which is what makes the sentence above it fixable.
    """
    if verdict is None or verdict.matches:
        return candidate
    said = verdict.why or "not the thing this interest asked for"
    return replace(
        candidate,
        weight=candidate.weight * SET_ASIDE,
        reasons=(*candidate.reasons, f"set aside by the judge: {said}"),
    )


def _is_askable(candidate: Candidate, wants: dict[str, InterestRule]) -> bool:
    """Whether there is a written sentence to measure this candidate against."""
    return candidate.category is CandidateCategory.WANTED and candidate.rule_id in wants


def _ask_about_all(
    askable: list[Candidate],
    wants: dict[str, InterestRule],
    judge,
    workers: int,
) -> list[Verdict]:
    """Every verdict, asking each distinct question only once.

    A capture repeats itself -- the same Dell monitor appears twice in one
    page, and a lot can match two interests -- so questions are keyed by the
    rule asking and the text being read. Identical questions get one answer.
    """
    questions = [
        (wants[candidate.rule_id], subject_of(candidate.listing))
        for candidate in askable
    ]
    distinct = {
        (rule.interest_id, subject): (rule, subject) for rule, subject in questions
    }
    unique = list(distinct.values())

    answers: dict[tuple[str, str], Verdict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        asked = [
            pool.submit(judge.verdict, instructions_for(rule), subject)
            for rule, subject in unique
        ]
        for (rule, subject), pending in zip(unique, asked, strict=True):
            answers[(rule.interest_id, subject)] = pending.result()

    return [answers[(rule.interest_id, subject)] for rule, subject in questions]
