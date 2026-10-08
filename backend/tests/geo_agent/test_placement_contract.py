"""**Key-name contract** between the placement strategy tool and the service layer -- no DB, no LLM.

True story: the tool read p.get("reason"), while placement_strategy produced "rationale".
Key mismatch -> "reason" always an empty string -> the model only got two numbers, "rank + hit rate".
And those two are **inversely correlated** (rank = response share x miss ratio x industry weight), so the model inevitably
read it as "sorted by hit rate" and rendered a self-contradictory table for ops: a 55% hit rate ranked 4th.

This kind of bug doesn't error or crash, it just quietly loses a field; without pinning it, it'll happen again.

Run: venv/bin/python -m pytest tests/geo_agent/test_placement_contract.py -q   # cwd = backend/
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re


def _tool_src() -> str:
    import agents.geo.admin_tools as m
    return pathlib.Path(m.__file__).resolve().read_text(encoding="utf-8")


def _keys_read_from(varname: str, src: str) -> set[str]:
    """Scan literal keys in `<varname>.get("x")`. Regex rather than ast -- there are only code fragments here,
    and a cut fragment isn't necessarily a valid statement; ast.parse errors on indentation (we hit this)."""
    return set(re.findall(rf'{varname}\.get\("(\w+)"\)', src))


def _service_keys() -> set[str]:
    """Which keys the dict literal returned by _engine_priority has."""
    from geo.services import placement_strategy as ps

    tree = ast.parse(inspect.getsource(ps._engine_priority))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def test_rationale_key_matches_service():
    """The tool must read rationale -- it used to read reason, and the reason field was always empty."""
    assert "rationale" in _service_keys(), "服务层不再产出 rationale?那工具侧要跟着改"
    assert 'p.get("rationale")' in _tool_src(), "工具没读 rationale,理由会是空字符串"
    assert 'p.get("reason")' not in _tool_src(), "又读回 reason 了,服务层没有这个键"


def test_engine_priority_keys_all_exist_in_service():
    """Every key the tool reads from p must actually be produced by the service layer -- one typo silently drops one field."""
    src = _tool_src()
    i = src.index("prio = [{")
    block = src[i:src.index('for p in (strategy.get("engine_priority")', i)]
    read = _keys_read_from("p", block)
    missing = read - _service_keys()
    assert not missing, f"工具读了服务层没有的键(会静默变 None):{sorted(missing)}"


def test_rationale_not_clipped_too_short():
    """The reason is a full Chinese sentence; 80 chars would cut off conclusions like "it's a weakness / switch to maintenance" -- exactly the information to deliver."""
    m = re.search(r'"理由": _clip\(p\.get\("rationale"\),\s*(\d+)\)', _tool_src())
    assert m, "找不到理由字段的 _clip 调用"
    assert int(m.group(1)) >= 120, f"理由被截到 {m.group(1)} 字,结论会被切掉"


def test_docstring_warns_about_the_ordering():
    """Rank and hit rate are inversely correlated. The docstring is the schema fed to the model -- without spelling it out, the model inevitably reads it as
    "sorted by hit rate" and renders accordingly."""
    import agents.geo.admin_tools as m

    doc = m.admin_placement_strategy.__doc__ or ""
    assert "not ranked by hit rate" in doc
    assert "miss ratio" in doc


def test_share_pct_is_exposed():
    """Hit rate alone can't explain the rank; response share is the other half of the factor and must be given together."""
    assert '"应答占比": p.get("share_pct")' in _tool_src()
