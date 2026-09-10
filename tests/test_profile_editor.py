"""Guided profile edits preserve the human-owned TOML around three scalars."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from auction_lens.config import (
    REMOVE,
    ProfileEdits,
    load_config,
    parse_config,
    profile_restore_recovery_path,
    profile_snapshot_path,
    recover_profile_restore,
    restore_profile_text,
    save_profile_text,
    update_profile_text,
)
from auction_lens.file_io import write_bytes_atomically
from support import EXAMPLE_CONFIG, temporary_directory


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


class ProfileSavingTests(unittest.TestCase):
    def test_snapshot_path_is_beside_and_unambiguously_named_for_its_config(self):
        self.assertEqual(
            profile_snapshot_path("config/local.toml"),
            profile_snapshot_path("config/local.toml").parent / "local.toml.previous",
        )

    def test_writable_profiles_require_the_toml_suffix_that_git_ignores(self):
        for path in ("config/prefs.conf", "config/prefs.TOML"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "requires a .toml"):
                    profile_snapshot_path(path)

    def test_an_accepted_edit_saves_exact_original_bytes_before_replacing(self):
        source = b'[logistics]\r\nlarge_item_policy = "ask"'
        proposed = b'[logistics]\r\nlarge_item_policy = "allow"'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            target.write_bytes(source)

            snapshot = save_profile_text(target, source, proposed)

            self.assertEqual(snapshot.read_bytes(), source)
            self.assertEqual(target.read_bytes(), proposed)

    def test_an_unchanged_proposal_writes_neither_profile_nor_snapshot(self):
        source = b'[logistics]\nlarge_item_policy = "ask"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            target.write_bytes(source)

            snapshot = save_profile_text(target, source, source)

            self.assertEqual(target.read_bytes(), source)
            self.assertFalse(snapshot.exists())

    def test_a_change_before_the_first_check_is_never_overwritten(self):
        previewed = b'[logistics]\nlarge_item_policy = "ask"\n'
        external = b'[logistics]\nlarge_item_policy = "reject"\n'
        proposed = b'[logistics]\nlarge_item_policy = "allow"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            target.write_bytes(external)

            with self.assertRaisesRegex(RuntimeError, "changed after the profile preview"):
                save_profile_text(target, previewed, proposed)

            self.assertEqual(target.read_bytes(), external)
            self.assertFalse(profile_snapshot_path(target).exists())

    def test_a_change_after_the_snapshot_is_detected_before_profile_write(self):
        source = b'[logistics]\nlarge_item_policy = "ask"\n'
        external = b'[logistics]\nlarge_item_policy = "reject"\n'
        proposed = b'[logistics]\nlarge_item_policy = "allow"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            target.write_bytes(source)

            def write_then_change(path, value):
                write_bytes_atomically(path, value)
                if path == snapshot:
                    target.write_bytes(external)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=write_then_change,
            ):
                with self.assertRaisesRegex(RuntimeError, "changed after the profile preview"):
                    save_profile_text(target, source, proposed)

            self.assertEqual(target.read_bytes(), external)
            self.assertEqual(snapshot.read_bytes(), source)

    def test_snapshot_failure_leaves_the_profile_untouched(self):
        source = b'[logistics]\nlarge_item_policy = "ask"\n'
        proposed = b'[logistics]\nlarge_item_policy = "allow"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            target.write_bytes(source)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=OSError("snapshot stopped"),
            ):
                with self.assertRaisesRegex(OSError, "snapshot stopped"):
                    save_profile_text(target, source, proposed)

            self.assertEqual(target.read_bytes(), source)
            self.assertFalse(profile_snapshot_path(target).exists())

    def test_profile_write_failure_leaves_original_and_usable_snapshot(self):
        source = b'[logistics]\nlarge_item_policy = "ask"\n'
        proposed = b'[logistics]\nlarge_item_policy = "allow"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            target.write_bytes(source)

            def fail_profile(path, value):
                if path == target:
                    raise OSError("profile stopped")
                write_bytes_atomically(path, value)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=fail_profile,
            ):
                with self.assertRaisesRegex(OSError, "profile stopped"):
                    save_profile_text(target, source, proposed)

            self.assertEqual(target.read_bytes(), source)
            self.assertEqual(snapshot.read_bytes(), source)

    def test_invalid_proposed_bytes_are_rejected_before_a_snapshot(self):
        source = b'[logistics]\nlarge_item_policy = "ask"\n'
        with temporary_directory() as directory:
            target = directory / "local.toml"
            target.write_bytes(source)

            with self.assertRaises(ValueError):
                save_profile_text(target, source, b"not = [valid")

            self.assertEqual(target.read_bytes(), source)
            self.assertFalse(profile_snapshot_path(target).exists())


class ProfileRestoreSavingTests(unittest.TestCase):
    SOURCE = b'[logistics]\nlarge_item_policy = "ask"\n'
    PREVIOUS = b'[logistics]\nlarge_item_policy = "allow"\n'

    def test_restore_exchanges_two_distinct_versions_and_removes_recovery(self):
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            recovery = profile_restore_recovery_path(target)
            target.write_bytes(self.SOURCE)
            snapshot.write_bytes(self.PREVIOUS)

            restore_profile_text(target, self.SOURCE, self.PREVIOUS)

            self.assertEqual(target.read_bytes(), self.PREVIOUS)
            self.assertEqual(snapshot.read_bytes(), self.SOURCE)
            self.assertFalse(recovery.exists())

    def test_failed_profile_replacement_preserves_both_distinct_versions(self):
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            recovery = profile_restore_recovery_path(target)
            target.write_bytes(self.SOURCE)
            snapshot.write_bytes(self.PREVIOUS)

            def fail_target(path, value):
                if path == target:
                    raise OSError("profile stopped")
                write_bytes_atomically(path, value)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=fail_target,
            ):
                with self.assertRaisesRegex(OSError, "profile stopped"):
                    restore_profile_text(target, self.SOURCE, self.PREVIOUS)

            self.assertEqual(target.read_bytes(), self.SOURCE)
            self.assertEqual(snapshot.read_bytes(), self.PREVIOUS)
            self.assertFalse(recovery.exists())

    def test_failed_snapshot_replacement_rolls_back_without_losing_either_version(self):
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            recovery = profile_restore_recovery_path(target)
            target.write_bytes(self.SOURCE)
            snapshot.write_bytes(self.PREVIOUS)

            def fail_snapshot(path, value):
                if path == snapshot:
                    raise OSError("snapshot stopped")
                write_bytes_atomically(path, value)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=fail_snapshot,
            ):
                with self.assertRaisesRegex(OSError, "snapshot stopped"):
                    restore_profile_text(target, self.SOURCE, self.PREVIOUS)

            self.assertEqual(target.read_bytes(), self.SOURCE)
            self.assertEqual(snapshot.read_bytes(), self.PREVIOUS)
            self.assertFalse(recovery.exists())

    def test_unfinished_rollback_retains_and_recovers_the_only_former_current_copy(self):
        with temporary_directory() as directory:
            target = directory / "local.toml"
            snapshot = profile_snapshot_path(target)
            recovery = profile_restore_recovery_path(target)
            target.write_bytes(self.SOURCE)
            snapshot.write_bytes(self.PREVIOUS)

            def fail_snapshot_and_rollback(path, value):
                if path == snapshot or (path == target and value == self.SOURCE):
                    raise OSError("rollback stopped")
                write_bytes_atomically(path, value)

            with patch(
                "auction_lens.config.editor.write_bytes_atomically",
                side_effect=fail_snapshot_and_rollback,
            ):
                with self.assertRaisesRegex(OSError, "rollback stopped"):
                    restore_profile_text(target, self.SOURCE, self.PREVIOUS)

            self.assertEqual(target.read_bytes(), self.PREVIOUS)
            self.assertEqual(snapshot.read_bytes(), self.PREVIOUS)
            self.assertEqual(recovery.read_bytes(), self.SOURCE)

            self.assertTrue(recover_profile_restore(target))
            self.assertEqual(target.read_bytes(), self.SOURCE)
            self.assertEqual(snapshot.read_bytes(), self.PREVIOUS)
            self.assertFalse(recovery.exists())


if __name__ == "__main__":
    unittest.main()
