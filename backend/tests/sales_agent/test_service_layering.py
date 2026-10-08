"""Structural gate for the `service/` package -- guards the 2026-08-22 split.

service.py (5645 lines) was split into 12 modules ordered by dependency layer + a facade. This pins:
1. One-way layering: upper layers may reference lower ones; a lower layer referencing an upper one turns red immediately (or circular deps grow back);
2. Complete facade: existing `from agents.sales_agent.service import X` calls must not lose anything due to the split;
3. Unique symbols: the same name may not be defined in two modules;
4. Subpackage containment: subpackages under `service/` (currently only sms_providers) must be registered in SUBPACKAGES,
   and may only be referenced by upper layers, never referencing the service layer back.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_service_layering.py -q
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SERVICE_DIR = pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent" / "service"

# Dependency layer order: earlier entries are lower. Upper layers may import lower ones, not vice versa.
LAYERS = [
    "common",
    # Channel-agnostic base for outreach channels (credential encryption / scope normalization / number selection). At the very bottom:
    # both email_config and sms_config use it, and it depends only on common.
    "channel_common",
    "profile_suggestions",
    "catalog",
    "entities",
    "undo",
    "companies",
    # LLM mapping of target industries -> real tags in the company DB. Above companies (uses its
    # _db_seed_company_query / industry_search_terms), called only by api.py.
    "industry_mapping",
    "reports",
    "profiles",
    "recommendation_profiles",
    # Mapping layer from brand materials -> profile. Above profiles (uses its _new_brand_key /
    # _profile_summary_text), below guidance (card copy reads the derived profile).
    "brandprofile",
    "guidance",
    "recommend",
    "leadreports",
    "email_config",
    # SMS config. Depends only on common + channel_common, and doesn't reference email_config either way --
    # the two channels' config layers are parallel; neither may call the other.
    "sms_config",
    # Suppression list (global do-not-contact list by email address). Depends only on common, below email_send:
    # the send layer calls it as the last gate before sending, while it needs to know nothing about how sending works.
    "suppression",
    "email_send",
    # SMS sending. It sits above email_send not because SMS depends on email, but because
    # `_message_type_for_draft` / `_report_share_for_draft` / `_delivery_content_preview`
    # these three **draft-level** (channel-agnostic) functions currently live in email_send -- rewriting them would hit
    # test_no_duplicate_symbol. Real decoupling requires pushing them down first, which is a separate refactor.
    "sms_send",
    # Persisting receipts / inbound SMS to DB + writing back unsubscribes. Above sms_send (uses its credentials and number normalization),
    # and doesn't reference delivery_stats either way -- one writes state, the other only reads aggregates.
    "sms_callback",
    # Cross-channel stats merge. Must sit above both channels -- it references both email_send and sms_send,
    # and a channel module referencing it back would be circular. "Which companies did I send to today" in chat goes through this layer.
    "delivery_stats",
    "outreach",
    # Scheduled send. Sitting above outreach is mandatory: the executor must go through `confirm_outreach_send`
    # instead of building its own send chain (design §1.1) -- two chains eventually diverge, and the resulting
    # "scheduled emails skipped the compliance gate" is the kind of thing that can least afford to go wrong.
    "send_schedule",
    "sessions",
]
RANK = {name: i for i, name in enumerate(LAYERS)}

# Subpackages (directories) allowed under `service/`, and their position in the layer order.
#
# sms_providers are outbound adapters for each SMS platform (signing, HTTP, receipt parsing) with zero in-package deps --
# so they rank below all layers (-1): anyone may reference them, they may reference nobody. They live in service/ rather than
# beside service/: root-level subpackages (flow/, runtime/) are used directly by the orchestration layer api.py/agent.py,
# while sms_providers are only called by the three service/sms_* modules, so they follow their callers.
#
# Not merged into LAYERS because LAYERS doubles as the "facade must re-export" list -- adapters are internal implementation
# and shouldn't be raised onto the public `from agents.sales_agent.service import ...` surface.
SUBPACKAGES = {"sms_providers": -1}
RANK_ALL = {**RANK, **SUBPACKAGES}


def _tree(module: str) -> ast.Module:
    return ast.parse((SERVICE_DIR / f"{module}.py").read_text(encoding="utf-8"))


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _service_imports(tree: ast.Module) -> list[str]:
    """Which sibling modules or subpackages in the same package this module imports.

    Take the segment **immediately after** `.service.`, not the last one -- otherwise
    `...service.sms_providers.base` would be read as `base`, which isn't in the layer table.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and ".service." in node.module:
            out.append(node.module.split(".service.", 1)[1].split(".")[0])
    return out


def test_layer_files_exist():
    found = sorted(p.stem for p in SERVICE_DIR.glob("*.py") if p.stem != "__init__")
    assert found == sorted(LAYERS), f"service/ 下的模块与层序表不一致:多出 {set(found) - set(LAYERS)},缺少 {set(LAYERS) - set(found)}"


def test_subpackages_declared():
    """New subpackages under service/ must first get a layer position in SUBPACKAGES, or the layer check would miss them."""
    found = sorted(p.name for p in SERVICE_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists())
    assert found == sorted(SUBPACKAGES), f"service/ 下的子包与 SUBPACKAGES 不一致:多出 {set(found) - set(SUBPACKAGES)},缺少 {set(SUBPACKAGES) - set(found)}"


@pytest.mark.parametrize("module", LAYERS)
def test_no_upward_import(module):
    """Lower layers may not reference upper layers -- the split's only hard constraint; break it and circular deps grow back."""
    bad = [dep for dep in _service_imports(_tree(module)) if RANK_ALL[dep] >= RANK[module]]
    assert not bad, f"{module}.py 引用了同层或更上层的 {sorted(set(bad))};要么换个归属,要么把被引用的函数下沉"


@pytest.mark.parametrize("pkg", sorted(SUBPACKAGES))
def test_subpackage_stays_leaf(pkg):
    """Subpackages may only be referenced by the service layer, never reference it back -- they're the bottom leaves.

    Once an adapter imports sms_config / sms_send back, the send chain and platform implementations get entangled,
    and provider stub tests have to drag in half the service package.
    """
    for path in sorted((SERVICE_DIR / pkg).rglob("*.py")):
        deps = [d for d in _service_imports(ast.parse(path.read_text(encoding="utf-8"))) if d != pkg]
        bad = [d for d in deps if RANK_ALL.get(d, len(LAYERS)) >= RANK_ALL[pkg]]
        assert not bad, f"{pkg}/{path.name} 引用了 {sorted(set(bad))};适配器必须对 service 层零依赖"


def test_no_duplicate_symbol():
    seen: dict[str, str] = {}
    dup = []
    for module in LAYERS:
        for name in _top_level_names(_tree(module)):
            if name in seen:
                dup.append(f"{name}({seen[name]} / {module})")
            seen[name] = module
    assert not dup, f"同名符号在多个模块重复定义:{dup}"


def test_facade_reexports_everything():
    """The facade must re-export all submodule symbols, or external imports silently lose things."""
    facade = _tree("__init__")
    exported = {alias.name for node in ast.walk(facade) if isinstance(node, ast.ImportFrom) for alias in node.names}
    defined = {name for module in LAYERS for name in _top_level_names(_tree(module))}
    missing = sorted(defined - exported)
    assert not missing, f"门面漏了 {len(missing)} 个符号:{missing[:10]}"


def test_facade_covers_actual_importers():
    """Every name api.py / tools.py actually imports must be in the facade (including private names)."""
    pkg = SERVICE_DIR.parent
    facade = _tree("__init__")
    exported = {alias.name for node in ast.walk(facade) if isinstance(node, ast.ImportFrom) for alias in node.names}
    for filename in ("api.py", "tools.py"):
        tree = ast.parse((pkg / filename).read_text(encoding="utf-8"))
        wanted = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and "sales_agent.service" in node.module
            for alias in node.names
        }
        missing = sorted(wanted - exported)
        assert not missing, f"{filename} 需要但门面没有:{missing}"
