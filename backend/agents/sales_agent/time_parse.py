"""`when_text` -> absolute time: one implementation, three callers.

Callers (scheduled sending design 4.3):

1. Rule Guard (1) in `agent.py`: the time vocabulary of `_is_send_schedule_request`;
2. Post-validation (2b) in `flow/intent.py`: forced downgrade when classified as `send_schedule_create` but no time can be extracted;
3. `schedule_outreach_send` / `reschedule_send` in `tools.py`: actual parsing.

**All three must share this one implementation.** Separate copies create the dead zone "(1) does not catch it, the model does, (2b) downgrades it again":
the user clearly stated a time but it was treated as drafting. So (1) and (2b) only call `has_time_expression()`,
and no one may copy the vocabulary elsewhere.

Two hard rules:

- **The model must not emit ISO timestamps itself.** It only puts the time fragment from the user's words verbatim into `when_text`;
  parsing and timezone conversion all happen here. See the comment at `service/email_send.py:679`: timezones already
  broke once in production (2026-08-23, the same tool used two different times in two answers), and letting the model compute it would hand
  it back again.
- **If it cannot be parsed, say so; never pick a time yourself.** 「晚点发」「有空发一下」「明天发」 ("send later", "send when free", "send tomorrow")
  all return `WHEN_VAGUE`, and the caller asks for a specific time. A wrong guess means a batch of emails actually going out at a time the user
  never expected, which is in a different league from "doing one thing less".

Timezone metric definition matches the rest of the DB: **store naive UTC, display +8**. Asia/Shanghai has had no daylight saving
since 1991, so a fixed +8 matches zoneinfo exactly for future times; this follows the repo's existing
approach (`_bj_day_start_utc` at `api.py:771`) and does not introduce zoneinfo, to avoid two coexisting timezone definitions,
which would be the source of the next breakage.

Handling ambiguity: whatever can be pinned to a specific time is pinned, **and then the caller echoes the full time back to the user to verify**
(first row of the design 4.7 boundary table). The echo is the fallback for all heuristic rules here; without an echo, do not use this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Same metric definition as `api.py` / `service/email_send.py`; do not write another one elsewhere.
BJ_OFFSET = timedelta(hours=8)

# Three shapes of parse result.
WHEN_ABSOLUTE = "absolute"  # Pinned to a specific time
WHEN_VAGUE = "vague"        # The sentence refers to a time, but not a precise one: ask, do not guess
WHEN_NONE = "none"          # The sentence contains no time expression at all

# Machine-readable reasons, for logs and wording branches (the wording itself is in prompts.py, not here).
REASON_OK = "ok"
REASON_IMMEDIATE = "immediate"                    # 「现在就发」 ("send now")
REASON_VAGUE_MARKER = "vague_marker"              # 「晚点」「有空」「找个时间」 ("later", "when free", "sometime")
REASON_DATE_WITHOUT_TIME = "date_without_time"    # 「明天发」「下午发」 ("send tomorrow", "send this afternoon"): date/period but no clock time
REASON_NO_TIME_EXPRESSION = "no_time_expression"  # 「把有邮箱的都发了」 ("send to everyone with an email")


@dataclass(frozen=True)
class WhenParseResult:
    """Parse result. `utc_at` can be written directly to `sales_agent_send_schedules.scheduled_at`."""

    kind: str
    reason: str
    matched: str = ""                 # Matched fragment of the original words, for echo and logs
    local_at: datetime | None = None  # naive, Beijing time
    utc_at: datetime | None = None    # naive UTC
    is_past: bool = False             # The parsed time has already passed: **do not silently roll over to tomorrow**, ask the user
    is_immediate: bool = False        # "Send now"; still goes through the full preview + confirmation, no skipping

    @property
    def ok(self) -> bool:
        """Pinned to a future time; a plan can be created directly."""
        return self.kind == WHEN_ABSOLUTE and not self.is_past

    @property
    def needs_followup(self) -> bool:
        """Must go back and ask the user: vague, no time given, or the given time has passed."""
        return not self.ok


# ---------------------------------------------------------------- vocabularies
# The tables below are the **only** copy shared by (1)/(2b)/tools. Adding a word takes effect in all three places at once.

# Scheduling action words. **Not time words**: 「定时把这批发了」 ("schedule this batch to send") has scheduling intent but no time;
# (1) matches on "time word OR scheduling word", and once in the tool this module judges it vague and asks (last row of design 4.7).
SCHEDULE_MARKERS = (
    "定时", "定个时间", "排期", "排个时间", "安排时间", "到点", "按时",
    "预约发送", "定时发送", "计划发送", "排一下时间",
)

# "Immediately". Goes through the same confirmation flow, just with scheduled_at = now (design 4.7 boundary table).
_IMMEDIATE = ("现在就", "现在", "立刻", "立即", "马上", "即刻", "这就")

# Vague times: refer to a time but cannot land on a precise one. **Must ask; never pick one yourself.**
_VAGUE = (
    "晚点", "晚些", "稍后", "稍晚", "待会", "呆会", "一会儿", "一会", "过会",
    "回头", "有空", "有时间", "找个时间", "找时间", "抽空", "方便的时候", "方便时",
    "尽快", "赶紧", "随时", "改天", "择日", "过两天", "这两天", "近期", "合适的时候",
    "看情况", "有机会",
)

# Date offsets. Includes combined date+period words like `明早` / `今晚` ("tomorrow morning" / "tonight"); they also appear in the period table,
# and the two tables are scanned separately, together yielding "tomorrow + morning".
_DAY_OFFSETS = {
    "大前天": -3, "前天": -2, "昨天": -1, "昨日": -1, "昨晚": -1,
    "今天": 0, "今日": 0, "本日": 0, "今晚": 0, "今早": 0, "今晨": 0, "今夜": 0,
    "明天": 1, "明日": 1, "明儿": 1, "明早": 1, "明晚": 1, "明晨": 1,
    "后天": 2, "大后天": 3,
}

# Periods of day. The value is the category for "how to convert a bare hour to 24-hour time"; see `_apply_period()`.
_PERIODS = {
    "凌晨": "dawn", "半夜": "dawn", "深夜": "dawn",
    "清晨": "am", "早晨": "am", "早上": "am", "一早": "am", "上午": "am",
    "今早": "am", "今晨": "am", "明早": "am", "明晨": "am",
    "中午": "noon", "晌午": "noon", "午间": "noon",
    "下午": "pm", "午后": "pm",
    "傍晚": "eve", "晚上": "eve", "晚间": "eve", "夜里": "eve", "夜间": "eve",
    "今晚": "eve", "明晚": "eve", "昨晚": "eve", "今夜": "eve",
}

# Anchored expressions: although "periods", in everyday Chinese they point to a narrow enough moment to pin down.
# **Only these few**. 「下午发」「晚上发」 ("send in the afternoon/evening") span four or five hours and always go vague to ask.
# 「下班前」 ("before end of work") is explicitly required to be parsed by the design 4.7 boundary table, read as 17:30: in everyday Chinese 「下班前」
# means before the moment work ends; mapping it to 18:00 would make it "at end of work", half an hour late.
_ANCHORS = {
    "下班前": (17, 30),
    "下班后": (18, 0),
    "下班": (17, 30),
    "上班": (9, 0),
    "上班后": (9, 0),
    "中午": (12, 0),
    "午休": (12, 0),
    "午饭": (12, 0),
}

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM_CHARS = "".join(_CN_DIGITS) + "十"
_NUM = rf"(?:\d{{1,2}}|[{_CN_NUM_CHARS}]{{1,3}})"

# Full-width digits/colons/spaces are all folded to half-width. **Must be a 1:1 character mapping**: `matched` relies on indices
# to take the fragment from the original text, and any length change misaligns the fragment.
_NORMALIZE = str.maketrans("０１２３４５６７８９：　", "0123456789: ")

# "15:30". Two-digit minutes are mandatory, so half-typed input like "3:5" is not accepted.
_HHMM_RE = re.compile(r"(?<!\d)(?P<h>\d{1,2}):(?P<m>\d{2})(?!\d)")
# 「三点半」「3点15分」「十五点」「九点整」 ("half past three", "3:15", "fifteen o'clock", "nine o'clock sharp")
_DIAN_RE = re.compile(rf"(?P<h>{_NUM})\s*[点時时](?:\s*(?P<rest>半|一刻|三刻|整|{_NUM}\s*分?))?")
# 「一小时后」「30分钟后」「两个半小时以后」「三天后」 ("in an hour", "in 30 minutes", "in two and a half hours", "in three days")
_REL_RE = re.compile(
    rf"(?P<n>{_NUM}|半)\s*(?:个)?\s*(?P<half>半)?\s*(?P<unit>小时|钟头|分钟|天|周|星期|礼拜)\s*(?:之后|以后|后)"
)
# 「下周三」「周五」「礼拜天」 ("next Wednesday", "Friday", "Sunday")
_WEEK_RE = re.compile(r"(?P<pre>下下|下|这|本|上)?\s*(?:周|星期|礼拜)\s*(?P<d>[一二三四五六日天七])")
# 「9月20日」「9月20号」 ("September 20")
_MD_RE = re.compile(rf"(?P<mon>{_NUM})\s*月\s*(?P<day>{_NUM})\s*[日号]")
# "2026-09-20"
_YMD_RE = re.compile(r"(?<!\d)(?P<y>20\d{2})[-/](?P<mon>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)")

_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "七": 6}


def _cn_int(text: str) -> int | None:
    """Chinese numerals -> int, only 0-99. Returns None if unrecognized (not 0, since 0 is a valid hour)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        if (left and left not in _CN_DIGITS) or (right and right not in _CN_DIGITS):
            return None
        return (_CN_DIGITS[left] if left else 1) * 10 + (_CN_DIGITS[right] if right else 0)
    # Concatenations like 「二三」 are not accepted: in Chinese they do not mean 23, and accepting them would parse a malformed sentence into a time.
    return _CN_DIGITS.get(text) if len(text) == 1 else None


def _find_token(text: str, tokens) -> tuple[str, int, int] | None:
    """Earliest first, longest first at the same position. Returns (word, start, end).

    These two rules are what make 「大后天」 ("in three days") override 「后天」 ("the day after tomorrow") and 「一会儿」 override 「一会」 ("in a moment").
    """
    best: tuple[str, int, int] | None = None
    for token in tokens:
        idx = text.find(token)
        if idx < 0:
            continue
        if best is None or idx < best[1] or (idx == best[1] and len(token) > best[2] - best[1]):
            best = (token, idx, idx + len(token))
    return best


def _apply_period(kind: str, hour: int) -> tuple[int, int]:
    """Period + bare hour -> (24-hour hour, day carry)."""
    if kind == "dawn":  # 12 in the early morning = 0:00
        return (0 if hour == 12 else hour), 0
    if kind == "am":  # 12 in the morning = 12 noon
        return (12 if hour == 12 else hour), 0
    if kind == "noon":  # 1 at noon = 13:00, 11 at noon = 11:00
        return (hour + 12 if 1 <= hour <= 4 else hour), 0
    if kind == "pm":  # 12 in the afternoon = 12:00
        return (hour + 12 if 1 <= hour <= 11 else hour), 0
    if kind == "eve":  # 12 in the evening = 0:00 the next day
        if hour == 12:
            return 0, 1
        return (hour + 12 if hour < 12 else hour), 0
    return hour, 0


def _bare_hour(hour: int) -> tuple[int, int]:
    """A bare hour without a period word.

    In an office context "send at three" is almost never 3 a.m., so 1-6 are read as afternoon; 7-11 as morning.
    This is a heuristic, backstopped by the caller echoing the full time (first row of the design 4.7 boundary table).
    """
    if hour == 24:
        return 0, 1
    if 1 <= hour <= 6:
        return hour + 12, 0
    return hour, 0


@dataclass(frozen=True)
class _Clock:
    hour: int       # Raw value, period not applied yet, unless `literal` is true
    minute: int
    literal: bool   # Already in 24-hour notation; must not be shifted to the afternoon
    start: int
    end: int


def _parse_clock(text: str) -> _Clock | None:
    """Extract hour and minute. Returns None if unrecognized."""
    match = _HHMM_RE.search(text)
    if match:
        hour, minute = int(match.group("h")), int(match.group("m"))
        if hour > 24 or minute > 59:
            return None
        # "06:30" with a leading zero, or already >= 13: this is 24-hour notation and the user has been explicit.
        # "3:30" has a single digit and, like 「三点半」 ("half past three"), is colloquial, so it still goes through the `_bare_hour()` heuristic.
        literal = hour >= 13 or len(match.group("h")) == 2
        return _Clock(hour, minute, literal, match.start(), match.end())

    match = _DIAN_RE.search(text)
    if not match:
        return None
    hour = _cn_int(match.group("h"))
    if hour is None or hour > 24:
        return None
    rest = (match.group("rest") or "").strip()
    if rest == "半":
        minute = 30
    elif rest == "一刻":
        minute = 15
    elif rest == "三刻":
        minute = 45
    elif rest in ("", "整"):
        minute = 0
    else:
        minute = _cn_int(rest.rstrip("分").strip())
        if minute is None or minute > 59:
            return None
    return _Clock(hour, minute, hour >= 13, match.start(), match.end())


def _weekday_offset(match: re.Match, today: datetime) -> int | None:
    target = _WEEKDAY_INDEX.get(match.group("d"))
    if target is None:
        return None
    pre = match.group("pre") or ""
    if pre in ("下", "下下"):
        # In Chinese 「下周三」 ("next Wednesday") = Wednesday of the next calendar week, not "seven days from now".
        monday = today + timedelta(days=7 - today.weekday())
        if pre == "下下":
            monday += timedelta(days=7)
        return (monday + timedelta(days=target) - today).days
    if pre == "上":
        monday = today - timedelta(days=today.weekday() + 7)
        return (monday + timedelta(days=target) - today).days
    # Bare 「周三」/「本周三」 ("Wednesday" / "this Wednesday"): this week if not yet reached, next week if already past; never points backwards.
    return (target - today.weekday()) % 7


def parse_when_text(text: str, *, now_utc: datetime | None = None) -> WhenParseResult:
    """Parse an utterance (or a time fragment) into an absolute time.

    `now_utc` is naive UTC, only passed in tests; production always uses `datetime.utcnow()`.
    """
    raw = str(text or "")
    normalized = raw.translate(_NORMALIZE).lower()
    if not normalized.strip():
        return WhenParseResult(kind=WHEN_NONE, reason=REASON_NO_TIME_EXPRESSION)

    now_utc = now_utc or datetime.utcnow()
    now_local = (now_utc + BJ_OFFSET).replace(microsecond=0)

    def _result(local_at: datetime, spans, *, immediate: bool = False, reason: str = REASON_OK) -> WhenParseResult:
        local_at = local_at.replace(second=0, microsecond=0)
        start = min(s for s, _ in spans)
        end = max(e for _, e in spans)
        return WhenParseResult(
            kind=WHEN_ABSOLUTE,
            reason=reason,
            matched=raw[start:end],
            local_at=local_at,
            utc_at=local_at - BJ_OFFSET,
            # "Send now" by definition never counts as past, even if a few seconds pass between parsing and execution.
            is_past=(not immediate) and local_at < now_local,
            is_immediate=immediate,
        )

    # (1) "Send now". Comes first: it is the only absolute time that needs no date/time words.
    immediate = _find_token(normalized, _IMMEDIATE)
    clock = _parse_clock(normalized)
    if immediate and clock is None:
        return _result(now_local, [(immediate[1], immediate[2])], immediate=True, reason=REASON_IMMEDIATE)

    spans: list[tuple[int, int]] = []
    day_offset: int | None = None
    explicit_date: datetime | None = None

    # (2) Relative time. 「一小时后」 ("in an hour") is a precise moment, computed and returned directly; 「三天后」 ("in three days") only gives a date,
    #    so we keep looking for a clock time; otherwise we would be picking a time for the user using "the current hour and minute".
    rel = _REL_RE.search(normalized)
    if rel:
        count = 0.5 if rel.group("n") == "半" else _cn_int(rel.group("n"))
        if count is not None:
            if rel.group("half") and rel.group("n") != "半":
                count += 0.5
            unit = rel.group("unit")
            if unit in ("小时", "钟头"):
                return _result(now_local + timedelta(hours=count), [(rel.start(), rel.end())])
            if unit == "分钟":
                return _result(now_local + timedelta(minutes=count), [(rel.start(), rel.end())])
            day_offset = int(count) if unit == "天" else int(count) * 7
            spans.append((rel.start(), rel.end()))

    # (3) Explicit dates. year-month-day > month-day > weekday > today/tomorrow.
    if day_offset is None:
        ymd = _YMD_RE.search(normalized)
        md = _MD_RE.search(normalized)
        week = _WEEK_RE.search(normalized)
        day_token = _find_token(normalized, _DAY_OFFSETS)
        if ymd:
            try:
                explicit_date = datetime(int(ymd.group("y")), int(ymd.group("mon")), int(ymd.group("day")))
            except ValueError:
                explicit_date = None
            if explicit_date:
                spans.append((ymd.start(), ymd.end()))
        elif md:
            month, day = _cn_int(md.group("mon")), _cn_int(md.group("day"))
            if month and day:
                # 「9月20日」 ("September 20") without a year: if this year's date has passed it means next year; never points backwards.
                for year in (now_local.year, now_local.year + 1):
                    try:
                        candidate = datetime(year, month, day)
                    except ValueError:
                        break
                    if candidate.date() >= now_local.date():
                        explicit_date = candidate
                        break
                if explicit_date:
                    spans.append((md.start(), md.end()))
        elif week:
            offset = _weekday_offset(week, now_local)
            if offset is not None:
                day_offset = offset
                spans.append((week.start(), week.end()))
        elif day_token:
            day_offset = _DAY_OFFSETS[day_token[0]]
            spans.append((day_token[1], day_token[2]))

    period = _find_token(normalized, _PERIODS)
    if period:
        spans.append((period[1], period[2]))

    base_date = explicit_date or (now_local + timedelta(days=day_offset or 0))
    base_date = base_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # (4) Clock time.
    if clock is not None:
        hour, minute, carry = clock.hour, clock.minute, 0
        spans.append((clock.start, clock.end))
        if immediate:
            # 「现在三点发」 ("now, send at three"): the user gave a time; 「现在」 ("now") is just a filler word, not send-immediately.
            spans.append((immediate[1], immediate[2]))
        if hour == 24:
            hour, carry = 0, 1
        elif clock.literal:
            pass  # 24-hour notation, use as-is
        elif period:
            hour, carry = _apply_period(_PERIODS[period[0]], hour)
        elif hour != 0:
            hour, carry = _bare_hour(hour)
        return _result(base_date + timedelta(days=carry, hours=hour, minutes=minute), spans)

    # (5) No clock time, but an anchored expression (「下班前」「中午」 "before end of work", "noon"): narrow enough to pin down.
    anchor = _find_token(normalized, _ANCHORS)
    if anchor:
        hour, minute = _ANCHORS[anchor[0]]
        spans.append((anchor[1], anchor[2]))
        return _result(base_date + timedelta(hours=hour, minutes=minute), spans)

    # (6) Anything left cannot be pinned to a precise time; always ask. Vague words are reported first, as their wording differs.
    vague = _find_token(normalized, _VAGUE)
    if vague:
        return WhenParseResult(kind=WHEN_VAGUE, reason=REASON_VAGUE_MARKER, matched=raw[vague[1]:vague[2]])
    if spans:
        start, end = min(s for s, _ in spans), max(e for _, e in spans)
        return WhenParseResult(kind=WHEN_VAGUE, reason=REASON_DATE_WITHOUT_TIME, matched=raw[start:end])
    if immediate:
        return _result(now_local, [(immediate[1], immediate[2])], immediate=True, reason=REASON_IMMEDIATE)
    return WhenParseResult(kind=WHEN_NONE, reason=REASON_NO_TIME_EXPRESSION)


def extract_time_expression(text: str) -> str:
    """The time fragment in the sentence, or an empty string if none.

    (1) (rule Guard) and (2b) (post-validation) only call this one function: it shares code with actual parsing,
    so there is no mismatch of "detection says yes, parsing says no".

    **Vague times also count as "yes"**: the right handling of 「晚点发」 ("send later") is to ask for a specific time, not to downgrade to drafting.
    (2b) only downgrades sentences with truly no time word at all.
    """
    return parse_when_text(text).matched


def has_time_expression(text: str) -> bool:
    return bool(extract_time_expression(text))


def has_schedule_marker(text: str) -> bool:
    """Scheduling action words like 「定时」「排期」 ("schedule", "scheduling"). **Not time words**: see the comment on `SCHEDULE_MARKERS`."""
    normalized = str(text or "").translate(_NORMALIZE).lower()
    return any(marker in normalized for marker in SCHEDULE_MARKERS)


def to_utc(local_at: datetime) -> datetime:
    """naive Beijing time -> naive UTC."""
    return local_at - BJ_OFFSET


def to_local(utc_at: datetime) -> datetime:
    """naive UTC -> naive Beijing time."""
    return utc_at + BJ_OFFSET


def bj_day_start_utc(days_ago: int = 0, *, now_utc: datetime | None = None) -> datetime:
    """The naive UTC moment corresponding to "0:00 Beijing time on a given day".

    The daily quota is split by it (design 4.2, the section "daily_cap hangs on the sender mailbox"): `sent_at` stores UTC,
    and the UTC day boundary = 08:00 Beijing time; grouping directly by UTC would cut the user's "today" in the middle,
    producing mismatches like "200 already sent yesterday, yet more can be sent at 9 this morning".

    `api.py`'s `_bj_day_start_utc()` now delegates here. **One implementation, two callers**:
    same reason as at the top of this module: with timezone conversion written in two places, sooner or later only one gets changed.
    """
    bj_now = (now_utc or datetime.utcnow()) + BJ_OFFSET
    bj_start = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return bj_start - timedelta(days=max(0, int(days_ago or 0))) - BJ_OFFSET


def describe_local(local_at: datetime, *, now_utc: datetime | None = None) -> str:
    """Full time string for echo.

    Design 4.7 requires parse results to be echoed to the user for verification: all heuristic rules in this module (bare hours read as afternoon,
    「下班前」 ("before end of work") = 17:30) rely on this step as the fallback, so do not reply with just "OK".
    """
    if not local_at:
        return ""
    now_local = (now_utc or datetime.utcnow()) + BJ_OFFSET
    delta_days = (local_at.date() - now_local.date()).days
    prefix = {0: "今天", 1: "明天", 2: "后天", -1: "昨天"}.get(delta_days)
    if prefix is None:
        prefix = f"{local_at.month} 月 {local_at.day} 日"
    return f"{prefix} {local_at:%H:%M}(北京时间)"


def describe_utc(utc_at: datetime, *, now_utc: datetime | None = None) -> str:
    """naive UTC stored in the DB -> echo string."""
    return describe_local(to_local(utc_at), now_utc=now_utc) if utc_at else ""


def when_followup_text(result: WhenParseResult, *, verb: str = "发") -> str:
    """Follow-up wording when the time is not pinned down. **Four reasons, four sentences** (design 4.7 boundary table).

    Generic copy like "invalid time format" is not allowed here: the user said "send later", and what they need to know is
    "what time should I say", not "I said it wrong".

    It lives in time_parse rather than tools.py because it has two callers: the model tools
    (`schedule_outreach_send` / `reschedule_send`) and the REST endpoints
    (`POST /outreach/schedules`, `PATCH /outreach/schedules/{id}`). With a copy on each side,
    the same "send later" would get a follow-up question in chat and a 422 on the panel.
    """
    if result.is_past:
        shown = to_local(result.utc_at).strftime("%m月%d日 %H:%M") if result.utc_at else result.matched
        return f"「{result.matched}」指的是 {shown},这个时间已经过了。你是想说明天的这个点,还是换一个时间?"
    if result.reason == REASON_VAGUE_MARKER:
        return f"「{result.matched}」还定不到具体时刻。要几点{verb}?说「明天上午十点」这样的就行 —— 我不会替你挑一个时间。"
    if result.reason == REASON_DATE_WITHOUT_TIME:
        return f"「{result.matched}」有日子没有点。当天几点{verb}?"
    if result.reason == REASON_NO_TIME_EXPRESSION:
        return f"要几点{verb}?定时{verb}和立即{verb}是两个不同的授权,我不能默认现在就{verb}。"
    return f"这个时间我没解析出来。说一个具体时刻吧,比如「明天上午十点」「今天 15:00」。"
