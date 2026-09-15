"""Guided profile edits preserve the human-owned TOML around three scalars."""

from __future__ import annotations

import unittest
from decimal import Decimal

from auction_lens.config.load import load_config, parse_config
from auction_lens.config.profile_edit import REMOVE, ProfileEdits, update_profile_text
from support import EXAMPLE_CONFIG


class ConfigTextParsingTests(unittest.TestCase):
    def test_text_and_file_boundaries_share_one_config_builder(self):
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8")

        self.assertEqual(parse_config(text), load_config(EXAMPLE_CONFIG))


class ProfileTextEditingTests(unittest.TestCase):
    def test_three_existing_values_change_without_touching_surrounding_bytes(self):
        source = (
            '[provider]\r\nid = "generic"\n'
            "[logistics]\r\n"
            'large_item_policy   =   "ask"  # keep this explanation\r\n'
            "manual_handling_limit_lb = 75\n"
            "large_dimension_threshold_in = 60\r\n"
            'oversized_terms = ["special handling"]\n'
            "[unrelated]\r\nvalue = 9"
        )
        expected = source.replace(
            'large_item_policy   =   "ask"',
            'large_item_policy   =   "allow"',
        ).replace(
            "manual_handling_limit_lb = 75",
            "manual_handling_limit_lb = 80.5",
        ).replace(
            "large_dimension_threshold_in = 60",
            "large_dimension_threshold_in = 72",
        )

        updated = update_profile_text(
            source,
            ProfileEdits(
                large_item_policy="allow",
                manual_handling_limit_lb=Decimal("80.50"),
                large_dimension_threshold_in=Decimal("72.0"),
            ),
        )

        self.assertEqual(updated, expected)
        self.assertFalse(updated.endswith(("\r", "\n")))

    def test_a_missing_key_is_inserted_in_field_order_using_the_table_ending(self):
        source = (
            "[logistics]\r\n"
            'large_item_policy = "ask"\r\n'
            "# This comment stays with the dimension.\n"
            "large_dimension_threshold_in = 60\n"
            'oversized_terms = ["special handling"]\n'
            "[reports]\nmax_items = 10\n"
        )
        expected = source.replace(
            'large_item_policy = "ask"\r\n',
            'large_item_policy = "ask"\r\nmanual_handling_limit_lb = 85\r\n',
        )

        updated = update_profile_text(
            source,
            ProfileEdits(manual_handling_limit_lb=Decimal("85")),
        )

        self.assertEqual(updated, expected)

    def test_missing_fields_are_inserted_in_schema_order(self):
        source = (
            "[logistics]\n"
            "large_dimension_threshold_in = 60\n"
            'oversized_terms = ["special handling"]\n'
        )

        updated = update_profile_text(
            source,
            ProfileEdits(
                large_item_policy="allow",
                manual_handling_limit_lb=Decimal("90"),
            ),
        )

        self.assertEqual(
            updated,
            "[logistics]\n"
            'large_item_policy = "allow"\n'
            "manual_handling_limit_lb = 90\n"
            "large_dimension_threshold_in = 60\n"
            'oversized_terms = ["special handling"]\n',
        )

    def test_a_missing_table_is_appended_without_inventing_a_final_newline(self):
        source = '[provider]\nid = "generic"'

        updated = update_profile_text(
            source,
            ProfileEdits(manual_handling_limit_lb=Decimal("82")),
        )

        self.assertEqual(
            updated,
            '[provider]\nid = "generic"\n\n'
            "[logistics]\n"
            "manual_handling_limit_lb = 82",
        )
        self.assertEqual(parse_config(updated).logistics.manual_handling_limit_lb, 82)

    def test_replacing_the_last_line_preserves_crlf_and_no_final_newline(self):
        source = "[logistics]\r\nmanual_handling_limit_lb = 75"

        updated = update_profile_text(
            source,
            ProfileEdits(manual_handling_limit_lb=Decimal("76")),
        )

        self.assertEqual(updated, "[logistics]\r\nmanual_handling_limit_lb = 76")

    def test_default_removes_an_assignment_but_retains_its_inline_comment(self):
        source = (
            "[logistics]\n"
            '  large_item_policy = "allow"  # Reconsider this when transport changes.\n'
            "manual_handling_limit_lb = 80\n"
        )

        updated = update_profile_text(
            source,
            ProfileEdits(large_item_policy=REMOVE),
        )

        self.assertEqual(
            updated,
            "[logistics]\n"
            "  # Reconsider this when transport changes.\n"
            "manual_handling_limit_lb = 80\n",
        )
        self.assertEqual(parse_config(updated).logistics.large_item_policy, "ask")

    def test_removing_the_last_assignment_preserves_no_final_newline(self):
        source = "[logistics]\nmanual_handling_limit_lb = 80"

        updated = update_profile_text(
            source,
            ProfileEdits(manual_handling_limit_lb=REMOVE),
        )

        self.assertEqual(updated, "[logistics]")

    def test_keep_same_value_and_remove_missing_value_are_byte_exact_no_ops(self):
        source = '[provider]\r\nid = "generic"'
        examples = (
            ProfileEdits(),
            ProfileEdits(large_item_policy="ask"),
            ProfileEdits(manual_handling_limit_lb=Decimal("75")),
            ProfileEdits(large_dimension_threshold_in=REMOVE),
        )

        for edits in examples:
            with self.subTest(edits=edits):
                self.assertEqual(update_profile_text(source, edits), source)

    def test_noncanonical_or_lexically_ambiguous_toml_is_refused(self):
        sources = (
            '[logistics]\n"large_item_policy" = "ask"\n',
            'logistics.large_item_policy = "ask"\n',
            'logistics = { large_item_policy = "ask" }\n',
            '[logistics]\nlarge_item_policy = """ask"""\n',
            '[notes]\nvalue = """\n[logistics]\nlarge_item_policy = "ask"\n"""\n',
        )

        for source in sources:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "cannot safely update"):
                    update_profile_text(source, ProfileEdits(large_item_policy="allow"))

    def test_an_unrelated_multiline_value_is_preserved_when_the_target_is_clear(self):
        source = (
            '[notes]\nvalue = """first line\nsecond line"""\n'
            "[logistics]\n"
            'large_item_policy = "ask"\n'
        )

        updated = update_profile_text(source, ProfileEdits(large_item_policy="allow"))

        self.assertEqual(
            updated,
            source.replace('large_item_policy = "ask"', 'large_item_policy = "allow"'),
        )

    def test_invalid_edits_are_rejected_before_text_changes(self):
        source = '[logistics]\nlarge_item_policy = "ask"\n'
        examples = (
            ProfileEdits(large_item_policy="sometimes"),
            ProfileEdits(manual_handling_limit_lb=Decimal("-1")),
            ProfileEdits(large_dimension_threshold_in=Decimal("NaN")),
            ProfileEdits(manual_handling_limit_lb="80"),  # type: ignore[arg-type]
        )

        for edits in examples:
            with self.subTest(edits=edits):
                with self.assertRaises(ValueError):
                    update_profile_text(source, edits)


if __name__ == "__main__":
    unittest.main()
