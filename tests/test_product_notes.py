"""Researched product knowledge reaching a card without being fetched.

The point of these is the boundary. A note is words a person wrote, so it has
to arrive on every card for the product and change no number on any of them.
Two failures would matter and neither would be loud: a note quietly scoring a
lot, and a note configured but never shown because nothing could match it.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config.load import parse_config
from auction_lens.config.notes import ProductNote
from auction_lens.history.observations import ObservationChange
from auction_lens.matching.model import Candidate, CandidateCategory
from auction_lens.matching.notes import notes_for
from auction_lens.reports.findings import build_report
from auction_lens.reports.html import render_html
from auction_lens.reports.text import render_text
from support import REPORT_ZONE, SOUNDBAR, example_listings

_PROVIDER = """
[provider]
provider_id = "nellis"

[[interests]]
name = "example"
wants = "an example"
any_terms = ["example"]
"""

_NOTE = """
[[product_notes]]
any_terms = ["acme", "acme garden"]
note = \"\"\"
    Owners report the deck belt slipping on wet grass. Check it before bidding.
\"\"\"
"""


NO_CHANGE = ObservationChange(is_new=False, price_changed=False)


def _listing(title):
    return replace(example_listings()[SOUNDBAR], title=title)


def _candidate(listing, known=()):
    return Candidate(
        listing=listing,
        category=CandidateCategory.WANTED,
        rule_id="example",
        rule_name="example",
        score=80,
        total_cost=Decimal("25"),
        retail_ratio=Decimal("0.2"),
        reasons=("matched example",),
        change=NO_CHANGE,
        known=known,
    )


class ConfiguredNoteTests(unittest.TestCase):
    """Loading the section, and refusing the shapes that cannot be seen."""

    def test_a_note_is_read_with_its_terms_lowered_and_text_stripped(self):
        config = parse_config(_PROVIDER + _NOTE)

        (note,) = config.product_notes
        self.assertEqual(note.any_terms, ("acme", "acme garden"))
        self.assertTrue(note.note.startswith("Owners report"))
        self.assertTrue(note.note.endswith("before bidding."))

    def test_a_file_with_no_notes_section_is_perfectly_valid(self):
        config = parse_config(_PROVIDER)

        self.assertEqual(config.product_notes, ())

    def test_a_note_with_no_terms_is_refused_rather_than_silently_unseen(self):
        # It would load, never match, and read to whoever wrote the file as
        # though the product were covered.
        with self.assertRaises(ValueError) as caught:
            parse_config(
                _PROVIDER
                + '\n[[product_notes]]\nany_terms = []\nnote = "something"\n'
            )

        self.assertIn("any_terms", str(caught.exception))

    def test_a_note_with_no_text_is_refused(self):
        with self.assertRaises(ValueError):
            parse_config(_PROVIDER + '\n[[product_notes]]\nany_terms = ["acme"]\n')


class NoteMatchingTests(unittest.TestCase):
    """Which titles carry which notes, by the same rules a term follows."""

    def _notes_on(self, title, notes=None):
        if notes is None:
            notes = parse_config(_PROVIDER + _NOTE).product_notes
        return notes_for(_listing(title), notes)

    def test_a_title_naming_the_product_carries_the_note(self):
        (note,) = self._notes_on("ACME 21 Inch Electric Lawn Mower")

        self.assertIn("deck belt slipping", note)

    def test_a_title_not_naming_it_carries_nothing(self):
        self.assertEqual(self._notes_on("Generic 21 Inch Electric Lawn Mower"), ())

    def test_the_term_has_to_stand_on_its_own_as_a_word(self):
        # The same whole-phrase rule the interest terms use, so a note and a
        # rule cannot disagree about what a title says.
        self.assertEqual(self._notes_on("Acmetastic Mower Blades"), ())

    def test_matching_ignores_the_case_the_warehouse_shouted_in(self):
        self.assertNotEqual(self._notes_on("*** ACME *** 21 INCH MOWER"), ())

    def test_two_notes_about_one_product_are_both_carried_in_order(self):
        notes = (
            ProductNote(any_terms=("acme",), note="first"),
            ProductNote(any_terms=("mower",), note="second"),
        )

        self.assertEqual(self._notes_on("Acme Mower", notes), ("first", "second"))

    def test_one_note_is_shown_once_however_many_of_its_terms_match(self):
        notes = (ProductNote(any_terms=("acme", "mower"), note="only once"),)

        self.assertEqual(self._notes_on("Acme Mower", notes), ("only once",))


    def test_a_required_term_keeps_a_makers_name_off_its_other_products(self):
        # Keyed on the name alone, a note about one maker's mowers would also
        # reach its leaf blowers and hose reels.
        notes = (
            ProductNote(
                any_terms=("acme",), all_terms=("mower",), note="about the mower"
            ),
        )

        self.assertEqual(
            self._notes_on("Acme 21 Inch Electric Lawn Mower", notes), ("about the mower",)
        )
        self.assertEqual(self._notes_on("Acme Cordless Leaf Blower", notes), ())
        self.assertEqual(self._notes_on("Acme Expandable Hose Reel", notes), ())

    def test_every_required_term_has_to_appear_not_just_one(self):
        notes = (
            ProductNote(
                any_terms=("acme",),
                all_terms=("mower", "electric"),
                note="about the electric mower",
            ),
        )

        self.assertEqual(
            self._notes_on("Acme 21 Inch Electric Lawn Mower", notes),
            ("about the electric mower",),
        )
        self.assertEqual(self._notes_on("Acme 21 Inch Gas Lawn Mower", notes), ())

    def test_a_note_with_no_required_terms_is_unrestricted(self):
        notes = (ProductNote(any_terms=("acme",), note="anything acme"),)

        self.assertEqual(self._notes_on("Acme Leaf Blower", notes), ("anything acme",))


class RenderedNoteTests(unittest.TestCase):
    """What a reader sees, and what the note must not touch."""

    def _report(self, known=()):
        listing = _listing("Acme 21 Inch Electric Lawn Mower")
        candidate = _candidate(listing, known=known)
        return build_report([candidate], REPORT_ZONE)

    def test_the_text_report_labels_a_note_apart_from_the_scoring(self):
        report = self._report(known=("Owners report the deck belt slipping.",))

        rendered = render_text(report)
        self.assertIn("Known: Owners report the deck belt slipping.", rendered)
        self.assertIn("Why: matched example", rendered)

    def test_a_multi_line_note_is_printed_as_one_line(self):
        # TOML triple quotes are how a readable note gets written, and the
        # indentation they carry is not something a report should show.
        report = self._report(known=("first line\n    second line",))

        self.assertIn("Known: first line second line", render_text(report))

    def test_the_html_report_carries_the_same_note(self):
        report = self._report(known=("Owners report the deck belt slipping.",))

        rendered = render_html(report)
        self.assertIn("Known", rendered)
        self.assertIn("Owners report the deck belt slipping.", rendered)

    def test_a_card_with_no_note_gains_no_empty_label(self):
        rendered = render_text(self._report())

        self.assertNotIn("Known", rendered)

    def test_the_html_card_with_no_note_gains_no_empty_list(self):
        self.assertNotIn("Known", render_html(self._report()))

    def test_a_note_leaves_the_score_and_the_total_exactly_alone(self):
        without = self._report().findings[0]
        with_note = self._report(known=("anything at all",)).findings[0]

        self.assertEqual(with_note.score, without.score)
        self.assertEqual(
            with_note.facts.total_cost,
            without.facts.total_cost,
            "a note is words, and words do not price a lot",
        )
        self.assertEqual(with_note.reasons, without.reasons)


if __name__ == "__main__":
    unittest.main()
