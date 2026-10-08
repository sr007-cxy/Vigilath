"""Streaming middle of the intro email: sent to the frontend **character by character**, while guaranteeing every released character has passed the gate.

This path is only for the three model-rewritten letter styles (scenario hook / industry insight / needs check). Header and footer are still
hard-assembled by code; only the middle streams.

**Characters and paragraphs are different things**: characters are only a frontend preview and never enter the final email; paragraphs are the stripped persistence granularity.
Stripping needs the whole paragraph (greeting at the start, signature and contact info at the end), so each paragraph is sent again once complete, and the frontend uses it
to replace that paragraph's preview.

Pins five things:

1. **Released characters can't be taken back**. They're already drawn on screen; erasing means a flicker. So the preview must always be a
   **prefix** of the final paragraph -- the most central invariant in this file.
2. **A lookback window holds back partial banned words**. The gate is substring matching: 「检测报告显」 isn't a violation, 「检测报告显示」 ("audit report shows")
   is, and releasing char by char would let the first five out first. So the buffer tail holds back the length of the longest banned word,
   releasing only once the following characters arrive and the whole passes the gate.
3. **Line-start probing holds back lines that will be stripped**. Greeting/signature/contact lines are stripped whole and can't be printed first
   then erased. All three regexes are whole-line or line-start anchored with bounded length, so if nothing matches after the probe length, nothing
   ever will -- that's what justifies releasing characters before the line is finished.
4. **Passing the gate on a prefix is equivalent to passing on the whole email**. The intro path (email with `can_reference_report=False`)
   only has "violation on appearance" rules, monotonic over prefixes. The report email path has "missing report link is a violation",
   so prefix gating would flag every unfinished email -- this scheme can't be copied over there.
5. **When the model falls back to JSON, it must not take a different implementation**. Two implementations will eventually disagree on some edge,
   and that disagreement is a violating email being sent.

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_intro_stream.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.service.brandprofile import email_material_from_profile
from agents.sales_agent.service.outreach import (
    _INTRO_PREVIEW_HOLDBACK,
    IntroBodyStream,
    assemble_intro_email,
    default_intro_subject,
    intro_greeting_line,
    intro_stream_from_text,
)

GEO_TAG = {
    "company_short_name": "GEO-TAG",
    "core_message": "GEO Tag 是GEO领域首个中立计量工具",
    "service_features": ["AI 引擎引用监测", "GEO 效果第三方计量"],
    "target_scenarios": ["有官网但搜索获客一般的企业"],
    "contact_email": "hi@geotag.example.com",
    "website": "https://geotag.example.com",
}


@pytest.fixture()
def context() -> dict:
    return {
        "company": {"name": "星迈科技"},
        "brand_profile": {"email_material": email_material_from_profile(GEO_TAG)},
    }


def _drip(stream: IntroBodyStream, text: str, size: int = 7) -> list[str]:
    """Feed it chopped into fixed lengths, simulating a model token stream. Return the paragraphs sent along the way."""
    out: list[str] = []
    for i in range(0, len(text), size):
        out.extend(stream.push(text[i:i + size]).paragraphs)
    out.extend(stream.finish().paragraphs)
    return out


BODY = "SUBJECT: 关于AI搜索可见性\n\n第一段正文,说对方的处境。\n\n第二段正文,带出品牌能力。\n\n第三段正文,结尾一个提问。"


# --- Paragraph boundaries ---

def test_paragraphs_publish_only_on_blank_lines(context):
    """Half sentences must not be sent -- every released character must have passed the gate."""
    stream = IntroBodyStream(context)
    assert stream.push("SUBJECT: 关于AI搜索可见性\n\n第一段还没").paragraphs == []
    assert stream.push("写完").paragraphs == []
    assert stream.push("。\n\n").paragraphs == ["第一段还没写完。"]


def test_dripping_tokens_yields_the_same_paragraphs_as_one_shot(context):
    """Feeding chopped and feeding whole must match, or the streamed copy and the persisted copy diverge."""
    dripped = _drip(IntroBodyStream(context), BODY, size=3)
    one_shot = IntroBodyStream(context)
    one_shot.push(BODY)
    one_shot.finish()
    assert dripped == one_shot.paragraphs
    assert dripped == ["第一段正文,说对方的处境。", "第二段正文,带出品牌能力。", "第三段正文,结尾一个提问。"]


def test_finish_flushes_the_last_paragraph(context):
    """The last paragraph has no trailing blank line; without flush the whole paragraph is lost."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n第一段。\n\n最后一段没有结尾空行。")
    assert stream.finish().paragraphs == ["最后一段没有结尾空行。"]
    assert stream.paragraphs == ["第一段。", "最后一段没有结尾空行。"]


def test_a_single_blob_without_blank_lines_still_splits(context):
    """When the model writes no blank lines at all, split by single lines -- don't let the whole email clump together."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n第一句。\n第二句。\n第三句。")
    stream.finish()
    assert stream.paragraphs == ["第一句。", "第二句。", "第三句。"]


# --- Subject ---

def test_subject_comes_from_the_first_line(context):
    stream = IntroBodyStream(context)
    update = stream.push("SUBJECT: 关于AI搜索可见性\n")
    assert update.subject == "关于AI搜索可见性"


def test_subject_line_with_only_one_newline_does_not_eat_the_first_paragraph(context):
    """Models often put just one newline after the SUBJECT line -- that's why the subject is taken by line."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 关于AI搜索可见性\n第一段正文。\n\n")
    assert stream.subject == "关于AI搜索可见性"
    assert stream.paragraphs == ["第一段正文。"]


def test_an_advertising_subject_falls_back_to_the_template(context):
    """The subject gate is the same as on the non-streaming path -- too long or with exclamation marks, it's replaced with the template subject."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 最后机会！立减！错过再等一年错过再等一年\n")
    assert stream.subject == default_intro_subject(context)


def test_no_subject_line_means_template_subject_and_the_first_line_stays_body(context):
    stream = IntroBodyStream(context)
    stream.push("第一段正文,模型忘了写标题行。\n\n第二段。\n\n")
    assert stream.subject == default_intro_subject(context)
    assert stream.paragraphs[0] == "第一段正文,模型忘了写标题行。"


# --- Per-paragraph stripping ---

@pytest.mark.parametrize("junk", [
    "星迈科技相关负责人您好：",
    "祝好",
    "此致",
    "联系邮箱：hi@geotag.example.com",
    "官网：https://geotag.example.com",
])
def test_model_written_head_and_tail_paragraphs_are_dropped(context, junk):
    """The prompt says "don't write these three", and the model writing them anyway is the norm. Without stripping you get two greetings and two signatures."""
    stream = IntroBodyStream(context)
    stream.push(f"SUBJECT: 标题\n\n{junk}\n\n真正的正文。\n\n")
    assert stream.paragraphs == ["真正的正文。"]


def test_the_assembled_letter_has_exactly_one_greeting_and_one_signoff(context):
    """Streamed paragraphs and the ones used for assembly are the same copy -- no double greeting after assembly."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n星迈科技相关负责人您好：\n\n真正的正文。\n\n祝好\n")
    stream.finish()
    content = assemble_intro_email(context, stream.paragraphs)["content"]
    assert content.count(intro_greeting_line(context)) == 1
    assert content.count("祝好") == 1


# --- Prefix gating ---

def test_a_violating_paragraph_stops_the_stream(context):
    """Stop as soon as the dirty paragraph appears, nothing after is sent -- blocks earlier than whole-email gating, but blocks the same set."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n干净的第一段。\n\n")
    update = stream.push("检测报告显示贵司官网总分只有 40 分。\n\n")
    assert update.violations, "无报告的信里写诊断结论必须被拦"
    assert stream.push("后面还有一段。\n\n").paragraphs == [], "拦下之后不能再往外发"


def test_a_clean_letter_passes_the_gate(context):
    stream = IntroBodyStream(context)
    _drip(stream, BODY)
    assert stream.violations == []


def test_impersonating_the_platform_is_caught_mid_stream(context):
    """When the sender isn't Vigilath, any Vigilath in the email is blocked -- the model can't touch this flag."""
    stream = IntroBodyStream(context, {"sender_is_vigilath": False})
    stream.push("SUBJECT: 标题\n\n我们是 Vigilath 官方合作方。\n\n")
    assert stream.violations


# --- Char-by-char preview ---
#
# Paragraphs are still the persistence granularity (stripping needs the whole paragraph); characters are only the **preview** granularity. Different semantics:
# `chunks` don't go into `paragraphs`, and when the frontend gets the same paragraph's `paragraph` it replaces the whole preview.


# A sufficiently long body paragraph. Short paragraphs get held back entirely by the line-start probe window, so char-by-char can't be observed -- real models write
# middles this long; testing with three short lines would test a scenario that never happens.
LONG = (
    "SUBJECT: 关于AI搜索可见性\n\n"
    "很多制造业企业现在都遇到同一个问题:官网做得并不差,但客户在AI里问起同类产品的时候,答案里始终没有出现自己的名字。\n\n"
    "我这边是GEO-TAG,做的正好是这一段的计量:把AI引擎给出的回答抓下来,看看里面到底引用了谁、引用的又是哪一个页面。\n\n"
    "如果贵司这边也关心这件事,可以回信约个时间聊聊;不合适也请直接告诉我,我不会再打扰。"
)


def _previews(stream: IntroBodyStream, text: str, size: int = 1) -> list[str]:
    """Group previews by paragraph. Group i is the characters released one by one while paragraph i was being written, joined.

    Within one `push`, paragraph frames come before preview frames (persist the finished paragraph first, then preview the new one),
    and grouping follows that order, otherwise the new paragraph's characters would be counted at the head of the previous one.
    """
    groups: list[str] = [""]
    for i in range(0, len(text), size):
        update = stream.push(text[i:i + size])
        groups.extend("" for _ in update.paragraphs)
        groups[-1] += "".join(update.chunks)
    groups.extend("" for _ in stream.finish().paragraphs)
    return groups[:len(stream.paragraphs)]


def test_the_preview_is_always_a_prefix_of_the_final_paragraph(context):
    """The most central one: released characters can't be taken back. The frontend appends; anything that isn't a prefix must be erased."""
    for size in (1, 3, 7, 40):
        stream = IntroBodyStream(context)
        previews = _previews(stream, LONG, size)
        assert len(previews) == len(stream.paragraphs) == 3
        for preview, para in zip(previews, stream.paragraphs):
            assert para.startswith(preview), f"切成 {size} 字一块时预览不是段落的前缀"


def test_the_preview_comes_out_one_character_at_a_time(context):
    """When the model emits character by character, the frontend should receive character by character -- the whole point of this change."""
    stream = IntroBodyStream(context)
    chunks: list[str] = []
    for ch in LONG:
        chunks.extend(stream.push(ch).chunks)
    stream.finish()
    # Each paragraph starts with an accumulated chunk (line-start probe window + lookback window together), then one character per chunk.
    bursts = [c for c in chunks if len(c) > 1]
    assert len(bursts) <= len(stream.paragraphs), f"除了每段开头那一片,其余都该是单字:{bursts}"
    singles = [c for c in chunks if len(c) == 1]
    assert len(singles) > 60, f"单字片太少了,没在逐字发:{len(singles)}"


def test_the_preview_covers_the_paragraph_except_the_holdback(context):
    """Only the trailing lookback window is held back. Holding more makes "char by char" a misnomer, with most of a paragraph waiting for the end."""
    stream = IntroBodyStream(context)
    previews = _previews(stream, LONG)
    for preview, para in zip(previews, stream.paragraphs):
        held = len(para) - len(preview)
        assert held <= _INTRO_PREVIEW_HOLDBACK + 1, f"这一段扣了 {held} 个字,超出回退窗口"


def test_a_half_written_banned_phrase_never_reaches_the_preview(context):
    """The gate is substring matching. Without the lookback window 「检测报告显」 would show first, and only on the next character would it be too late.

    The window is the length of the longest banned word, so the first character of any banned word is guaranteed to still be in the window --
    not "likely in time", but guaranteed by length.
    """
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n")
    shown = ""
    lead = "很多制造业企业现在都遇到同一个问题:官网做得并不差,但客户在AI里问起同类产品的时候,答案里始终没有出现自己的名字。"
    for ch in lead + "检测报告显示贵司官网总分只有 40 分。":
        update = stream.push(ch)
        shown += "".join(update.chunks)
        if update.violations:
            break
    assert stream.violations, "违禁词写全了就该拦下"
    assert shown, "拦下之前正常的那段正文要照常逐字出来"
    assert "检测报告" not in shown, "半截违禁词也不能露出来"


def test_a_violating_paragraph_stops_the_preview_before_it_closes(context):
    """Gating moves up to every push: when a dirty word appears the paragraph isn't finished, but from that moment not a single character may be released."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n干净的第一句。")
    update = stream.push("检测报告显示贵司官网总分只有 40 分。")
    assert update.violations
    assert update.chunks == [], "违规那下不能跟着发字"
    assert stream.push("后面还有话。").chunks == [], "拦下之后不能再往外发"


def test_the_gate_sees_the_whole_letter_so_far(context):
    """A banned word spanning pushes is missed if only the new piece is checked -- gating must use "persisted paragraphs + this paragraph so far"."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n上一句先垫一下。")
    assert stream.push("我们是 Vigilath 官方合作方。").violations

    stream2 = IntroBodyStream(context, {"sender_is_vigilath": False})
    stream2.push("SUBJECT: 标题\n\n垫一句。")
    assert stream2.push("我们是 Vigilath 的合作方。").violations


def test_finish_publishes_no_chunks(context):
    """The final call sends only paragraphs. Sending the remaining characters again makes the frontend show the last paragraph twice."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n最后一段没有空行结尾。")
    update = stream.finish()
    assert update.paragraphs == ["最后一段没有空行结尾。"]
    assert update.chunks == []


def test_the_preview_never_leaks_into_the_letter(context):
    """Previews are just previews. Persistence still uses paragraphs; the two must not contaminate each other."""
    stream = IntroBodyStream(context)
    _previews(stream, BODY)
    assert stream.paragraphs == ["第一段正文,说对方的处境。", "第二段正文,带出品牌能力。", "第三段正文,结尾一个提问。"]


def test_adding_the_preview_does_not_change_the_paragraphs(context):
    """Preview is an added layer; paragraph semantics must stay exactly the same -- the earlier assertions rely on this to keep holding."""
    dripped = IntroBodyStream(context)
    one_shot = IntroBodyStream(context)
    one_shot.push(BODY)
    one_shot.finish()
    assert _drip(dripped, BODY) == one_shot.paragraphs
    assert dripped.paragraphs == one_shot.paragraphs


def test_a_greeting_line_is_skipped_and_the_body_still_flows(context):
    """Greeting lines the model writes itself are stripped by `strip_intro_body_affixes`; the preview must skip them too.

    After skipping, the body must stream char by char as usual -- just not released, not stuck from then on.
    """
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n")
    assert stream.push("星迈科技相关负责人您好：\n").chunks == []
    shown = "".join(stream.push(
        "很多制造业企业现在都遇到同一个问题:官网做得并不差,但客户在AI里问起同类产品的时候,答案里始终没有自己的名字。"
    ).chunks)
    assert shown, "称呼行跳过之后,正文要照常往外放"
    assert "您好" not in shown, "跳过去的称呼行不能又冒出来"


@pytest.mark.parametrize("junk", ["祝好", "联系邮箱：hi@geotag.example.com"])
def test_a_signoff_or_contact_line_holds_the_preview(context, junk):
    """Signature and contact lines are stripped whole **at the paragraph end**, and whether it's the end is only known once the paragraph completes.

    If undecidable, hold it back -- printing then erasing is the ugliest option. The cost is the rest of this paragraph also isn't previewed
    and pops out whole at the end: the model is told not to write these two anyway, so writing them is abnormal, and slightly less animation on the abnormal path
    beats a flicker.
    """
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n")
    assert stream.push(junk + "\n").chunks == []
    assert stream.push("后面还有很长的一段正文" * 4).chunks == [], "判不了就扣着,整段都不再预览"
    # This time it wasn't stripped (more body follows, it's not at the paragraph end), so holding back really did cost a paragraph of animation for nothing.
    # Pinning this result shows how big the cost is: only animation is lost, the email itself is identical to the character.
    assert "".join(stream.push("\n\n").paragraphs).startswith(junk)


def test_a_contact_value_in_mid_line_stops_the_preview_there(context):
    """A real email/URL from the profile appearing in a line stops right there: if this line is the paragraph end, the whole line will be stripped."""
    stream = IntroBodyStream(context)
    stream.push("SUBJECT: 标题\n\n")
    stream.push("这一段先正常写一句够长的正文,把行首的探测窗口撑过去,然后再放联系方式。")
    shown = "".join(stream.push("详情见 https://geotag.example.com 这个地址。").chunks)
    assert "geotag.example.com" not in shown


# --- Model falls back to JSON ---

def test_json_output_publishes_nothing_but_keeps_the_raw(context):
    """JSON can't be streamed while written (unparseable until braces close); accumulate it for the caller to parse."""
    raw = '{"subject":"关于AI搜索可见性","body":"第一段。\\n\\n第二段。"}'
    stream = IntroBodyStream(context)
    _drip(stream, raw)
    assert stream.json_mode is True
    assert stream.paragraphs == []
    assert stream.raw == raw


def test_intro_stream_from_text_runs_the_same_gates(context):
    """After parsing, the JSON path must go back through the same stripping and gate, not a separate copy."""
    stream = intro_stream_from_text(context, "关于AI搜索可见性", "星迈科技相关负责人您好：\n\n真正的正文。\n\n祝好")
    assert stream.subject == "关于AI搜索可见性"
    assert stream.paragraphs == ["真正的正文。"]

    dirty = intro_stream_from_text(context, "标题", "检测报告显示贵司官网总分只有 40 分。")
    assert dirty.violations


def test_intro_stream_from_text_without_a_subject_uses_the_template(context):
    stream = intro_stream_from_text(context, "", "正文。")
    assert stream.subject == default_intro_subject(context)


# --- Parsing after the prompt format change (also used by the non-streaming path) ---

def test_parse_subject_line_format():
    from agents.sales_agent.api import _parse_intro_model_output

    parsed = _parse_intro_model_output("SUBJECT: 关于AI搜索可见性\n\n第一段。\n\n第二段。")
    assert parsed == {"subject": "关于AI搜索可见性", "body": "第一段。\n\n第二段。"}


def test_parse_still_accepts_json():
    """Even after the format change some fraction of models still emit JSON -- parsing it as plain text stuffs the whole JSON into the email."""
    from agents.sales_agent.api import _parse_intro_model_output

    parsed = _parse_intro_model_output('```json\n{"subject":"标题","body":"第一段。"}\n```')
    assert parsed == {"subject": "标题", "body": "第一段。"}


def test_parse_bare_text_keeps_everything_as_body():
    """Without even a subject line, treat the whole thing as body -- stripping and the compliance gate still back it up, closer to user intent than downgrading."""
    from agents.sales_agent.api import _parse_intro_model_output

    parsed = _parse_intro_model_output("第一段。\n\n第二段。")
    assert parsed == {"subject": "", "body": "第一段。\n\n第二段。"}


def test_the_stream_endpoint_is_registered():
    from agents.sales_agent.api import router

    paths = {route.path for route in router.routes}
    assert "/sales-agent/outreach/drafts/auto/stream" in paths
    assert "/sales-agent/outreach/drafts/auto" in paths, "流式是新加的一条,原来那条不能被顶掉"
