"""Profile backups preserve both versions through failed or interrupted writes."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from auction_lens.config.profile_backup import (
    profile_restore_recovery_path,
    profile_snapshot_path,
    recover_profile_restore,
    restore_profile_text,
    save_profile_text,
)
from auction_lens.files import write_bytes_atomically
from support import temporary_directory


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
                "auction_lens.config.profile_backup.write_bytes_atomically",
                side_effect=write_then_change,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "changed after the profile preview"
                ):
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
                "auction_lens.config.profile_backup.write_bytes_atomically",
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
                "auction_lens.config.profile_backup.write_bytes_atomically",
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
                "auction_lens.config.profile_backup.write_bytes_atomically",
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
                "auction_lens.config.profile_backup.write_bytes_atomically",
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
                "auction_lens.config.profile_backup.write_bytes_atomically",
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
