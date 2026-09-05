"""Choosing the adapter one configured source asked for.

Built-in names cover the mechanisms most sources need. An import path is the
escape hatch for an unusual API, and keeps its authentication, parsing, and
tests outside this project.
"""

from __future__ import annotations

from importlib import import_module

from ..config import ValuationSourceConfig
from .base import ValuationAdapter
from .http_json import HttpJsonAdapter
from .reference import ReferenceAdapter
from .xml_catalog import XmlCatalogAdapter

BUILTIN_ADAPTERS = {
    "reference": ReferenceAdapter,
    "xml_catalog": XmlCatalogAdapter,
    "http_json": HttpJsonAdapter,
}

IMPORT_PATH_SEPARATOR = ":"


def create_adapter(config: ValuationSourceConfig) -> ValuationAdapter:
    """Resolve a built-in adapter, or an import path such as package.module:Adapter."""
    factory = BUILTIN_ADAPTERS.get(config.adapter)
    if factory is None:
        factory = _imported_factory(config.adapter)
    return factory(config)


def _imported_factory(adapter: str):
    if IMPORT_PATH_SEPARATOR not in adapter:
        choices = ", ".join(sorted(BUILTIN_ADAPTERS))
        raise ValueError(f"unknown valuation adapter {adapter!r}; built-ins: {choices}")
    module_name, attribute = adapter.split(IMPORT_PATH_SEPARATOR, 1)
    return getattr(import_module(module_name), attribute)
