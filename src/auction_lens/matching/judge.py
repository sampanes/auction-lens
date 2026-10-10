"""Optional local-model review of lots found by broad text matching.

Word matching finds candidates; this decides which of them are really the
thing. Splitting it that way is what lets the word lists go back to being
broad: a term no longer has to be precise, only inclusive, because something
downstream is now reading the title rather than scanning it for words.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol

from ..config.interests import InterestRule
from ..listings.model import Listing
from .model import Candidate, CandidateCategory

MODEL_DISCARD_WORDS = frozenset({"discard", "remove", "reject", "drop", "no"})
KEEP_LOADED = "10m"
SETTLED = {"temperature": 0, "top_p": 1}


@dataclass(frozen=True)
class Verdict:
    """One judgement, including the short explanation shown to a reader."""

    matches: bool
    why: str = ""

    @classmethod
    def matched(cls, why: str = "") -> Verdict:
        return cls(matches=True, why=why)

    @classmethod
    def set_aside(cls, why: str) -> Verdict:
        """Build a refusal. This is a constructor, not a question.

        Ask a verdict what it decided with `.matches`. Reading `.set_aside` on
        an instance hands back this bound method, which is always truthy, so a
        caller testing it sees every lot as refused. A test harness written
        that way once reported five different rule drafts as refusing all
        seven of their own real examples.
        """
        return cls(matches=False, why=why)


def verdict_from(answer: str) -> Verdict:
    """Read one model reply, treating only a clear discard as a mismatch."""
    try:
        decided = json.loads(answer)
    except (json.JSONDecodeError, TypeError):
        return Verdict.matched("the judge did not answer usably")
    if not isinstance(decided, dict):
        return Verdict.matched("the judge did not answer usably")
    why = str(decided.get("why", "")).strip()
    spoken = str(decided.get("verdict", "")).strip().lower()
    if spoken in MODEL_DISCARD_WORDS:
        return Verdict.set_aside(why)
    return Verdict.matched(why)


class ModelUnavailable(RuntimeError):
    """Nothing is serving the model, so this run cannot vet at all."""


class Judge(Protocol):
    """The one question vetting asks of whatever is doing the judging.

    Written down so the parameter can say what it wants instead of naming
    the class that happens to satisfy it today: a test double is a judge,
    and so would a second model be.
    """

    def verdict(self, instructions: str, subject: str) -> Verdict: ...


@dataclass(frozen=True)
class LocalModel:
    """A dependency-free client for one locally served Ollama model."""

    endpoint: str
    model: str
    timeout_seconds: int = 60

    @property
    def chat_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/api/chat"

    @property
    def tags_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/api/tags"

    def reachable(self) -> bool:
        """Check once before a run so an outage costs one failed request."""
        try:
            with urllib.request.urlopen(self.tags_url, timeout=self.timeout_seconds):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def verdict(self, instructions: str, subject: str) -> Verdict:
        """Ask one question, treating an unreadable answer as a match."""
        try:
            answer = self._ask(instructions, subject)
        except (urllib.error.URLError, OSError, TimeoutError) as problem:
            raise ModelUnavailable(str(problem)) from problem
        return verdict_from(answer)

    def _ask(self, instructions: str, subject: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": subject},
                ],
                "stream": False,
                "format": "json",
                "keep_alive": KEEP_LOADED,
                "options": SETTLED,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.chat_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.load(response)["message"]["content"]


# Keep/discard is the model's small reply vocabulary. Internally, a confident
# discard sets a lot aside by lowering its rank; it never deletes the lot.
SCREENING_RULES = """You are screening auction lots for one buyer. A person
reads whatever you keep, so keeping something poor is cheap and discarding
something good is expensive.

Reply "discard" ONLY when you are confident the lot is not what the buyer
wants: it is an accessory or part sold on its own with none of the actual
thing included, or it is plainly a different product that merely shares a
word with what is wanted.

Reply "keep" in every other case, including when:
- the lot is a bundle, kit, set or system that contains the thing
- it comes with extras, or is sold together with something else
- its brand, model, size or material is unfamiliar to you
- you are unsure

Ignore condition entirely: damage, wear, missing parts and "for parts only"
never make it the wrong thing.

When in doubt, keep it. Answer with JSON only:
{"verdict": "keep" or "discard", "why": "<10 words or fewer>"}"""


def instructions_for(rule: InterestRule) -> str:
    """Narrow the standing screening brief to one configured interest."""
    return f"{SCREENING_RULES}\n\nThe buyer wants: {rule.wants.strip()}"


def subject_of(listing: Listing) -> str:
    """Show the judge the title and the provider's short condition note."""
    title = " ".join(listing.title.split())
    lines = [f"Lot title: {title}"]
    notes = " ".join((listing.notes or "").split())
    if notes:
        lines.append(f"Condition notes: {notes[:300]}")
    return "\n".join(lines)


def is_judgeable(rule: InterestRule) -> bool:
    """Only judge an interest that says in words what it wants."""
    return bool(rule.wants.strip())

# What a set-aside lot's weight is multiplied by.
#
# The judge sets lots aside by down-weighting rather than deleting them. That
# distinction is the whole safety of the thing, and the measured error rate is
# why. Across one capture a 7B judge was asked about 41 wanted matches, refused
# 26, and was wrong about 6 of those refusals -- a powered hand tool called
# "not a tool", a full-size tank called "too small". That is one wrong answer in
# every seven asked and, worse, close to one in four of the refusals, which are
# the only answers anyone acts on. Deleting on that accuracy would reproduce the
# exact failure it was built to end: a lot gone from the report with nobody able
# to tell it was ever there.
#
# Half of those six were one reproducible model fault, and it is worth knowing
# because it looks like a judgement and is not. Given a rule wanting "a kayak, a
# canoe, a paddleboard or a dinghy", the judge refused every paddleboard with
# "not a kayak": it measures the lot against the first item of the list and
# reads the rest as decoration. Moving the refused word to the front of the same
# list turns those refusals into keeps. Instructing it not to do that did not
# help, across four separate drafts of this brief. So a rule offering
# alternatives should lead with a general phrase rather than with one of them,
# and a reader who sees "not a <first thing the rule lists>" should suspect this
# before suspecting the lot. A rule naming one thing only is the same fault at
# its smallest: with a single item there is nothing for the rest of the sentence
# to decorate, so a title using a synonym of that one word fails a comparison of
# words rather than a reading of products.
#
# The repair for that is to say the synonyms are one word, and it is specifically
# not to list what is unwanted. Measured on one interest over sixteen lots that
# its condition policy passes and eighteen accessories: the bare noun kept
# fifteen and leaked five, the same sentence ending in a list of parts that do
# not count kept only thirteen, and a version adding just the synonyms kept all
# sixteen and leaked none. The middle case is why the list hurts. A whole product
# advertises its own components, so a rule declaring a component unwanted teaches
# the judge to refuse the product that names one -- it rejected an entire machine
# for stating the sort of tyre it rides on, and another for stating that its
# battery comes out. Where a rule must exclude something, prefer `none_terms`,
# which is literal and settles it before the judge is paid at all.
#
# A rule offering two kinds of thing is the same fault again, and the cure is
# not prose. One rule here wanted either of two kinds and gave a measurement
# for each; over ninety-nine recorded lots it wanted, it refused thirty-two,
# nearly all of them the kind named second, because whichever kind is second
# is the decoration. Three rewrites moved that to twenty and then fourteen and
# no further. Splitting it into one rule per kind took the choice away from
# the judge entirely, and each kind's own terms then settled what the prose
# had been arguing about: they recalled every one of that kind's lots in the
# corpus and pulled in none of the fifteen hundred near-misses.
#
# Say only what qualifies. Naming a dimension puts it in the judge's attention
# even when the sentence says it does not matter. A draft listing the
# measurements a lot's own figures should not be judged against kept
# fifty-three of sixty-eight, refusing real lots for an unfamiliar brand and
# for a figure being too high -- both things that draft had declared
# irrelevant. Replacing each with "any of them counts" kept sixty-six. The
# negative form is a disqualifier the model will find a way to apply.
#
# Name alternative products; never name a specification. The two halves of
# that split rule measured in opposite directions, and the difference was what
# the names were. One kind goes by several product names, each a thing a
# seller might title it: listing those is what stops the judge refusing a lot
# that used one, worth sixty-eight of seventy-two against sixty-seven without.
# The other kind's alternative names were tokens out of its specification, and
# listing those cost it: naming the measurements kept thirteen of thirty-one,
# naming just the shorthand tokens kept thirty-two while refusing real
# examples for not stating one of them, and naming none kept all thirty-seven.
# A token in the sentence becomes one the judge demands of every title, and
# the rule's own terms already guarantee the title said one.
#
# Shorter is not the lesson either: cutting that rule down to "any size, any
# shape, any brand counts" fell back to thirty-four and began refusing on
# brand. Say what the thing is, then say what does not matter about it.
#
# Where a rule wants a consumable or a part in its own right, say so outright:
# the brief above discards "an accessory or part sold on its own", so a rule
# that leaves it unsaid is asking the judge to refuse exactly what it searched
# for. Naming each member affirmatively is what fixed such a rule here, in two
# passes, from five refusals in ten real lots down to none on a clean lot.
#
# The wording is probably not the lever. On ten cases chosen to include every
# known 7B mistake, a 32B model scored ten of ten against the 7B's five, twice,
# on this exact brief with nothing reworded, and kept every refusal the 7B
# already had right. That is a test built to be failed by the smaller model
# rather than a fair sample, so read it as "the faults are capacity" and not as
# an accuracy figure.
#
# Capacity is not free, and the limit is memory rather than patience. A 32B at
# four-bit quantization wants about 22 GB resident. Where that does not fit, the
# remainder is served from the CPU and throughput collapses out of all
# proportion to the shortfall: measured with roughly a third of the weights on
# an accelerator, a fifteen-token answer took over two minutes, which is an hour
# of judging for one ordinary run. So the useful question when a judge is
# refusing good lots is not "is there a bigger model" but "what is the largest
# model that fits entirely", and that is a fact about a machine rather than
# about this file. Model choice stays in config.
#
# Down-weighting degrades gently instead. Where a want has plenty of real lots,
# the set-aside ones fall below reports.most_per_interest and are never seen.
# Where it has almost none, they surface -- which is the case where a person
# would rather look at something doubtful than at nothing.
#
# Small enough that no set-aside lot can outrank any matched one. A reported
# candidate has already cleared its interest's minimum_score, so the worst
# real match scores at least 60 at the lightest weight in use (0.4, the
# catch-all) for a priority of 24, while the best possible set-aside lot reaches
# 100 at the heaviest weight (1.5) for 15. The gap makes setting a lot aside
# behave like exclusion whenever there is anything matched to show instead.
SET_ASIDE_WEIGHT = Decimal("0.1")


@dataclass(frozen=True)
class Judgement:
    """One verdict, stored beside enough of the lot to read it later."""

    rule_name: str
    title: str
    verdict: Verdict


@dataclass(frozen=True)
class VettingOutcome:
    """Every candidate after judging, and what was pushed to the bottom.

    Counted rather than derived, because the report is capped afterwards and a
    reader should still be able to see "asked about 130, set aside 40" once the
    visible list has been cut to a readable length.
    """

    candidates: tuple[Candidate, ...] = ()
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
    judge: Judge,
    *,
    workers: int = 4,
) -> VettingOutcome:
    """Set aside candidates classified as mismatches, without deleting them.

    A candidate is left alone when nothing about it can be judged: a lot
    reported on price rather than on want has no sentence to be measured
    against, and neither has an interest that never wrote one down.
    """
    wants = {rule.interest_id: rule for rule in rules if is_judgeable(rule)}
    askable = [candidate for candidate in candidates if _is_askable(candidate, wants)]
    if not askable:
        return VettingOutcome(candidates=tuple(candidates))

    try:
        verdicts = _ask_about_all(askable, wants, judge, workers)
    except ModelUnavailable as problem:
        # Nothing is touched. A judge that cannot be reached must not be able
        # to reorder a report, because a silently unranked report looks exactly
        # like a ranked one.
        return VettingOutcome(candidates=tuple(candidates), unavailable=str(problem))

    answered = dict(zip((id(c) for c in askable), verdicts, strict=True))
    return VettingOutcome(
        candidates=tuple(
            _apply_verdict(candidate, answered.get(id(candidate)))
            for candidate in candidates
        ),
        judgements=tuple(
            Judgement(candidate.rule_name, candidate.listing.title, verdict)
            for candidate, verdict in zip(askable, verdicts, strict=True)
        ),
        asked=len(askable),
    )


def _apply_verdict(candidate: Candidate, verdict: Verdict | None) -> Candidate:
    """Return a candidate unchanged or set aside and labelled.

    The reason travels with the lot. A word list that wrongly excluded
    something said nothing at all, so nobody could tell a mistake from an
    absence; a set-aside lot arrives in the report saying exactly what it was
    accused of, which is what makes the sentence above it fixable.
    """
    if verdict is None or verdict.matches:
        return candidate
    said = verdict.why or "not the thing this interest asked for"
    return replace(
        candidate,
        weight=candidate.weight * SET_ASIDE_WEIGHT,
        reasons=(*candidate.reasons, f"set aside by the judge: {said}"),
        set_aside=True,
    )


def _is_askable(candidate: Candidate, wants: dict[str, InterestRule]) -> bool:
    """Whether there is a written sentence to measure this candidate against."""
    return candidate.category is CandidateCategory.WANTED and candidate.rule_id in wants


def _ask_about_all(
    askable: list[Candidate],
    wants: dict[str, InterestRule],
    judge: Judge,
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
