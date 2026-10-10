"""Provider ids resolve explicitly before any provider-specific work begins."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout

from auction_lens import collect, daily, doctor
from auction_lens.providers.nellis.discover import (
    check_discovery_ready,
    discover_searches,
)
from auction_lens.providers.nellis.parse import read_saved_page, read_search_page
from auction_lens.providers.registry import (
    HTML_PAGES,
    JSON_RECORDS,
    ProviderAdapter,
    resolve_provider,
)
from auction_lens.providers.vine import read as vine
from support import EXAMPLE_CONFIG, temporary_directory

# Written out rather than built from the registry, so adding a provider has to
# be admitted here too. The refusal names every id an operator may configure.
UNSUPPORTED_ERROR = (
    "unsupported provider id 'unsupported'; supported provider ids: nellis, vine"
)


class ProviderRegistryTests(unittest.TestCase):
    def test_an_unsupported_provider_id_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, UNSUPPORTED_ERROR):
            resolve_provider("unsupported")

    def test_nellis_binds_every_provider_specific_operation_explicitly(self):
        adapter = resolve_provider("nellis")

        self.assertIs(type(adapter), ProviderAdapter)
        self.assertIs(adapter.discover_searches, discover_searches)
        self.assertIs(adapter.check_discovery_ready, check_discovery_ready)
        self.assertIs(adapter.read_search_page, read_search_page)
        self.assertIs(adapter.read_saved_page, read_saved_page)
        self.assertEqual(adapter.saved_page_suffix, HTML_PAGES)

    def test_vine_binds_a_reader_to_every_path_and_a_refusal_to_discovery(self):
        adapter = resolve_provider("vine")

        self.assertIs(type(adapter), ProviderAdapter)
        self.assertIs(adapter.discover_searches, vine.discover_searches)
        self.assertIs(adapter.check_discovery_ready, vine.check_discovery_ready)
        self.assertIs(adapter.read_search_page, vine.read_records)
        self.assertIs(adapter.read_saved_page, vine.read_records)
        self.assertEqual(adapter.saved_page_suffix, JSON_RECORDS)

    def test_provider_commands_reject_an_unknown_id_before_output_or_state(self):
        commands = (collect.discover, daily.daily, collect.pull, doctor.doctor)
        with temporary_directory() as directory:
            config = directory / "unsupported.toml"
            config.write_text(
                EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
                    'id = "nellis"', 'id = "unsupported"', 1
                ),
                encoding="utf-8",
            )
            original = config.read_bytes()

            for command in commands:
                with self.subTest(command=command.__name__):
                    output = io.StringIO()
                    args = argparse.Namespace(config=str(config), visiting=[])
                    with redirect_stdout(output):
                        with self.assertRaisesRegex(ValueError, UNSUPPORTED_ERROR):
                            command(args)
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(tuple(directory.iterdir()), (config,))
                    self.assertEqual(config.read_bytes(), original)
