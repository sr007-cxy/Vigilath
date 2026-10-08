"""Diagnosis flow state machine -- pure functions, locking "what to do next" and "when to lock the business line".

Background in agents/geo/flow/__init__.py. Each of these assertions corresponds to a specific failure in the 2026-08-27 <brand-a> incident.
"""
from __future__ import annotations

from agents.geo.flow import DiagnosisState, absorb, next_step
from agents.geo.flow.router import ASKING, BRAND, DONE, INDUSTRY_TERMS, READY, RUNNING, blocks_other_lines


def _card(brand="<brand-a>"):
    return [("run_full_diagnosis", {"status": "need_input", "brand": brand,
                                    "field": "industry_terms", "suggestions": []})]


def test_empty_state_asks_for_brand():
    assert next_step(DiagnosisState()) == BRAND


def test_brand_without_terms_asks_for_terms():
    assert next_step(DiagnosisState(brand="<brand-a>")) == INDUSTRY_TERMS


def test_full_slots_are_ready():
    assert next_step(DiagnosisState(brand="<brand-a>", industry_terms=["牛羊肉"])) == READY


def test_one_message_giving_everything_skips_intermediate_cards():
    """Derive from "what's still missing" rather than "what the previous step was" -- if everything is given at once, don't pop cards one by one."""
    st = DiagnosisState()
    st.merge({"brand": "<brand-a>", "industry_terms": ["牛羊肉", "火锅食材"]})
    assert next_step(st) == READY


def test_running_stops_asking_for_slots():
    """Once started, don't ask again -- that run is already going with the words from that moment."""
    st = DiagnosisState(brand="<brand-a>", report_id=99, stage=RUNNING)
    assert next_step(st) == RUNNING


# ── Business line lock ────────────────────────────────────────────────────────────

def test_fresh_session_is_never_locked_to_geo():
    """★ The criterion can't be "is next_step a slot name".

    With empty slots next_step is also brand; using it as the criterion would lock **every first message** into the GEO line,
    and a user starting fresh with "how's sentiment today" wouldn't get sentiment tools.
    """
    assert next_step(DiagnosisState()) == BRAND
    assert blocks_other_lines(DiagnosisState()) is False


def test_locked_only_while_actually_waiting_for_an_answer():
    st = DiagnosisState()
    absorb(st, _card())
    assert st.stage == ASKING
    assert blocks_other_lines(st) is True


def test_not_locked_once_the_run_started():
    st = DiagnosisState()
    absorb(st, _card())
    absorb(st, [("run_full_diagnosis", {"report_id": 99, "status": "generating",
                                        "brand": "<brand-a>", "industry_terms": ["牛羊肉"]})])
    assert blocks_other_lines(st) is False


def test_answer_carrying_a_sentiment_word_would_be_misrouted_without_the_lock():
    """This reproduces R4: an answer containing 「口碑」 ("reputation") makes route_line route this turn to the sentiment line,
    and that turn can't even load run_full_diagnosis's schema, so the diagnosis can never be started.
    The lock criterion must use the state **before merging slots** (lock_geo in api.py).
    """
    from agents.geo.agent import route_line
    from agents.geo.flow import extract

    answer = "行业词:烤羊肉串 清真牛羊肉 口碑"
    assert route_line(answer) == "sentiment", "前提变了:这句话已经不再被判到舆情线"

    st = DiagnosisState()
    absorb(st, _card())
    lock_before_merge = blocks_other_lines(st)      # api.py takes it at exactly this moment
    st.merge(extract(answer))
    assert lock_before_merge is True
    assert st.industry_terms == ["烤羊肉串", "清真牛羊肉", "口碑"]


# ── Brand switch / cross-talk ────────────────────────────────────────────────────────

def test_switching_brand_clears_the_previous_task():
    """"Run XX for me again" must not run with the previous brand's industry words and order number."""
    st = DiagnosisState()
    absorb(st, [("run_full_diagnosis", {"report_id": 99, "status": "generating",
                                        "brand": "<brand-a>", "industry_terms": ["牛羊肉"]})])
    absorb(st, _card(brand="蒙羊"))
    assert st.brand == "蒙羊"
    assert st.report_id == 0
    assert st.industry_terms == []


def test_other_brands_progress_never_touches_this_task():
    """The "5/8" the user saw on 2026-08-27 was the progress of **<brand-c> #95**.
    Slots only recognize their own run; others' progress can't get in.
    """
    st = DiagnosisState()
    absorb(st, [("run_full_diagnosis", {"report_id": 99, "status": "generating",
                                        "brand": "<brand-a>", "industry_terms": ["牛羊肉"]})])
    absorb(st, [("get_full_diagnosis", {"report_id": 95, "status": "generating",
                                        "brand": "<brand-c>"})])
    assert st.report_id == 99
    assert next_step(st) == RUNNING


def test_ready_report_ends_the_task():
    st = DiagnosisState(brand="<brand-a>", industry_terms=["牛羊肉"], report_id=99, stage=RUNNING)
    absorb(st, [("get_full_diagnosis", {"report_id": 99, "status": "ready"})])
    assert next_step(st) == DONE


def test_roundtrip_through_context_json():
    st = DiagnosisState(brand="<brand-a>", industry_terms=["牛羊肉"], report_id=99, stage=RUNNING)
    back = DiagnosisState.from_ctx(st.to_ctx({}))
    assert (back.brand, back.industry_terms, back.report_id, back.stage) == \
           ("<brand-a>", ["牛羊肉"], 99, RUNNING)


def test_empty_values_never_wipe_stored_slots():
    """Slots not mentioned in a turn shouldn't be cleared (same as to_flow in sales_agent/flow/state.py)."""
    st = DiagnosisState(brand="<brand-a>", industry_terms=["牛羊肉"])
    ctx = st.to_ctx({})
    DiagnosisState().to_ctx(ctx)
    assert ctx["diagnosis"]["brand"] == "<brand-a>"
    assert ctx["diagnosis"]["industry_terms"] == ["牛羊肉"]
