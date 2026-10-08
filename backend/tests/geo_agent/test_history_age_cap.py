"""History fed to the model is capped by **time**.

agent_conversations has only one row per account and never expires. The 2026-08-27 session held
52 messages / 71KB from 6/4 -> 8/27, spanning three months, four brands and two reports, while _feed_cap was 50 messages --
so the 8/25 "<brand-c> stuck at 5/8" context sat untouched on top of the 8/27 <brand-a> turn.
Message counts can't control this: someone saying only ten things a month still keeps the whole month in the window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.geo.methods import drop_stale

NOW = datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc)


class _P:
    def __init__(self, ts, kind="text", tool_call_id=None):
        self.timestamp, self.part_kind, self.tool_call_id = ts, kind, tool_call_id


class _M:
    def __init__(self, ts, kind="request", parts=None):
        self.timestamp, self.kind = ts, kind
        self.parts = parts if parts is not None else [_P(ts)]


def _at(days_ago=0, hours_ago=0):
    return NOW - timedelta(days=days_ago, hours=hours_ago)


def test_drops_the_stale_head_keeps_the_recent_tail():
    msgs = [_M(_at(days_ago=84)), _M(_at(days_ago=2)), _M(_at(hours_ago=2)), _M(_at(hours_ago=1))]
    got = drop_stale(msgs, 24, now=NOW)
    assert got == msgs[2:]


def test_the_actual_incident_shape():
    """The 8/25 <brand-c> turns shouldn't appear in the window of the 8/27 <brand-a> turn."""
    brand_c = [_M(datetime(2026, 8, 25, 3, 13, tzinfo=timezone.utc)),
               _M(datetime(2026, 8, 25, 3, 23, tzinfo=timezone.utc))]
    brand_a = [_M(datetime(2026, 8, 27, 2, 27, tzinfo=timezone.utc))]
    assert drop_stale(brand_c + brand_a, 24, now=NOW) == brand_a


def test_result_is_always_a_contiguous_suffix():
    """Never cut holes in the middle -- holes split tool_call <-> tool_return,
    and both directions lead to hard-to-debug failures (see the comment on align_to_pair_boundary).
    """
    msgs = [_M(_at(days_ago=30)), _M(_at(hours_ago=1)), _M(_at(days_ago=30)), _M(_at(hours_ago=1))]
    got = drop_stale(msgs, 24, now=NOW)
    assert got == msgs[3:], "遇到旧消息就该停,不能跳过它继续留后面的"


def test_naive_timestamps_are_treated_as_utc():
    """The DB has both: with tzinfo (newer pydantic-ai) and naive UTC (existing data)."""
    msgs = [_M(datetime(2026, 6, 4, 16, 34)), _M(datetime(2026, 8, 27, 2, 27))]
    assert drop_stale(msgs, 24, now=NOW) == msgs[1:]


def test_message_without_timestamp_rides_with_its_neighbours():
    """Messages without a timestamp stay with the adjacent newer message; they aren't judged dead on their own."""
    msgs = [_M(_at(days_ago=30)), _M(None, parts=[_P(None)]), _M(_at(hours_ago=1))]
    assert drop_stale(msgs, 24, now=NOW) == msgs[1:]


def test_zero_disables_the_cut():
    msgs = [_M(_at(days_ago=84)), _M(_at(hours_ago=1))]
    assert drop_stale(msgs, 0, now=NOW) == msgs


def test_empty_and_garbage_pass_through():
    assert drop_stale([], 24, now=NOW) == []
    assert drop_stale(None, 24, now=NOW) is None
    assert drop_stale("nonsense", 24, now=NOW) == "nonsense"


def test_everything_recent_is_kept_whole():
    msgs = [_M(_at(hours_ago=3)), _M(_at(hours_ago=2)), _M(_at(hours_ago=1))]
    assert drop_stale(msgs, 24, now=NOW) == msgs
