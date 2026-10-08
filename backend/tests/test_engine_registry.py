"""The backend's engine tables must agree with each other, and retired engines must be gone everywhere.

Lesson from removing grok / copilot on 2026-09-20: an engine's identity is scattered across a dozen-plus places (master TODO K27),
and missing one means "validation passes, nobody downstream recognizes it". Until it's consolidated into one contract, pin the backend tables with tests first;
telemetry has the corresponding tests/test_engine_tables.py.
"""

from __future__ import annotations

import pytest

from browser_engine.client import ENGINE_ROUTING
from geo.api.ai_telemetry_reports import _ENGINE_ALIAS_DEFAULT
from geo.models.ai_telemetry import VALID_ENGINES
from geo.services.full_diagnosis_competitive import _DEFAULT_ENGINE_ALIAS

RETIRED = {"grok", "copilot"}
CN_WORKER = {"doubao_browser", "qwen_browser", "wenxin_browser"}
OVERSEAS_WORKER = {"gemini_browser", "perplexity_browser", "chatgpt_browser", "claude_browser"}


class Test下线引擎一处不剩:
    @pytest.mark.parametrize("name,table", [
        ("VALID_ENGINES", VALID_ENGINES),
        ("browser_engine.ENGINE_ROUTING", set(ENGINE_ROUTING)),
        ("reports 别名表", set(_ENGINE_ALIAS_DEFAULT) | set(_ENGINE_ALIAS_DEFAULT.values())),
        ("竞品诊断别名表", set(_DEFAULT_ENGINE_ALIAS) | set(_DEFAULT_ENGINE_ALIAS.values())),
    ])
    def test_(self, name, table):
        assert not (RETIRED & table), f"{name} 里还有 {RETIRED & table}"


class Test真机通道:
    def test_国内海外真机通道都是合法引擎(self):
        assert CN_WORKER | OVERSEAS_WORKER <= VALID_ENGINES

    def test_两份别名表一致(self):
        # Report center and competitor diagnosis each keep a copy on purpose (different gate metric definitions, see their comments), but the mapping contents must match
        assert _ENGINE_ALIAS_DEFAULT == _DEFAULT_ENGINE_ALIAS

    def test_每个真机通道都归并到本尊(self):
        for e in CN_WORKER | OVERSEAS_WORKER:
            assert e in _ENGINE_ALIAS_DEFAULT, f"{e} 没有别名,报表里会直出生键"
            assert _ENGINE_ALIAS_DEFAULT[e] == e.removesuffix("_browser")

    def test_别名的本尊自己不是别名(self):
        for canon in _ENGINE_ALIAS_DEFAULT.values():
            assert canon not in _ENGINE_ALIAS_DEFAULT, f"{canon} 既是本尊又是别名"
