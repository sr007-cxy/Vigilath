"""Letter styles for the no-report intro email: header and footer assembled by code, the middle can be rewritten by the model.

Brands like GEO-TAG **always** take this email (no report capability -> `can_reference_report=False`),
so every case here is a path that always occurs in production, not an edge case.

Pins three things:

1. **Header and footer never go through the model**. Greeting, contact info and signature are all assembled by `assemble_intro_email()`.
   Contact info is a whitelist -- any contact info the model writes is made up; given the greeting, the model will on its own
   append the job title after the company name. Header and footer of all four styles must be identical verbatim; only the middle differs.
2. **The model's middle must be stripped clean before assembly**. The prompt says "don't write these three", and the model writing them anyway is the norm;
   without stripping you get two greetings and two signatures.
3. **The cache keys of the four styles must all differ**. If `report_email_fingerprint(no_report=True)`
   misses one flag, all three model styles fall back to `leijun_pitch` -- switching style hits the previous style's
   already-generated draft, and the user sees "clicked but nothing happened".

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_intro_letter_styles.py -q
"""
from __future__ import annotations

from agents.sales_agent.service.brandprofile import email_material_from_profile
from agents.sales_agent.service.outreach import (
    NO_REPORT_EMAIL_PROMPT_VERSION,
    NO_REPORT_EMAIL_STYLES,
    assemble_intro_email,
    build_no_report_default_body_paragraphs,
    build_no_report_default_email_copy,
    default_intro_subject,
    no_report_email_style_is_llm,
    no_report_email_style_options,
    normalize_no_report_email_style_key,
    report_email_fingerprint,
    sanitize_intro_subject,
    split_intro_body_paragraphs,
    strip_intro_body_affixes,
)

GEO_TAG = {
    "company_short_name": "GEO-TAG",
    "core_message": "GEO Tag 是GEO领域首个中立计量工具",
    "service_features": ["AI 引擎引用监测", "GEO 效果第三方计量"],
    "target_scenarios": ["有官网但搜索获客一般的企业"],
    "contact_email": "hi@geotag.example.com",
    "website": "https://geotag.example.com",
}


def _context(profile: dict = GEO_TAG, company_name: str = "星迈科技") -> dict:
    return {
        "company": {"name": company_name},
        "brand_profile": {"email_material": email_material_from_profile(profile)},
    }


# --- Style table ---

def test_only_standard_skips_the_model():
    assert no_report_email_style_is_llm("standard") is False
    for key in ("scenario_fit", "industry_view", "need_check"):
        assert no_report_email_style_is_llm(key) is True, key


def test_unknown_style_falls_back_to_standard():
    """The fallback must be the assembled style -- when unsure, prefer not going through the model."""
    for raw in ("", "  ", "leijun_pitch", "executive_view", "不存在的类型"):
        assert normalize_no_report_email_style_key(raw) == "standard", raw


def test_style_key_is_case_and_dash_insensitive():
    assert normalize_no_report_email_style_key("Scenario-Fit") == "scenario_fit"


def test_options_payload_carries_what_the_frontend_needs():
    options = no_report_email_style_options()
    assert [o["key"] for o in options] == list(NO_REPORT_EMAIL_STYLES)
    for option in options:
        assert option["label"] and option["description"] and option["mode"] in {"template", "llm"}
        # requirements is a prompt implementation detail, not exposed to the frontend.
        assert "requirements" not in option


def test_llm_styles_all_carry_requirements():
    for key, style in NO_REPORT_EMAIL_STYLES.items():
        assert bool(style["requirements"]) == (style["mode"] == "llm"), key


# --- Header and footer assembled by code, identical across all four styles ---

def test_head_and_tail_are_identical_across_styles():
    context = _context()
    template = assemble_intro_email(context, build_no_report_default_body_paragraphs(context)[0])["content"]
    rewritten = assemble_intro_email(context, ["模型自己写的一段。", "第二段。"])["content"]
    for letter in (template, rewritten):
        assert letter.startswith("星迈科技相关负责人您好：\n\n")
        assert letter.endswith("联系邮箱：hi@geotag.example.com\n官网：https://geotag.example.com\n\n祝好")


def test_model_body_html_is_derived_server_side():
    """The model gives plain text only; the rich-text version is converted by the backend -- no dirty tags from model output."""
    out = assemble_intro_email(_context(), ["第一段。", "第二段\n换行。"])
    assert out["html_content"].startswith("<p>星迈科技相关负责人您好：</p>")
    assert "<p>第一段。</p>" in out["html_content"]
    assert "第二段<br>换行。" in out["html_content"]
    assert out["html_content"].endswith("<p>祝好</p>")


def test_company_name_missing_degrades_to_plain_greeting():
    letter = assemble_intro_email(_context(company_name=""), ["正文。"])["content"]
    assert letter.startswith("您好：\n\n")


def test_standard_copy_is_head_plus_template_body_plus_tail():
    """`build_no_report_default_email_copy` is "template middle + unified assembly"; no second assembly path allowed."""
    context = _context()
    paragraphs, html_parts = build_no_report_default_body_paragraphs(context)
    assert build_no_report_default_email_copy(context) == {
        "subject": default_intro_subject(context),
        **assemble_intro_email(context, paragraphs, html_parts),
    }


# --- Stripping the model's middle ---

def test_model_written_greeting_and_signoff_are_stripped():
    body = "星迈科技相关负责人您好：\n\n正文第一段。\n\n正文第二段。\n\n祝好"
    assert strip_intro_body_affixes(body, _context()) == "正文第一段。\n\n正文第二段。"


def test_model_written_contacts_are_stripped_by_value_and_by_label():
    body = (
        "正文。\n\n"
        "联系邮箱：hi@geotag.example.com\n"
        "官网：https://geotag.example.com\n\n"
        "此致\n敬礼"
    )
    assert strip_intro_body_affixes(body, _context()) == "正文。"


def test_fabricated_contact_line_at_the_tail_is_stripped_too():
    """Contact info made up by the model has the same format but fake content -- strip it by line-start label too."""
    body = "正文。\n\n电话：400-000-0000"
    assert strip_intro_body_affixes(body, _context()) == "正文。"


def test_stripping_keeps_body_sentences_that_merely_mention_contact_words():
    """「邮箱」 ("email") appearing in a sentence doesn't make it a signature line; don't strip the body away."""
    body = "如果贵司这边有相关需求,欢迎直接回复本邮件,或者留个邮箱我们再细聊。"
    assert strip_intro_body_affixes(body, _context()) == body


def test_assembled_letter_has_no_duplicate_greeting_or_signoff():
    """Run strip + assemble together -- that's the actual production path."""
    context = _context()
    raw = "星迈科技相关负责人您好：\n\n正文。\n\n联系邮箱：hi@geotag.example.com\n\n祝好"
    letter = assemble_intro_email(context, split_intro_body_paragraphs(strip_intro_body_affixes(raw, context)))["content"]
    assert letter.count("您好：") == 1
    assert letter.count("祝好") == 1
    assert letter.count("hi@geotag.example.com") == 1


def test_paragraphs_split_on_blank_lines_and_fall_back_to_single_lines():
    assert split_intro_body_paragraphs("A\n\nB\n\nC") == ["A", "B", "C"]
    assert split_intro_body_paragraphs("A\nB\nC") == ["A", "B", "C"]
    assert split_intro_body_paragraphs("   ") == []


# --- Subject gate ---

def test_overlong_or_empty_subject_falls_back_to_the_template():
    context = _context()
    fallback = default_intro_subject(context)
    assert sanitize_intro_subject("", context) == fallback
    just_fits = "关于贵司在AI搜索里的可见性一点想法"  # 18 characters
    assert sanitize_intro_subject(just_fits, context) == just_fits
    assert sanitize_intro_subject(just_fits + "想聊聊", context) == fallback  # 21 characters


def test_exclamation_and_model_added_ad_words_are_rejected():
    context = _context()
    fallback = default_intro_subject(context)
    assert sanitize_intro_subject("关于AI搜索可见性的一点想法！", context) == fallback
    assert sanitize_intro_subject("行业领先的GEO计量方案", context) == fallback


def test_ad_word_the_brand_itself_uses_is_kept():
    """「首个」 ("first") is positioning the customer entered in their profile; blocking it would erase their real positioning."""
    context = _context()
    assert sanitize_intro_subject("GEO领域首个中立计量工具", context) == "GEO领域首个中立计量工具"


def test_normal_subject_passes_through_unquoted():
    assert sanitize_intro_subject(' “关于贵司的AI搜索可见性” ', _context()) == "关于贵司的AI搜索可见性"


# --- Cache key ---

def _fingerprint(style_key: str, no_report: bool = True) -> str:
    return report_email_fingerprint(1, "email", style_key, {"has_real_detection": False}, "", brand={"id": 7}, no_report=no_report)


def test_each_intro_style_gets_its_own_cache_key():
    keys = [_fingerprint(k) for k in NO_REPORT_EMAIL_STYLES]
    assert len(set(keys)) == len(keys), "切类型会直接命中上一种已经生成好的草稿"


def test_without_the_no_report_switch_the_styles_collide():
    """This is the bug itself -- without no_report=True the three styles' fingerprints are identical."""
    assert len({_fingerprint(k, no_report=False) for k in NO_REPORT_EMAIL_STYLES}) == 1


def test_intro_prompt_change_bumps_the_draft_cache_version():
    """Changing the prompt without bumping the version shows up in production as "picked a style but the email didn't change"."""
    assert NO_REPORT_EMAIL_PROMPT_VERSION == "no-report-intro-v2"
