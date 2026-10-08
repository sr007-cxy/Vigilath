"""Adapter registry: the module and class each engine id points to must really exist, display names must map one-to-one, and decommissioned ones must leave no trace.

Lesson from removing grok / copilot on 2026-09-20: ENGINE_MODULE_MAP is strings; deleting the file without deleting the entry
means _load_adapters at startup only logs a warning and skips it - the engine still shows on the dashboard while orders always fail.
"""

from __future__ import annotations

import importlib
from unittest import TestCase

from app.main import ENGINE_DISPLAY_NAMES, ENGINE_MODULE_MAP

RETIRED = {"grok", "copilot"}


class EngineModuleMapTests(TestCase):
    def test_retired_engines_gone(self):
        self.assertFalse(RETIRED & set(ENGINE_MODULE_MAP))
        self.assertFalse(RETIRED & set(ENGINE_DISPLAY_NAMES))

    def test_every_entry_resolves_to_a_real_class(self):
        for engine, spec in ENGINE_MODULE_MAP.items():
            module_path, cls_name = spec.split(":", 1)
            with self.subTest(engine=engine):
                mod = importlib.import_module(module_path)
                self.assertTrue(hasattr(mod, cls_name), f"{module_path} 里没有 {cls_name}")

    def test_display_names_cover_every_engine(self):
        # doubao_mobile reuses the Doubao adapter and is displayed as Doubao, without a separate name
        need = set(ENGINE_MODULE_MAP) - {"doubao_mobile"}
        self.assertEqual(need - set(ENGINE_DISPLAY_NAMES), set())
        self.assertEqual(set(ENGINE_DISPLAY_NAMES) - set(ENGINE_MODULE_MAP), set())
