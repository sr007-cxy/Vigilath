"""End-to-end evaluation — runs the real model, collects tool traces + LLM judge scores.

Measures "whether the user really got the right result", the north-star metric (component level only guarantees mechanisms don't regress).
Slow, needs an API key + DB connection, so run per release / sampled, not on every CI run.

Run the agent once per Case, then:
  · Trace check (deterministic): were expect_tools really called, were forbid_tools not called, did forbid_text not appear;
  · Judge score (LLM): query + answer go to the judge, 0/1 task completed + a one-sentence reason.

Usage:
    export DEEPSEEK_API_KEY=sk-...            # or OPENROUTER_API_KEY
    export EVAL_ACCOUNT_ID=1                  # Which account's real data to run on (default 1)
    <agent-venv>/bin/python -m agents.geo.eval.run_eval
    # Only some tags:  --tags hallucination,write-guard
    # Only some ids:   --ids s_history,g_publish_view
    # Skip the judge (traces only, saves money):  --no-judge
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from agents.geo.eval.dataset import CASES, Case


def _called_tools(result) -> list[str]:
    """Extract the names of tools actually called (in order) from one agent.run's messages."""
    names: list[str] = []
    try:
        from pydantic_ai.messages import ToolCallPart
        for msg in result.all_messages():
            for part in getattr(msg, "parts", []):
                if isinstance(part, ToolCallPart):
                    names.append(part.tool_name)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 工具轨迹解析失败: {e}", file=sys.stderr)
    return names


def _trajectory_verdict(case: Case, tools: list[str], answer: str) -> tuple[bool, list[str]]:
    """Deterministic check: returns (all passed, list of failure reasons)."""
    fails = []
    for t in case.expect_tools:
        if t not in tools:
            fails.append(f"未调用期望工具 {t}")
    for t in case.forbid_tools:
        if t in tools:
            fails.append(f"调用了禁用工具 {t}(越线)")
    for s in case.forbid_text:
        if s in (answer or ""):
            fails.append(f"输出出现禁用文案「{s}」")
    return (not fails, fails)


_JUDGE_PROMPT = """你是 Vigilath GEO/舆情 Agent 的评测判官。判断这次回答是否**正确且完整地满足了用户意图**。
判分标准:答非所问 / 编造数据 / 该查没查 / 越线推销另一条线 → 0;切题、基于真实数据、动作正确 → 1。

用户问:{utterance}
背景(期望):{note}
Agent 实际调用的工具:{tools}
Agent 回答:
{answer}

只输出 JSON:{{"score": 0 或 1, "reason": "一句中文理由"}}"""


async def _judge(utterance: str, note: str, tools: list[str], answer: str) -> dict:
    """LLM-as-judge: reuse the pro model for independent scoring (same stack as the agent under test, different agent instance)."""
    from pydantic_ai import Agent
    from agents.geo.model import get_deepseek_model

    judge = Agent(get_deepseek_model("pro"), system_prompt="你是严格的评测判官,只输出 JSON。")
    prompt = _JUDGE_PROMPT.format(utterance=utterance, note=note or "(无)",
                                  tools=", ".join(tools) or "(未调用任何工具)", answer=answer[:4000])
    r = await judge.run(prompt)
    raw = (r.output or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"score": None, "reason": f"判官输出非 JSON: {raw[:120]}"}


async def _run_case(case: Case, account_id: int, do_judge: bool) -> dict:
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.deps import AgentDeps
    from geo.database import SessionLocal

    db = SessionLocal()
    try:
        deps = AgentDeps(account_id=account_id, user_id=account_id, db=db, caps=["read", "write"])
        agent = build_embed_agent(True, line=route_line(case.utterance))
        result = await agent.run(case.utterance, deps=deps)
        answer = (result.output or "").strip()
        tools = _called_tools(result)
    except Exception as e:  # noqa: BLE001
        return {"id": case.id, "error": str(e), "passed": False}
    finally:
        db.close()

    traj_ok, fails = _trajectory_verdict(case, tools, answer)
    out = {"id": case.id, "line": route_line(case.utterance), "tools": tools,
           "traj_ok": traj_ok, "traj_fails": fails, "answer": answer[:300]}
    if do_judge:
        try:
            j = await _judge(case.utterance, case.note, tools, answer)
            out["judge_score"] = j.get("score")
            out["judge_reason"] = j.get("reason")
        except Exception as e:  # noqa: BLE001
            out["judge_score"] = None
            out["judge_reason"] = f"判官异常: {e}"
    out["passed"] = traj_ok and (out.get("judge_score") in (1, None) if do_judge else True)
    return out


async def _main(cases: list[Case], account_id: int, do_judge: bool) -> int:
    results = []
    for c in cases:
        print(f"▶ [{c.id}] {c.utterance}")
        r = await _run_case(c, account_id, do_judge)
        results.append(r)
        if r.get("error"):
            print(f"  ✗ 运行异常: {r['error']}")
            continue
        flag = "✓" if r["traj_ok"] else "✗"
        print(f"  {flag} 轨迹 line={r['line']} tools={r['tools']}")
        for f in r["traj_fails"]:
            print(f"      - {f}")
        if do_judge:
            print(f"  judge={r.get('judge_score')} — {r.get('judge_reason')}")

    n = len(results)
    traj_pass = sum(1 for r in results if r.get("traj_ok"))
    judged = [r for r in results if r.get("judge_score") in (0, 1)]
    judge_pass = sum(1 for r in judged if r["judge_score"] == 1)
    print("\n" + "=" * 56)
    print(f"轨迹通过(组件级):{traj_pass}/{n}")
    if do_judge:
        print(f"任务成功(判官,端到端):{judge_pass}/{len(judged)}" + ("" if judged else " (无有效判分)"))
    print("=" * 56)
    return 0 if traj_pass == n else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default="", help="逗号分隔,只跑含这些 tag 的 case")
    ap.add_argument("--ids", default="", help="逗号分隔,只跑这些 id")
    ap.add_argument("--no-judge", action="store_true", help="跳过 LLM 判官(只看轨迹)")
    args = ap.parse_args()

    cases = CASES
    if args.ids:
        want = set(args.ids.split(","))
        cases = [c for c in cases if c.id in want]
    if args.tags:
        want = set(args.tags.split(","))
        cases = [c for c in cases if want & set(c.tags)]
    if not cases:
        print("没有匹配的 case", file=sys.stderr)
        return 2

    account_id = int(os.environ.get("EVAL_ACCOUNT_ID", "1"))
    return asyncio.run(_main(cases, account_id, do_judge=not args.no_judge))


if __name__ == "__main__":
    raise SystemExit(main())
