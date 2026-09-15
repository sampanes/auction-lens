"""Judging: whether a lot is really the thing an interest asked for.

Nothing here needs a model running. The judge is an interface with one method,
so the tests supply their own and the questions become inspectable data.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config.interests import InterestRule
from auction_lens.listings.model import ObservationChange
from auction_lens.matching.judge import (
    LocalModel,
    ModelUnavailable,
    Verdict,
    instructions_for,
    is_judgeable,
    subject_of,
    verdict_from,
    vet,
)
from auction_lens.matching.model import Candidate, CandidateCategory
from support import SOUNDBAR, example_listings

MONITOR = InterestRule(
    name="monitor",
    any_terms=("monitor",),
    wants="a computer display panel you plug into a PC",
)


class RecordingJudge:
    """A judge whose answers the test writes, and that remembers being asked."""

    def __init__(self, answers=None, default=None):
        self.answers = answers or {}
        self.default = default or Verdict.kept()
        self.asked = []

    def verdict(self, instructions: str, subject: str) -> Verdict:
        self.asked.append((instructions, subject))
        for fragment, answer in self.answers.items():
            if fragment.lower() in subject.lower():
                return answer
        return self.default


class RefusingJudge:
    """A judge that cannot be reached at all."""

    def verdict(self, instructions: str, subject: str) -> Verdict:
        raise ModelUnavailable("connection refused")


def a_candidate(title: str, rule: InterestRule = MONITOR, **changes) -> Candidate:
    """One wanted candidate carrying a listing with the given title."""
    listing = replace(example_listings()[SOUNDBAR], title=title)
    fields = {
        "listing": listing,
        "category": CandidateCategory.WANTED,
        "rule_id": rule.interest_id,
        "rule_name": rule.name,
        "score": 80,
        "total_cost": Decimal("10"),
        "retail_ratio": None,
        "reasons": (),
        "change": ObservationChange(is_new=False, price_changed=False),
    }
    return Candidate(**{**fields, **changes})


class QuestionTests(unittest.TestCase):
    def test_the_question_carries_the_sentence_the_interest_wrote(self):
        self.assertIn("a computer display panel", instructions_for(MONITOR))

    def test_an_interest_with_nothing_written_is_not_judgeable(self):
        self.assertFalse(is_judgeable(replace(MONITOR, wants="   ")))

    def test_the_lot_is_described_by_its_title(self):
        listing = replace(example_listings()[SOUNDBAR], title="LG 32in 4K Monitor")
        self.assertIn("LG 32in 4K Monitor", subject_of(listing))

    def test_condition_notes_are_shown_because_a_title_omits_them(self):
        listing = replace(
            example_listings()[SOUNDBAR], title="LG Monitor", notes="parts only"
        )
        self.assertIn("parts only", subject_of(listing))

    def test_a_note_written_across_several_lines_arrives_as_one(self):
        # People type these into a box over several visits, and where they
        # pressed Enter must not decide what the judge reads.
        listing = replace(
            example_listings()[SOUNDBAR],
            title="Inflatable Water Slide",
            notes="9/8 blower\nnot included\nleaks air",
        )
        self.assertIn("blower not included leaks air", subject_of(listing))


class AnswerTests(unittest.TestCase):
    def test_a_discard_is_read_with_its_reason(self):
        answer = verdict_from('{"verdict": "discard", "why": "baby monitor"}')
        self.assertFalse(answer.matches)
        self.assertEqual(answer.why, "baby monitor")

    def test_a_lot_the_judge_leaves_alone_is_kept(self):
        self.assertTrue(verdict_from('{"verdict": "keep", "why": "a monitor"}').matches)

    def test_a_synonym_for_discarding_is_understood(self):
        # A model told to say "discard" sometimes says "remove". Refusing to
        # understand that would silently keep everything it meant to throw out.
        self.assertFalse(verdict_from('{"verdict": "remove"}').matches)

    def test_a_word_nobody_recognises_keeps_the_lot(self):
        self.assertTrue(verdict_from('{"verdict": "perhaps"}').matches)

    def test_an_unreadable_answer_keeps_the_lot(self):
        self.assertTrue(verdict_from("I think maybe?").matches)

    def test_an_answer_missing_the_decision_keeps_the_lot(self):
        self.assertTrue(verdict_from('{"why": "unsure"}').matches)

    def test_the_judge_is_asked_what_to_discard_not_what_to_keep(self):
        # The direction is the design: an unsure judge must leave lots alone.
        self.assertIn("discard", instructions_for(MONITOR))
        self.assertIn("When in doubt, keep it", instructions_for(MONITOR))


class VettingTests(unittest.TestCase):
    def test_a_lot_the_judge_accepts_survives(self):
        kept = vet([a_candidate("LG 32in 4K Monitor")], (MONITOR,), RecordingJudge())
        self.assertEqual(len(kept.kept), 1)

    def test_a_lot_the_judge_refuses_sinks_rather_than_vanishing(self):
        judge = RecordingJudge({"baby": Verdict.dropped("a baby monitor")})
        outcome = vet([a_candidate("VTech Baby Monitor")], (MONITOR,), judge)
        self.assertEqual(len(outcome.kept), 1)
        self.assertEqual(outcome.set_aside, 1)
        self.assertLess(outcome.kept[0].weight, Decimal("1"))

    def test_a_sunk_lot_says_what_it_was_accused_of(self):
        judge = RecordingJudge({"baby": Verdict.dropped("a baby monitor")})
        outcome = vet([a_candidate("VTech Baby Monitor")], (MONITOR,), judge)
        self.assertIn("set aside by the judge: a baby monitor", outcome.kept[0].reasons)

    def test_a_sunk_lot_can_never_outrank_a_kept_one(self):
        judge = RecordingJudge({"baby": Verdict.dropped("a baby monitor")})
        # The worst real lot that can reach a report against the best sunk
        # one. A candidate has cleared its minimum_score to get here, so 60
        # at the lightest weight in use is the floor of what is possible.
        real = a_candidate("LG Monitor", score=60, weight=Decimal("0.4"))
        sunk = a_candidate("VTech Baby Monitor", score=100, weight=Decimal("1.5"))
        outcome = vet([real, sunk], (MONITOR,), judge)
        by_title = {c.listing.title: c for c in outcome.kept}
        self.assertGreater(
            by_title["LG Monitor"].priority, by_title["VTech Baby Monitor"].priority
        )

    def test_the_reason_for_a_refusal_is_kept_so_a_mistake_can_be_read(self):
        judge = RecordingJudge({"baby": Verdict.dropped("a baby monitor")})
        outcome = vet([a_candidate("VTech Baby Monitor")], (MONITOR,), judge)
        self.assertEqual(outcome.judgements[0].verdict.why, "a baby monitor")

    def test_a_lot_reported_on_price_alone_is_never_asked_about(self):
        priced = a_candidate("Anything", category=CandidateCategory.ANOMALY)
        judge = RecordingJudge()
        outcome = vet([priced], (MONITOR,), judge)
        self.assertEqual(judge.asked, [])
        self.assertEqual(len(outcome.kept), 1)

    def test_an_interest_that_wrote_no_sentence_is_never_asked_about(self):
        silent = replace(MONITOR, wants="")
        judge = RecordingJudge()
        outcome = vet([a_candidate("LG Monitor", silent)], (silent,), judge)
        self.assertEqual(judge.asked, [])
        self.assertEqual(len(outcome.kept), 1)

    def test_an_unreachable_judge_keeps_everything_rather_than_emptying_the_report(self):
        outcome = vet([a_candidate("LG Monitor")], (MONITOR,), RefusingJudge())
        self.assertEqual(len(outcome.kept), 1)
        self.assertFalse(outcome.ran)
        self.assertIn("refused", outcome.unavailable)

    def test_the_same_question_is_asked_once_however_often_it_repeats(self):
        judge = RecordingJudge()
        same = [a_candidate("Dell 27 QHD Monitor") for _ in range(3)]
        outcome = vet(same, (MONITOR,), judge)
        self.assertEqual(len(judge.asked), 1)
        self.assertEqual(len(outcome.kept), 3)
        self.assertEqual(outcome.asked, 3)

    def test_a_note_can_rule_a_lot_out_because_the_judge_reads_it(self):
        # The seller said the fan is missing, which no title would ever say.
        # This is what the warehouse-note exclusions used to do by keyword.
        judge = RecordingJudge({"blower not included": Verdict.dropped("no blower")})
        candidate = a_candidate("Inflatable Water Slide Bounce House")
        candidate = replace(
            candidate,
            listing=replace(candidate.listing, notes="9/8 blower not included"),
        )
        self.assertEqual(vet([candidate], (MONITOR,), judge).set_aside, 1)

    def test_two_interests_asking_about_one_lot_are_two_questions(self):
        other = InterestRule(name="tv", any_terms=("tv",), wants="a television")
        judge = RecordingJudge()
        vet(
            [a_candidate("Smart TV Monitor"), a_candidate("Smart TV Monitor", other)],
            (MONITOR, other),
            judge,
        )
        self.assertEqual(len(judge.asked), 2)


class ReachabilityTests(unittest.TestCase):
    def test_a_server_that_is_not_there_is_reported_as_unreachable(self):
        # Port 1 is reserved and never listening, so this needs no fixture.
        model = LocalModel(endpoint="http://localhost:1", model="x", timeout_seconds=1)
        self.assertFalse(model.reachable())

    def test_the_endpoint_is_read_without_a_trailing_slash_confusing_it(self):
        model = LocalModel(endpoint="http://localhost:11434/", model="x")
        self.assertEqual(model.chat_url, "http://localhost:11434/api/chat")


if __name__ == "__main__":
    unittest.main()
