"""Deterministic validation of tool output.

The 2026-08-22 company size bucketing bug (two of the five official options swallowed into "1-10 employees")
survived into production precisely because nobody validated recommendation results. This pins a few basic consistency checks.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_verify.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.runtime.verify import (
    normalize_flow_action_text,
    redact_unfinished_report,
    resolve_delivery_status_followup,
    resolve_detection_action,
    resolve_lead_action,
    resolve_orphan_flow_action,
    verify_delivery_status_reply,
    verify_delivery_target_reply,
    verify_detection_keywords_answer,
    verify_recommendation,
    verify_report_summary,
    verify_slot_answer,
)


def _payload(n=10, requested=10, actual=None, shortfall=0, status="ok", dup=False):
    ids = list(range(1, n + 1))
    if dup and n >= 2:
        ids[-1] = ids[0]
    return {
        "leads": [{"company_id": i} for i in ids],
        "requested_limit": requested,
        "actual_count": n if actual is None else actual,
        "shortfall": shortfall,
        "status": status,
    }


def test_healthy_payload_passes():
    assert verify_recommendation(_payload()) == []


def test_exceeding_requested_limit_caught():
    problems = verify_recommendation(_payload(n=120, requested=100, shortfall=0))
    assert any("超过请求上限" in p for p in problems)


def test_duplicate_company_caught():
    problems = verify_recommendation(_payload(n=10, dup=True))
    assert any("出现多次" in p for p in problems)


def test_actual_count_mismatch_caught():
    problems = verify_recommendation(_payload(n=10, actual=42))
    assert any("对不上" in p for p in problems)


def test_shortfall_inconsistency_caught():
    """Requested 100, got only 10, yet the gap says 0 -- that's whitewashing."""
    problems = verify_recommendation(_payload(n=10, requested=100, shortfall=0))
    assert any("缺口" in p for p in problems)


def test_ok_status_with_shortfall_caught():
    problems = verify_recommendation(_payload(n=50, requested=100, shortfall=50, status="ok"))
    assert any("ok" in p for p in problems)


def test_partial_status_with_shortfall_is_fine():
    assert verify_recommendation(_payload(n=50, requested=100, shortfall=50, status="partial")) == []


@pytest.mark.parametrize("junk", [None, "字符串", 42, []])
def test_garbage_payload_does_not_crash(junk):
    assert verify_recommendation(junk) == ["推荐结果结构异常"]


def test_empty_leads_with_matching_counts_passes():
    assert verify_recommendation({"leads": [], "requested_limit": 0, "actual_count": 0, "shortfall": 0}) == []


# --- Report summary ---

def test_unfinished_report_must_not_carry_score():
    problems = verify_report_summary({"score": 78, "grade": "B"}, report_ready=False)
    assert problems and "尚未完成" in problems[0]


def test_finished_report_may_carry_score():
    assert verify_report_summary({"score": 78}, report_ready=True) == []


def test_unfinished_report_without_conclusions_is_fine():
    assert verify_report_summary({"status": "generating"}, report_ready=False) == []


def test_unfinished_report_narrative_also_caught():
    """Narrative blocks are more likely than scores to be quoted as conclusions in emails."""
    problems = verify_report_summary({"executive_summary": "整体表现良好", "key_findings": ["a"]}, report_ready=False)
    assert problems and "结论摘要" in problems[0] and "关键发现" in problems[0]


def test_problem_text_uses_chinese_not_field_names():
    problems = verify_report_summary({"total_score": 78, "grade": "B"}, report_ready=False)
    assert "total_score" not in problems[0] and "grade" not in problems[0]


def test_redact_clears_conclusions_keeps_progress():
    payload = {
        "status": "generating",
        "total_score": 78,
        "grade": "B",
        "executive_summary": "还没跑完就下结论",
        "key_findings": ["a", "b"],
        "progress": {"done": 3, "total": 8},
        "url": "https://example.com",
    }
    redact_unfinished_report(payload)
    assert verify_report_summary(payload, report_ready=False) == []
    assert payload["total_score"] is None and payload["executive_summary"] == "" and payload["key_findings"] == []
    assert payload["progress"] == {"done": 3, "total": 8} and payload["url"] == "https://example.com"


# --- Do results match the selected filters ---

def _with_conditions(n=20, industry_hits=0, region_hits=0, filter_applied=False):
    payload = _payload(n=n, requested=n, shortfall=0)
    payload.update({
        "requested_industries": ["教育培训"],
        "requested_regions": ["北京"],
        "industry_match_count": industry_hits,
        "region_match_count": region_hits,
        "filter_applied": filter_applied,
    })
    return payload


def test_zero_condition_match_is_caught():
    """Production as-is: selected "education & training + Beijing", got back 20 Qingdao medical device companies -- all three count checks passed."""
    problems = verify_recommendation(_with_conditions())
    assert any("没有一家符合所选行业" in p for p in problems)
    assert any("没有一家符合所选地区" in p for p in problems)


def test_seed_pool_fallback_is_stated():
    problems = verify_recommendation(_with_conditions())
    assert any("未过滤候选范围" in p for p in problems)


def test_partial_match_is_caught():
    problems = verify_recommendation(_with_conditions(industry_hits=3, region_hits=20))
    assert any("只有 3/20 家符合所选行业" in p for p in problems)
    assert not any("符合所选地区" in p for p in problems), "地区 20/20 全中,不该报"


def test_full_match_passes():
    assert verify_recommendation(_with_conditions(industry_hits=20, region_hits=20, filter_applied=True)) == []


def test_no_conditions_means_nothing_to_check():
    """Skip this check when the user didn't restrict industry/region."""
    payload = _payload()
    payload.update({"requested_industries": [], "requested_regions": []})
    assert verify_recommendation(payload) == []


def test_empty_result_skips_condition_check():
    """With zero results, report the gap, not the match rate."""
    payload = {"leads": [], "requested_limit": 0, "actual_count": 0, "shortfall": 0,
               "requested_industries": ["教育培训"], "industry_match_count": 0}
    assert verify_recommendation(payload) == []


# --- Input understanding / action word harness ---


@pytest.mark.parametrize("raw,want", [
    ("  继续。", "继续"),
    ("确认？", "确认"),
    ("下一步!", "下一步"),
    ("生成报告～", "生成报告"),
    ("继续找企业。", "继续找企业"),
])
def test_flow_action_normalization_strips_edge_punctuation(raw, want):
    assert normalize_flow_action_text(raw) == want


@pytest.mark.parametrize("raw", ["继续", "确认。", "确认选择", "下一步", "跳过"])
def test_orphan_flow_action_only_matches_without_pending_context(raw):
    assert resolve_orphan_flow_action(raw, step="", detection_step="") == normalize_flow_action_text(raw)
    assert resolve_orphan_flow_action(raw, step="target_regions", detection_step="") == ""
    assert resolve_orphan_flow_action(raw, step="", detection_step="detection_keywords") == ""


@pytest.mark.parametrize("raw,want", [
    ("生成报告", "生成报告"),
    ("检测官网？", "检测官网"),
    ("官网检测", "官网检测"),
    ("生成诊断报告", "生成诊断报告"),
])
def test_detection_actions_are_resolved_before_slot_answers(raw, want):
    assert resolve_detection_action(raw) == want
    assert verify_slot_answer("seller_capabilities", raw)


@pytest.mark.parametrize("raw,want", [
    ("找客户", "找客户"),
    ("找企业。", "找企业"),
    ("推荐企业", "推荐企业"),
    ("继续找客户", "继续找客户"),
])
def test_lead_actions_are_resolved_before_slot_answers(raw, want):
    assert resolve_lead_action(raw) == want
    assert verify_slot_answer("seller_capabilities", raw)


@pytest.mark.parametrize("raw", ["继续", "确认。", "确认选择", "下一步", "跳过", "更新画像", "修改资料"])
def test_slot_answer_rejects_button_actions_with_punctuation(raw):
    assert verify_slot_answer("seller_capabilities", raw)


def test_slot_answer_accepts_real_answer():
    assert verify_slot_answer("seller_capabilities", "AI-GEO 服务、SEO 技术团队") == []


@pytest.mark.parametrize("raw", ["继续", "确认。", "下一步", "跳过", "找客户", "生成报告"])
def test_detection_keywords_reject_button_actions(raw):
    assert verify_detection_keywords_answer(raw)


def test_detection_keywords_accept_real_terms():
    assert verify_detection_keywords_answer("品牌词: Vigilath; 行业关键词: GEO、SaaS") == []


# --- Outreach/report status harness ---


def test_delivery_followup_resolves_detail_and_scope_from_state():
    state = {"kind": "report_visit", "scope": "today"}
    assert resolve_delivery_status_followup("具体哪家？", state) == {"kind": "report_visit", "scope": "today"}
    assert resolve_delivery_status_followup("不是今天的，全部", state) == {"kind": "report_visit", "scope": "all"}
    assert resolve_delivery_status_followup("你好", state) is None
    assert resolve_delivery_status_followup("具体哪家", {"kind": "unknown"}) is None


def test_delivery_status_verify_catches_false_zero_report_reply():
    data = {"summary": {"report_visit_count": 2, "report_visited": 1, "report_visited_companies": 1}}
    problems = verify_delivery_status_reply("报告访问", "没有找到报告访问记录。", data, kind="report_visit")
    assert any("报告访问数据非 0" in p for p in problems)
    assert any("报告访问次数 2" in p for p in problems)


def test_delivery_status_verify_accepts_matching_report_reply():
    data = {"summary": {"report_visit_count": 2, "report_visited": 1, "report_visited_companies": 1}}
    assert verify_delivery_status_reply("报告访问", "管理范围有 1 家企业打开/访问过报告,报告访问 2 次。", data, kind="report_visit") == []


def test_delivery_status_verify_catches_false_zero_open_reply():
    data = {"summary": {"opened": 1, "opened_companies": 1, "open_count": 3}}
    problems = verify_delivery_status_reply("开信情况", "没有找到开信/阅读记录。", data, kind="open")
    assert any("开信/阅读数据非 0" in p for p in problems)


def test_delivery_target_verify_catches_account_wide_answer():
    """Asking about a specific mailbox but answering with account-wide totals -- every number right, the answer wrong; must be blocked."""
    data = {"summary": {"sent": 1, "failed": 0, "attempted": 1}}
    problems = verify_delivery_target_reply(
        "zhangsan@company201.example.com", "管理范围总共尝试发送 950 封邮件,成功 871 封,失败 79 封。", data,
    )
    assert any("没有出现这个对象" in p for p in problems)


def test_delivery_target_verify_catches_false_zero_for_named_target():
    data = {"summary": {"sent": 2, "failed": 1, "attempted": 3}}
    problems = verify_delivery_target_reply(
        "信阳市嘉欣电子科技有限公司", "信阳市嘉欣电子科技有限公司没有查到发送记录。", data,
    )
    assert any("回答却说没有" in p for p in problems)


def test_delivery_target_verify_accepts_named_and_matching_answer():
    data = {"summary": {"sent": 1, "failed": 0, "attempted": 1}}
    assert verify_delivery_target_reply(
        "zhangsan@company201.example.com", "zhangsan@company201.example.com 共有 1 封发送记录,已成功发出。", data,
    ) == []


def test_delivery_target_verify_requires_saying_no_records_when_empty():
    data = {"summary": {"sent": 0, "failed": 0, "attempted": 0}}
    problems = verify_delivery_target_reply("zzz@nowhere.cn", "zzz@nowhere.cn 的情况我确认过了。", data)
    assert any("没有把这一点说清楚" in p for p in problems)
    assert verify_delivery_target_reply("zzz@nowhere.cn", "没有查到向 zzz@nowhere.cn 发送过邮件的记录。", data) == []
