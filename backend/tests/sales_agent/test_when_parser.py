"""`when_text` time parser (计划-2026-09-11-定时发送邮件.md §4.3 / §4.7 boundary table).

This file guards three things, ordered by "how costly a mistake would be":

1. **No silent rollover.** When "send at three today" meets a moment already past three, it must return `is_past=True` so the caller
   asks the user, not quietly move to tomorrow -- quietly moving means a batch of mail goes out tomorrow afternoon without the user expecting it.
2. **Vague times must not pick one on their own.** 「晚点发」 ("send later"), 「有空发一下」 ("send when free"), 「明天发」 ("send tomorrow") are all `WHEN_VAGUE`.
3. **Detection and parsing must be the same code.** `has_time_expression()` (used by (1) and (2b)) and
   `parse_when_text()` (used by tools) share one path, leaving no blind spot of "detection says yes, parsing says no".

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_when_parser.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agents.sales_agent.time_parse import (
    BJ_OFFSET,
    REASON_DATE_WITHOUT_TIME,
    REASON_IMMEDIATE,
    REASON_NO_TIME_EXPRESSION,
    REASON_VAGUE_MARKER,
    WHEN_ABSOLUTE,
    WHEN_NONE,
    WHEN_VAGUE,
    describe_local,
    describe_utc,
    extract_time_expression,
    has_schedule_marker,
    has_time_expression,
    parse_when_text,
    to_local,
    to_utc,
)

# 2026-09-13 is a Sunday, 14:00 Beijing time. An afternoon reference point lets us test both
# "three o'clock" not yet reached and "noon" already passed.
NOW_UTC = datetime(2026, 9, 13, 6, 0)
# 16:30 Beijing time on the same day -- used to test the boundary where "three today" has passed.
LATE_UTC = datetime(2026, 9, 13, 8, 30)


def _local(text: str, now_utc: datetime = NOW_UTC) -> datetime | None:
    return parse_when_text(text, now_utc=now_utc).local_at


# --------------------------------------------------------------- phrasings resolvable to an exact time

@pytest.mark.parametrize(
    "text,expected",
    [
        # The three phrasings named in the first row of the design 4.7 boundary table
        ("明早九点发", datetime(2026, 9, 14, 9, 0)),
        ("一小时后发", datetime(2026, 9, 13, 15, 0)),
        ("今天下班前发了", datetime(2026, 9, 13, 17, 30)),
        # The original sentence from the target conversation
        ("下午三点帮我把刚才推荐的有邮箱的企业发一下邮件", datetime(2026, 9, 13, 15, 0)),
        # Period of day x hour
        ("上午十点发", datetime(2026, 9, 13, 10, 0)),
        ("晚上八点发", datetime(2026, 9, 13, 20, 0)),
        ("凌晨两点发", datetime(2026, 9, 13, 2, 0)),
        ("中午十二点发", datetime(2026, 9, 13, 12, 0)),
        ("中午一点发", datetime(2026, 9, 13, 13, 0)),
        ("傍晚六点发", datetime(2026, 9, 13, 18, 0)),
        # Several ways of writing minutes
        ("三点半发", datetime(2026, 9, 13, 15, 30)),
        ("三点一刻发", datetime(2026, 9, 13, 15, 15)),
        ("三点三刻发", datetime(2026, 9, 13, 15, 45)),
        ("九点整发", datetime(2026, 9, 13, 9, 0)),
        ("下午三点二十发", datetime(2026, 9, 13, 15, 20)),
        ("下午三点20分发", datetime(2026, 9, 13, 15, 20)),
        # Numeric forms
        ("15:30 发", datetime(2026, 9, 13, 15, 30)),
        ("十五点发", datetime(2026, 9, 13, 15, 0)),
        ("明天 09:00 发", datetime(2026, 9, 14, 9, 0)),
        # Dates
        ("明天下午三点发", datetime(2026, 9, 14, 15, 0)),
        ("后天上午九点发", datetime(2026, 9, 15, 9, 0)),
        ("大后天上午九点发", datetime(2026, 9, 16, 9, 0)),
        ("9月20日下午三点发", datetime(2026, 9, 20, 15, 0)),
        ("2026-09-20 10:00 发", datetime(2026, 9, 20, 10, 0)),
        ("三天后下午三点发", datetime(2026, 9, 16, 15, 0)),
        # Relative
        ("30分钟后发", datetime(2026, 9, 13, 14, 30)),
        ("半小时后发", datetime(2026, 9, 13, 14, 30)),
        ("两个半小时后发", datetime(2026, 9, 13, 16, 30)),
        ("两个小时以后发", datetime(2026, 9, 13, 16, 0)),
    ],
)
def test_absolute_expressions(text, expected):
    result = parse_when_text(text, now_utc=NOW_UTC)
    assert result.kind == WHEN_ABSOLUTE, f"{text} 应该能解析出绝对时刻"
    assert result.local_at == expected


def test_utc_and_local_are_the_same_moment():
    """The DB stores naive UTC and displays +8; both must be the same instant."""
    result = parse_when_text("明天下午三点发", now_utc=NOW_UTC)
    assert result.local_at - result.utc_at == BJ_OFFSET
    assert to_utc(result.local_at) == result.utc_at
    assert to_local(result.utc_at) == result.local_at


def test_no_seconds_left_in_the_result():
    """The computed time must land on a whole minute, or the "30 seconds between emails" receipt drifts by half a second."""
    result = parse_when_text("一小时后发", now_utc=NOW_UTC + timedelta(seconds=37))
    assert result.local_at.second == 0 and result.local_at.microsecond == 0


# --------------------------------------------------------------- afternoon heuristic for bare hours

@pytest.mark.parametrize(
    "text,hour",
    [
        ("一点发", 13), ("三点发", 15), ("六点发", 18),   # 1-6 o'clock read as afternoon
        ("七点发", 7), ("九点发", 9), ("十一点发", 11),    # 7-11 o'clock read as morning
        ("十二点发", 12),
        ("十三点发", 13), ("二十点发", 20),               # already 24-hour format, used as-is
    ],
)
def test_bare_hour_heuristic(text, hour):
    """In an office context "send at three" can't be 3 a.m. The heuristic's safety net is that the caller must echo the full time."""
    assert _local(text).hour == hour


def test_two_digit_zero_padded_hour_is_literal():
    """A leading zero in "06:30" = the user is using 24-hour format; don't move it to the afternoon."""
    assert _local("06:30 发") == datetime(2026, 9, 13, 6, 30)
    # Single-digit colloquial forms still use the heuristic
    assert _local("3:30发") == datetime(2026, 9, 13, 15, 30)


def test_midnight_rolls_to_next_day():
    assert _local("晚上十二点发") == datetime(2026, 9, 14, 0, 0)
    assert _local("24点发") == datetime(2026, 9, 14, 0, 0)


# --------------------------------------------------------------- no silent rollover

def test_past_time_is_flagged_not_rolled_forward():
    """Design 4.7: "send at three today" but it's already past three -- point out the time has passed, **don't silently roll over to tomorrow**."""
    result = parse_when_text("今天三点发", now_utc=LATE_UTC)
    assert result.kind == WHEN_ABSOLUTE
    assert result.local_at == datetime(2026, 9, 13, 15, 0), "不许挪到明天"
    assert result.is_past is True
    assert result.ok is False and result.needs_followup is True


def test_bare_past_hour_is_also_flagged():
    """"Send at three" without "today" must not roll over either -- rolling over is choosing tomorrow for the user."""
    result = parse_when_text("三点发", now_utc=LATE_UTC)
    assert result.is_past is True and result.local_at.date() == datetime(2026, 9, 13).date()


def test_future_time_is_not_flagged():
    result = parse_when_text("今天三点发", now_utc=NOW_UTC)
    assert result.is_past is False and result.ok is True


def test_relative_time_is_never_past():
    assert parse_when_text("一小时后发", now_utc=LATE_UTC).is_past is False


def test_month_day_without_year_never_points_backwards():
    """"January 5" without a year: if this year's has passed, it means next year, not nine months ago."""
    result = parse_when_text("1月5日上午十点发", now_utc=NOW_UTC)
    assert result.local_at == datetime(2027, 1, 5, 10, 0)


# --------------------------------------------------------------- vague times must be followed up

@pytest.mark.parametrize(
    "text",
    ["晚点发", "晚些时候发", "稍后发", "待会发", "回头发", "有空发一下",
     "今天找个时间发", "抽空发一下", "方便的时候发", "尽快发", "改天发", "过两天发"],
)
def test_vague_expressions_are_never_guessed(text):
    """Design 4.7: vague times **must prompt for a specific time, never pick one on their own**."""
    result = parse_when_text(text, now_utc=NOW_UTC)
    assert result.kind == WHEN_VAGUE, f"{text} 不该被解析成一个具体时刻"
    assert result.reason == REASON_VAGUE_MARKER
    assert result.local_at is None and result.utc_at is None
    assert result.needs_followup is True


@pytest.mark.parametrize("text", ["明天发", "后天发", "下午发", "晚上发", "上午发", "周三发", "9月20日发"])
def test_date_without_clock_is_vague(text):
    """A date without an hour also "can't be resolved to an exact time". "Send tomorrow" must ask what time, not pick 09:00 for the user."""
    result = parse_when_text(text, now_utc=NOW_UTC)
    assert result.kind == WHEN_VAGUE and result.reason == REASON_DATE_WITHOUT_TIME
    assert result.local_at is None


def test_explicit_clock_beats_a_vague_word():
    """If the user gave an exact time, use it, even if the sentence also has a vague word."""
    result = parse_when_text("晚点吧,就下午三点发", now_utc=NOW_UTC)
    assert result.kind == WHEN_ABSOLUTE and result.local_at == datetime(2026, 9, 13, 15, 0)


# --------------------------------------------------------------- anchors: only accept the narrow ones

@pytest.mark.parametrize(
    "text,expected",
    [
        ("下班前发", datetime(2026, 9, 13, 17, 30)),
        ("下班后发", datetime(2026, 9, 13, 18, 0)),
        ("明天上班发", datetime(2026, 9, 14, 9, 0)),
        ("中午发", datetime(2026, 9, 13, 12, 0)),
    ],
)
def test_anchor_expressions(text, expected):
    assert _local(text) == expected


def test_before_off_work_lands_before_off_work():
    """「下班前」 means "before end of work". Mapping it to 18:00 makes it "at end of work", half an hour late."""
    at = _local("今天下班前发了")
    assert datetime(2026, 9, 13, 9, 0) <= at < datetime(2026, 9, 13, 18, 0)


# --------------------------------------------------------------- "send now"

def test_immediate_still_needs_the_normal_flow():
    """Design 4.7: "send now" goes through the same confirmation flow, just with scheduled_at = now."""
    result = parse_when_text("现在就发", now_utc=NOW_UTC)
    assert result.kind == WHEN_ABSOLUTE and result.reason == REASON_IMMEDIATE
    assert result.is_immediate is True
    assert result.is_past is False, "立即发按定义不算过期,否则每次都要追问一遍"
    assert result.local_at == datetime(2026, 9, 13, 14, 0)


@pytest.mark.parametrize("text", ["现在就发", "立刻发", "马上发", "立即发"])
def test_immediate_variants(text):
    assert parse_when_text(text, now_utc=NOW_UTC).is_immediate is True


def test_explicit_clock_beats_immediate_word():
    """「现在」 ("now") in 「现在下午三点发」 is just a filler word, not "send immediately"."""
    result = parse_when_text("现在下午三点发", now_utc=NOW_UTC)
    assert result.is_immediate is False and result.local_at == datetime(2026, 9, 13, 15, 0)


# --------------------------------------------------------------- one implementation, three callers

@pytest.mark.parametrize(
    "text",
    ["把有邮箱的都发了", "把这批发出去", "帮我写封邮件", "改一下刚才那封", "邮件功能多少钱",
     "推荐 50 家工业制造企业", "我的画像是什么"],
)
def test_sentences_without_any_time_expression(text):
    """(2b) only downgrades this kind of sentence -- the model says scheduled send, but there's not a single time word in it."""
    result = parse_when_text(text)
    assert result.kind == WHEN_NONE and result.reason == REASON_NO_TIME_EXPRESSION
    assert has_time_expression(text) is False


@pytest.mark.parametrize("text", ["明早九点发", "一小时后发", "今天下班前发了", "晚点发", "有空发一下", "明天发"])
def test_vague_counts_as_having_a_time_expression(text):
    """**Vague times also count as "has a time".**

    The right handling of "send later" is to ask for a specific time, not get downgraded by (2b) to writing a draft -- after downgrading the user sees
    the draft panel right after saying "send later". (2b) only downgrades toward safety, but downgrading in the wrong direction is still wrong.
    """
    assert has_time_expression(text) is True


def test_extract_returns_the_original_fragment():
    """`matched` must be echoable to the user verbatim, so it must be a real fragment of the original text, not a normalized string."""
    text = "下午３点帮我发一下"      # full-width digit
    fragment = extract_time_expression(text)
    assert fragment in text, "片段必须能在原句里找到"
    assert fragment == "下午３点"


def test_judgement_and_parsing_can_never_disagree():
    """Item 2 of design 4.3: the detection in (1)/(2b) and the real parsing must be the same code.

    The value of this test: if someday someone adds a new phrasing to `parse_when_text` but forgets to sync the word list,
    this goes red -- because there is no second word list to forget.
    """
    for text in ["明早九点发", "晚点发", "三天后下午三点发", "把有邮箱的都发了"]:
        result = parse_when_text(text)
        assert has_time_expression(text) == bool(result.matched)
        assert (result.kind == WHEN_NONE) == (not has_time_expression(text))


def test_schedule_marker_is_not_a_time_expression():
    """「定时把这批发了」 ("send this batch on a schedule") has scheduling intent but no time.

    (1) hits via "time word OR scheduling word"; inside the tool the parser determines "no specific time can be resolved" and asks
    (last row of design 4.7). Counting 「定时」 ("scheduled") as a time word would mean (2b) could never downgrade again.
    """
    assert has_schedule_marker("定时把这批发了") is True
    assert has_time_expression("定时把这批发了") is False
    assert has_schedule_marker("把这批发了") is False


# --------------------------------------------------------------- echo

def test_describe_local_uses_relative_day_names():
    assert describe_local(datetime(2026, 9, 13, 15, 0), now_utc=NOW_UTC) == "今天 15:00(北京时间)"
    assert describe_local(datetime(2026, 9, 14, 9, 0), now_utc=NOW_UTC) == "明天 09:00(北京时间)"
    assert describe_local(datetime(2026, 9, 15, 9, 0), now_utc=NOW_UTC) == "后天 09:00(北京时间)"
    assert describe_local(datetime(2026, 9, 20, 15, 0), now_utc=NOW_UTC) == "9 月 20 日 15:00(北京时间)"


def test_describe_always_marks_the_timezone():
    """A bare time string lets the model guess the timezone; it went wrong once in production (email_send.py:679)."""
    assert "北京时间" in describe_local(datetime(2026, 9, 13, 15, 0), now_utc=NOW_UTC)
    assert "北京时间" in describe_utc(datetime(2026, 9, 13, 7, 0), now_utc=NOW_UTC)


def test_describe_utc_converts_before_formatting():
    assert describe_utc(datetime(2026, 9, 13, 7, 0), now_utc=NOW_UTC) == "今天 15:00(北京时间)"


def test_describe_handles_empty():
    assert describe_local(None) == "" and describe_utc(None) == ""


# --------------------------------------------------------------- garbage input must not blow up

@pytest.mark.parametrize("text", ["", "   ", None, "99点发", "25:70 发", "第三点", "13月45日发", "一二三"])
def test_garbage_never_raises(text):
    """The parser sits right below user input; raising means the whole conversation turn dies. If unrecognized, say so."""
    result = parse_when_text(text)
    assert result.kind in (WHEN_NONE, WHEN_VAGUE, WHEN_ABSOLUTE)
    if result.kind == WHEN_ABSOLUTE:
        assert 0 <= result.local_at.hour <= 23
