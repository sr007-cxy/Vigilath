"""Context budget and history trimming.

The original gap: `messages_json` stores Pydantic AI's full history, fed back to the model verbatim every turn, with no upper bound.
On the test host the largest session measured 131KB (roughly 30-40k tokens); every turn was paying for that volume,
and it would hit the provider's input limit sooner or later.

Two design principles:

1. **Store everything, feed a trimmed slice** — persisting stays full (traceable, reversible); trim only before feeding the model.
   Disabling the threshold fully restores the original behavior, no data changes needed.
2. **The summary doesn't call the model** — profile, this run's filter conditions and recent recommendation results are already in the DB;
   assembling a deterministic paragraph to replace the trimmed conversation suffices. Zero cost, unit-testable, no drift.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Chinese estimated at ~1.6 chars/token; 60000 chars ≈ 24k tokens, leaving ample room for the reply.
_DEFAULT_MAX_CHARS = 60_000
_DEFAULT_KEEP_TURNS = 6


@dataclass(frozen=True)
class ContextBudget:
    max_chars: int = _DEFAULT_MAX_CHARS
    keep_recent_turns: int = _DEFAULT_KEEP_TURNS

    @property
    def enabled(self) -> bool:
        return self.max_chars > 0


def budget_from_env() -> ContextBudget:
    """`SALES_AGENT_CTX_MAX_CHARS=0` disables trimming, reverting to pre-change behavior."""
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    return ContextBudget(
        max_chars=_int("SALES_AGENT_CTX_MAX_CHARS", _DEFAULT_MAX_CHARS),
        keep_recent_turns=_int("SALES_AGENT_CTX_KEEP_TURNS", _DEFAULT_KEEP_TURNS),
    )


def estimate_chars(messages: Any) -> int:
    """Rough estimate of history size. No tokenizer dependency; counts serialized characters."""
    if not messages:
        return 0
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        return len(ModelMessagesTypeAdapter.dump_json(messages))
    except Exception:  # noqa: BLE001 — estimation failures must not affect the main flow
        return sum(len(str(m)) for m in messages)


def _line(label: str, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = "、".join(str(v).strip() for v in value if str(v).strip())
    text = str(value or "").strip()
    return f"{label}:{text}" if text else ""


def build_state_digest(db: Any, user_id: int, lead_flow: dict[str, Any] | None = None, label: str = "此前对话摘要") -> str:
    """Assemble the structured state in the DB into a paragraph replacing the trimmed conversation history.

    Only reads the profile and this run's filter conditions, with no conversation text — even if conversation text is trimmed,
    what users really need the model to remember (who I am, what I'm looking for this time) isn't lost.
    """
    parts: list[str] = []
    try:
        from agents.sales_agent.service import seller_profile_for_lead_entry
        profile = seller_profile_for_lead_entry(db, user_id) or {}
        parts += [
            _line("我方专业能力", profile.get("service_capabilities")),
            _line("我方企业规模", profile.get("company_size")),
            _line("适合服务的客户", profile.get("typical_customers")),
        ]
    except Exception:  # noqa: BLE001 — must still produce a summary even if the profile can't be fetched
        pass

    flow = lead_flow if isinstance(lead_flow, dict) else {}
    parts += [
        _line("本次目标企业规模", flow.get("target_company_size")),
        _line("本次目标行业", flow.get("target_industries")),
        _line("本次目标地区", flow.get("target_regions")),
        _line("本次推荐数量", flow.get("requested_limit")),
    ]
    parts = [p for p in parts if p]

    # Which batch 「这批」 ("this batch") refers to is a database question, not a language question — but the model can't see campaign_id
    # (trimmed by the allowlist in `runtime/payload.py`), and once history is trimmed again not even "just recommended a batch" remains.
    # Put the batch number into the summary so it can at least pass it verbatim to schedule_outreach_send.
    campaign_id = int(flow.get("last_recommendation_campaign_id") or 0)
    if campaign_id:
        try:
            from agents.sales_agent.service import resolve_batch_lead_ids
            lead_count = len(resolve_batch_lead_ids(db, user_id, [campaign_id]))
        except Exception:  # noqa: BLE001 — failing to find batches mustn't prevent the whole summary from being assembled
            lead_count = 0
        if lead_count:
            parts.append(
                f"最近一批推荐的批次号 campaign_id={campaign_id},共 {lead_count} 家;"
                "用户说「这批」默认指它 —— 要排期发送时把这个批次号传给 schedule_outreach_send 的 campaign_id,不要自己挑 lead_ids"
            )

    if str(flow.get("website_scope") or "") == "only_with_website":
        # The website isn't a filter condition, only a scoring bonus. When the user explicitly asks to "only recommend ones with a website",
        # the reply must say so — can't recommend a batch of companies without websites and stay silent.
        parts.append("本次用户要求只推有官网的企业:推荐不按官网硬性筛选候选,官网只作为排序加分项,回复必须如实说明这一点,并给出这批里实际有几家有官网(website_count)")
    if not parts:
        return ""
    return f"【{label}】" + ";".join(parts) + "。以上为已确认信息,不要重复追问。"


def trim_history(messages: Any, digest: str, budget: ContextBudget) -> tuple[Any, bool]:
    """When over budget, keep: the earliest message (usually with system/first-turn intent) + summary + the most recent turns.

    Returns (trimmed history, whether trimmed). When within budget it's returned as-is, byte-identical to pre-change.
    """
    if not budget.enabled or not messages:
        return messages, False
    if estimate_chars(messages) <= budget.max_chars:
        return messages, False

    keep_tail = max(2, budget.keep_recent_turns * 2)   # One turn ≈ two messages, "request + response"
    head = list(messages[:1])
    tail = list(messages[-keep_tail:]) if len(messages) > keep_tail else list(messages)

    trimmed = head + tail
    if digest:
        injected = _digest_message(digest)
        if injected is not None:
            trimmed = head + [injected] + tail
    # In extreme cases the tail itself is still over budget — cut it in half again; better short than blown up
    while len(trimmed) > 3 and estimate_chars(trimmed) > budget.max_chars:
        trimmed = trimmed[:1] + trimmed[1 + (len(trimmed) - 1) // 2:]
    return trimmed, True


def _digest_message(digest: str):
    """Wrap the summary as a user message and put it back into history. If construction fails, skip it; the conversation mustn't be interrupted by it."""
    try:
        from pydantic_ai.messages import ModelRequest, UserPromptPart
        return ModelRequest(parts=[UserPromptPart(content=digest)])
    except Exception:  # noqa: BLE001
        return None
