"""Hard compliance gate for outreach copy -- the only check in the whole repo that really stops "the model confidently making things up".

`_outreach_copy_policy_violations` is where the product promises land: no diagnostic conclusions without a real report,
a public link required when there is a report, and no draft may claim to have been sent. If these rules are ever loosened,
production shows "a customer received an email with a fabricated audit score", which can't be undone afterwards, so they're pinned here.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_outreach_policy.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.service import (
    NoBrandProfileError,
    _has_unsubscribe_instruction,
    _outreach_copy_policy_violations,
    build_no_report_default_email_copy,
    email_material_from_profile,
)

NO_REPORT_META = {"kind": "no_report_default_email"}
REPORT_URL = "https://www.vigilath.cn/sales-agent/report/abc123"

# Production code has removed the hardcoded Vigilath defaults (positioning copy / platform support). These literals are only
# test fixtures: PLATFORM_* is the old support contact that "used to be forced into other people's emails", now asserted to
# **no longer appear**; VIGILATH_POSITIONING is the core one-liner from Vigilath's own profile, used to
# verify "Vigilath's own emails are also generated purely from its own profile".
PLATFORM_CONTACT_EMAIL = "<support-email>"
PLATFORM_CONTACT_PHONE = "<support-phone>"
VIGILATH_POSITIONING = "AI搜索可见性全链路解决方案（检测·审计·优化·监控）"

# An ordinary customer's brand profile -- selling website building, nothing to do with GEO/AEO.
# "Vigilath" appearing in the intro email is a regression.
WEBSITE_SHOP_PROFILE = {
    "company_short_name": "星启网络",
    "company_full_name": "杭州星启网络科技有限公司",
    "core_message": "10 年只做一件事：把企业官网做成能带单的官网",
    "core_service_overview": "从建站到改版一站式交付，最快 15 天上线。",
    "core_business_lines": ["企业官网建设", "老站改版"],
    "service_features": ["响应式官网建设", "老站重构与性能优化"],
    "service_guarantees": ["上线后 1 年免费维护"],
    "target_scenarios": ["没有官网的初创企业", "官网多年未更新的制造业"],
    "core_credentials": ["服务过 60+ 制造业官网"],
    "contact_email": "hello@xingqi.example.com",
    "contact_phone": "0571-88880000",
    "website": "https://xingqi.example.com",
}

VIGILATH_PROFILE = {
    "company_short_name": "Vigilath（威吉力）",
    "core_message": VIGILATH_POSITIONING,
    "service_features": ["主流大模型品牌认知审计", "AI 可见性持续监测"],
    "website": "https://www.vigilath.cn/",
}

PLACEHOLDER_BRAND_PROFILE = {
    "company_short_name": "默认品牌",
    "service_features": ["财务能力"],
    "target_scenarios": ["成长型企业", "有财务规范化需求的经营主体"],
}


def _context(profile: dict, company_name: str = "星迈科技") -> dict:
    """Assemble the two pieces of `_outreach_context_payload` used by `build_no_report_default_email_copy`."""
    return {
        "company": {"name": company_name} if company_name else {},
        "brand_profile": {"email_material": email_material_from_profile(profile)},
    }


def _check(subject="", content="", channel="email", can_reference_report=False, url="", metadata=None):
    return _outreach_copy_policy_violations(
        subject, content, channel, can_reference_report, url, metadata if metadata is not None else NO_REPORT_META
    )


# --- No report: no diagnostic conclusions of any kind ---

@pytest.mark.parametrize("phrase", [
    "报告显示", "检测报告显示", "已完成诊断", "已经检测", "完整诊断报告",
    "总分", "等级为", "llms.txt 缺失", "结构化数据不足", "官网存在明显问题",
])
def test_no_report_blocks_diagnosis_claims(phrase):
    violations = _check(content=f"您好，{phrase}贵司还有提升空间。")
    assert any("不能写具体诊断结论" in v for v in violations), f"没有真实报告时竟放行了 {phrase!r}"


def test_no_report_plain_pitch_passes():
    """Pure capability intros that don't touch conclusions must pass, or the default template would be blocked by its own gate."""
    content = "您好：现在大量客户直接通过 AI 大模型做选型对比，很多企业在 AI 对话中没有被提及。欢迎回复沟通。"
    assert _check(content=content) == []


# --- No-report intro email: generated from the sender's own brand profile ---
#
# Before 2026-08-27 this was a hardcoded Vigilath sales email. After report gating launched, brands without report capability
# **always** take this branch -- a customer selling website building would send Vigilath ads to all leads,
# signed with Vigilath's 400 phone number. The cases below pin "never go back to that".

def test_default_template_passes_its_own_policy():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE), "sales@example.com")
    assert _check(subject=copy["subject"], content=copy["content"]) == []


def test_default_template_carries_no_report_link():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE), "sales@example.com")
    assert "/sales-agent/report/" not in copy["content"]
    assert "/sales-agent/report/" not in copy["html_content"]


def test_default_template_uses_company_name():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE), "sales@example.com")
    assert "星迈科技" in copy["content"]


def test_default_template_falls_back_without_company():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE, company_name=""))
    assert copy["content"].startswith("您好：")


def test_default_template_never_pitches_vigilath_for_other_brands():
    """The core regression point of this change: other people's emails must not contain Vigilath / GEO&AEO / platform support numbers."""
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE))
    for field in ("subject", "content", "html_content"):
        blob = copy[field].casefold()
        assert "vigilath" not in blob
        assert "威吉力" not in copy[field]
        assert PLATFORM_CONTACT_EMAIL not in copy[field]
        assert PLATFORM_CONTACT_PHONE not in copy[field]


def test_default_template_speaks_the_brands_own_business():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE))
    assert "星启网络" in copy["subject"]
    for field in ("content", "html_content"):
        assert "把企业官网做成能带单的官网" in copy[field]
        assert "响应式官网建设" in copy[field]


def test_default_template_hides_placeholder_brand_and_expands_body():
    """`默认品牌` ("default brand") is a system placeholder name and must not enter emails; with sparse profiles, don't send a three-line empty template either."""
    copy = build_no_report_default_email_copy(_context(PLACEHOLDER_BRAND_PROFILE))
    for field in ("subject", "content", "html_content"):
        assert "默认品牌" not in copy[field]
    assert "财务能力" in copy["subject"]
    assert "财务能力" in copy["content"]
    assert "成长型企业" in copy["content"]
    assert "推进步骤" in copy["content"]
    assert "首次沟通" in copy["content"]
    assert "双方需要准备的信息" in copy["content"]
    assert len(copy["content"]) >= 260
    assert len([p for p in copy["content"].split("\n\n") if p.strip()]) >= 7


def test_default_template_signs_with_the_brands_own_contacts():
    """The signature uses only contact info from the profile -- the recipient replies to the sending company, not platform support."""
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE))
    for field in ("content", "html_content"):
        assert "hello@xingqi.example.com" in copy[field]
        assert "0571-88880000" in copy[field]


def test_default_template_prefers_the_explicit_contact_email():
    copy = build_no_report_default_email_copy(_context(WEBSITE_SHOP_PROFILE), "sales@example.com")
    assert "sales@example.com" in copy["content"]


def test_default_template_refuses_when_the_profile_is_empty():
    """Empty profile means don't send -- an email with only "hello, regards" is worse than none."""
    with pytest.raises(NoBrandProfileError):
        build_no_report_default_email_copy(_context({}))


def test_vigilath_email_is_built_from_its_own_profile_too():
    """Vigilath's own emails are also generated purely from its brand profile -- the core one-liner comes from core_message,
    no longer hardcoded locked copy. It takes the same no-special-case path as other brands."""
    copy = build_no_report_default_email_copy(_context(VIGILATH_PROFILE))
    for field in ("content", "html_content"):
        assert VIGILATH_POSITIONING in copy[field]


def test_vigilath_gets_no_injected_platform_contacts():
    """No contact info in the profile means no contact info in the signature -- Vigilath included; no more falling back to platform support."""
    copy = build_no_report_default_email_copy(_context(VIGILATH_PROFILE))
    assert PLATFORM_CONTACT_EMAIL not in copy["content"]
    assert PLATFORM_CONTACT_PHONE not in copy["content"]


# --- With report: the body must include the public link ---

def test_report_email_requires_public_link():
    violations = _outreach_copy_policy_violations("主题", "正文没有链接。", "email", True, REPORT_URL, {})
    assert any("必须包含公开报告链接" in v for v in violations)


def test_report_email_without_url_is_rejected():
    violations = _outreach_copy_policy_violations("主题", "正文。", "email", True, "", {})
    assert any("可公开访问" in v for v in violations)


def test_report_email_with_link_passes():
    content = f"您好，诊断结论详见 {REPORT_URL}"
    assert _outreach_copy_policy_violations("主题", content, "email", True, REPORT_URL, {}) == []


# --- Global: no impersonating the platform, no claiming already sent ---

@pytest.mark.parametrize("phrase", ["这里是 Vigilath", "我是 Vigilath", "我们是 Vigilath", "Vigilath 官方"])
def test_blocks_impersonating_platform(phrase):
    violations = _outreach_copy_policy_violations("主题", f"{phrase}，为您服务。", "email", True, REPORT_URL, {})
    assert any("冒充平台" in v for v in violations)


@pytest.mark.parametrize("phrase", ["已发送", "自动发送", "自动群发", "批量群发"])
def test_blocks_claiming_already_sent(phrase):
    violations = _check(content=f"该邮件{phrase}给贵司。")
    assert any("不能声称已经发送或自动群发" in v for v in violations)


def test_vigilath_intro_only_allowed_when_the_sender_really_is_vigilath():
    """The exemption for signing as Vigilath only honors `sender_is_vigilath`, which is computed from the brand profile and out of the model's reach.

    The exemption used to be "this is the no-report default template" -- back then the template was a hardcoded Vigilath sales email.
    Now intro emails are generated from each profile, so exempting by draft type would open a hole for everyone.
    """
    content = "Vigilath 可以帮您提升 AI 可见度。"
    blocked = "不能介绍或署名 Vigilath"
    assert any(blocked in v for v in _outreach_copy_policy_violations("", content, "email", False, "", {}))
    # Merely being "the no-report default template" is no longer enough
    assert any(blocked in v for v in _check(content=content))
    assert not any(blocked in v for v in _check(content=content, metadata={**NO_REPORT_META, "sender_is_vigilath": True}))


# --- Non-email channels ---

def test_sms_requires_unsubscribe():
    assert any("退订" in v for v in _check(content="您好，了解一下 AI 可见度。", channel="sms"))
    assert not any("退订" in v for v in _check(content="您好，了解一下 AI 可见度。回复退订不再联系。", channel="sms"))


def test_sms_length_capped():
    assert any("压缩为一条短消息" in v for v in _check(content="长" * 321 + "退订", channel="sms"))


@pytest.mark.parametrize("word", ["退订", "不再联系", "停止联系"])
def test_unsubscribe_detection(word):
    assert _has_unsubscribe_instruction(f"回复{word}")
    assert not _has_unsubscribe_instruction("回复 STOP")
