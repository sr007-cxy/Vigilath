"""Don't say the brand name twice in the intro; if contact info is filled in the profile, it must be in the signature.

2026-08-27 production, the opening of the GEO-TAG email was

    「我这边是GEO-TAG，GEO Tag 是GEO领域首个中立计量工具。」 ("This is GEO-TAG, GEO Tag is the first neutral measurement tool in GEO.")

`intro` was hard-concatenated as `我这边是{品牌名}` ("This is {brand}") + comma + the profile's "core one-liner". That line is filled in by the customer,
**is itself a complete sentence and already contains the brand name** -- so the name is said twice, spelled differently
(`GEO-TAG` / `GEO Tag`, reads like two companies), and the second half is comma-spliced onto the first into a run-on. The subject line
`{品牌名} - {核心一句话}` ("{brand} - {one-liner}") has the same flaw, and it is the first line the recipient sees.

Contact info half: emails without a report are assembled by the backend, and `_contact_lines` always adds a signature; for emails with a report the body
is written by the model, and the prompt only said "signature contact info **may only use** what's listed in contacts" -- that's a whitelist,
not "must include", so the model omitting the signature entirely isn't a violation, and the email is wasted.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_email_intro_and_contacts.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.service.brandprofile import email_material_from_profile
from agents.sales_agent.service.outreach import (
    REPORT_EMAIL_PROMPT_VERSION,
    _ensure_contact_block,
    _ensure_contact_block_html,
    _fit_pitch_to_intro,
    build_no_report_default_email_copy,
)

# Shape of that production profile: the one-liner includes the brand name, spelled differently from the brand name field.
GEO_TAG = {
    "company_short_name": "GEO-TAG",
    "core_message": "GEO Tag 是GEO领域首个中立计量工具",
    "service_features": ["AI 引擎引用监测", "GEO 效果第三方计量"],
    "target_scenarios": ["有官网但搜索获客一般的企业"],
    "contact_email": "hi@geotag.example.com",
    "website": "https://geotag.example.com",
}

LONG_GEO_TAG = {
    **GEO_TAG,
    "core_message": (
        "GEO Tag 是GEO领域首个中立计量工具,实现了全链路数据的统一口径:"
        "从AI引擎端的品牌提及、URL引用,到站点端的访客行为、转化金额,"
        "所有数据在同一事件模型、同一控制台内,可查可对账,填补了 GEO 行业第三方效果验证的空白"
    ),
}


def _context(profile: dict, company_name: str = "星迈科技") -> dict:
    return {
        "company": {"name": company_name},
        "brand_profile": {"email_material": email_material_from_profile(profile)},
    }


# --- The opening must not say the brand name twice ---

def test_pitch_leading_with_the_brand_name_is_trimmed():
    assert _fit_pitch_to_intro("GEO Tag 是GEO领域首个中立计量工具", "GEO-TAG") == ("，", "GEO领域首个中立计量工具")


def test_brand_name_match_ignores_case_and_separators():
    """Mismatched brand spellings are the norm; compare after normalization."""
    for written in ("GEO Tag 是中立计量工具", "geo-tag 是中立计量工具", "GEOTAG：中立计量工具"):
        assert _fit_pitch_to_intro(written, "GEO-TAG") == ("，", "中立计量工具"), written


def test_we_are_prefix_is_trimmed_too():
    assert _fit_pitch_to_intro("我们是GEO领域首个中立计量工具", "GEO-TAG") == ("，", "GEO领域首个中立计量工具")


def test_plain_appositive_is_left_alone():
    """Appositive phrases keep the comma join -- that's how most profiles are written; don't break it."""
    pitch = "AI搜索可见性全链路解决方案（检测·审计·优化·监控）"
    assert _fit_pitch_to_intro(pitch, "Vigilath（威吉力）") == ("，", pitch)


def test_sentence_with_another_subject_starts_a_new_sentence():
    """A complete sentence that can't be trimmed starts a new sentence instead of being spliced onto the first half."""
    assert _fit_pitch_to_intro("我们帮品牌做全链路计量", "GEO-TAG") == ("。", "我们帮品牌做全链路计量")


def test_brand_name_is_not_cut_mid_word():
    """Brand 「官网通」, one-liner 「官网通用方案」 -- without a boundary check it gets cut to 「,用方案」."""
    assert _fit_pitch_to_intro("官网通用方案一站式交付", "官网通") == ("，", "官网通用方案一站式交付")


def test_intro_says_the_brand_name_once():
    content = build_no_report_default_email_copy(_context(GEO_TAG))["content"]
    assert "我这边是GEO-TAG，GEO领域首个中立计量工具。" in content
    assert "GEO Tag 是" not in content, "品牌名连说了两遍"


def test_subject_is_short_and_does_not_inline_the_whole_pitch():
    """The subject holds only the short positioning; the long one goes into the body so it doesn't take 3-4 lines on list cards."""
    copy = build_no_report_default_email_copy(_context(LONG_GEO_TAG))
    assert copy["subject"] == "GEO-TAG - GEO领域首个中立计量工具，有效记录ROI"
    assert "统一口径" not in copy["subject"]
    assert "统一口径" in copy["content"]


def test_intro_template_change_bumps_the_draft_cache_version():
    """Otherwise the same lead hits the old draft cache, and in production it looks like "the subject didn't change"."""
    assert REPORT_EMAIL_PROMPT_VERSION == "report-email-v27"


def test_pitch_that_is_only_the_brand_name_is_not_proof_of_a_profile():
    """A one-liner containing only the brand name leaves nothing after trimming -- it can't count as proof the profile was filled in."""
    assert _fit_pitch_to_intro("GEO-TAG", "GEO-TAG") == ("，", "")


# --- If contact info is filled in the profile, it must be in the signature ---

def test_no_report_letter_signs_off_with_the_profile_email():
    content = build_no_report_default_email_copy(_context(GEO_TAG))["content"]
    assert "联系邮箱：hi@geotag.example.com" in content


def test_model_written_letter_gets_the_contacts_appended():
    """On the with-report path the body is model-written; add the signature if it's missing."""
    material = email_material_from_profile(GEO_TAG)
    out = _ensure_contact_block("您好，诊断结论详见附件。\n\n祝好", material)
    assert "hi@geotag.example.com" in out
    assert "https://geotag.example.com" in out


def test_contacts_already_present_are_not_appended_twice():
    material = email_material_from_profile(GEO_TAG)
    body = "您好。\n\n邮箱：hi@geotag.example.com\n官网：https://geotag.example.com"
    assert _ensure_contact_block(body, material) == body


def test_only_the_missing_contact_is_appended():
    """Dedup by value, so it's recognized even if the model used a different label."""
    material = email_material_from_profile(GEO_TAG)
    out = _ensure_contact_block("您好。\n\n邮箱：hi@geotag.example.com", material)
    assert out.count("hi@geotag.example.com") == 1
    assert "https://geotag.example.com" in out


def test_empty_profile_contacts_append_nothing():
    """The other half of the whitelist: anything not filled in the profile never appears; no fallback to platform support."""
    material = email_material_from_profile({"company_short_name": "星启网络", "core_message": "把官网做成能带单的官网"})
    body = "您好。\n\n祝好"
    assert _ensure_contact_block(body, material) == body


def test_html_twin_appends_the_same_contacts():
    """If plain text gets it but rich text doesn't, the copy the recipient sees is still missing it."""
    material = email_material_from_profile(GEO_TAG)
    out = _ensure_contact_block_html("<p>您好。</p>", material)
    assert "hi@geotag.example.com" in out
    assert out.startswith("<p>您好。</p>")


def test_html_twin_does_not_duplicate():
    material = email_material_from_profile(GEO_TAG)
    body = '<p>邮箱：hi@geotag.example.com<br>官网：https://geotag.example.com</p>'
    assert _ensure_contact_block_html(body, material) == body
