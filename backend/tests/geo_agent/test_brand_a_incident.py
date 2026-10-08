"""Turn-by-turn reproduction of the 2026-08-27 "<brand-a>" session -- one gate per each of the four symptoms.

The original session comes from the production DB (agent_conversations.id=2, account 1000023, report #99), times in UTC:

  02:27:56  User: "Run a one-click full diagnosis for <brand-a> for me"
  02:28:24  run_full_diagnosis -> **retry-prompt** "missing industry words"
  02:28:28  The model then made up a guess list on its own:
            "If <brand-a> sells shoes/apparel -> canvas shoes / if catering -> snacks /
              if enterprise services -> SaaS / if local services -> housekeeping"
  02:54:33  User gives an official account link -> fetched an **anti-scraping verification page** (65 chars), **still added to the knowledge base**
  02:56:14  User pastes the body by hand -> the model drafted 「<brand-d>羊肉哪家好」 ("which <brand-d> mutton is best") among the options on its own
  03:12:03  User copies this word and starts -> the model itself warned "it looks like <brand-d>", but ran anyway -> #99
  04:16:58  #99 ready. **45 minutes from request to actually running**

Also the whole session held 52 messages from 6/4 -> 8/27, and the turns where 8/25 <brand-c> #95 was stuck at **5/8**
kept sitting in the window -- the "8/5" the user mentioned was that.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from agents.geo import tools as tools_mod
from agents.geo.agent import route_line
from agents.geo.flow import DiagnosisState, absorb, extract, next_step
from agents.geo.flow.router import READY, RUNNING, blocks_other_lines, preamble
from agents.geo.methods import drop_stale
from agents.geo.tools import _fd_bad_terms, _fetch_looks_blocked

NOW = datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc)


class _M:
    def __init__(self, ts):
        self.timestamp, self.kind, self.parts = ts, "request", []


# ── Symptom (1) "no prompt for the user to choose industry words" ──────────────────────────────────────

def test_missing_terms_yields_a_card_not_a_retry_prompt():
    """That turn actually got a retry-prompt, so the model had to write a paragraph asking on its own."""
    head = inspect.getsource(tools_mod.run_full_diagnosis).split("running = _fd_latest")[0]
    assert '"status": "need_input"' in head
    assert 'raise ModelRetry("缺行业词' not in head


def test_the_card_is_a_form_the_frontend_can_render():
    from agents.geo.cards import CARD_TOOLS
    assert "run_full_diagnosis" in CARD_TOOLS, "不进 CARD_TOOLS 就推不成卡片,又退回文字复述"


# ── Symptom (2) "a pile of junk": the model's self-made guess list + cross-brand cross-talk ───────────────

def test_prompt_still_forbids_the_guessing_list():
    from agents.geo.agent import SYSTEM_PROMPT
    assert "绝不许你自己拟" in SYSTEM_PROMPT
    assert "猜测清单" in SYSTEM_PROMPT


def test_flow_preamble_says_it_out_loud_every_turn():
    """Prompts get diluted by history; 【当前流程】 ("current flow") is the part re-injected every turn."""
    st = DiagnosisState()
    absorb(st, [("run_full_diagnosis", {"status": "need_input", "brand": "<brand-a>"})])
    p = preamble(st)
    assert "<brand-a>" in p and "还没有发起" in p
    assert "不要替他猜" in p and "不要列举各行各业" in p


def test_brand_c_context_is_out_of_the_window_by_the_time_brand_a_asks():
    """The turns where 8/25 <brand-c> was stuck at 5/8 shouldn't appear in the context of this 8/27 turn."""
    brand_c = [_M(datetime(2026, 8, 25, 3, 13, tzinfo=timezone.utc)),
               _M(datetime(2026, 8, 25, 3, 23, tzinfo=timezone.utc))]
    brand_a = [_M(datetime(2026, 8, 27, 2, 27, tzinfo=timezone.utc))]
    assert drop_stale(brand_c + brand_a, 24, now=NOW) == brand_a


def test_another_brands_progress_cannot_become_this_ones():
    st = DiagnosisState()
    absorb(st, [("run_full_diagnosis", {"report_id": 99, "status": "generating",
                                        "brand": "<brand-a>", "industry_terms": ["牛羊肉"]})])
    absorb(st, [("get_full_diagnosis", {"report_id": 95, "status": "generating",
                                        "brand": "<brand-c>"})])
    assert st.report_id == 99


# ── Symptom (3) "didn't run": run hijacking + business line hijacked ──────────────────────────────────

def test_another_brands_running_report_no_longer_swallows_this_request():
    src = inspect.getsource(tools_mod.run_full_diagnosis)
    assert "_fd_latest(ctx, brand=brand)" in src


def test_the_answer_turn_keeps_the_diagnosis_tool_in_scope():
    """When the answer contains sentiment words, route_line routes this turn to the sentiment line -- that turn can't even
    load run_full_diagnosis's schema, so the diagnosis can never be started.
    """
    answer = "行业词:烤羊肉串 清真牛羊肉 口碑"
    assert route_line(answer) == "sentiment", "前提变了:这句话已不再被判到舆情线"

    st = DiagnosisState()
    absorb(st, [("run_full_diagnosis", {"status": "need_input", "brand": "<brand-a>"})])
    assert blocks_other_lines(st) is True, "这一轮必须锁在 geo 线上"


# ── Symptom (4) the two upstreams of "8/5": model-drafted question words + persisting pages whose body couldn't be fetched ───────────

def test_the_exact_term_that_poisoned_report_99_is_rejected():
    assert _fd_bad_terms(["<brand-d>羊肉哪家好"]) == ["<brand-d>羊肉哪家好"]
    assert _fd_bad_terms(["牛羊肉", "火锅食材"]) == []


def test_the_exact_wechat_page_no_longer_enters_the_knowledge_base():
    body = ("环境异常 当前环境异常,完成验证后即可继续访问。 去验证 : , 。 "
            "视频 小程序 赞 ,轻点两下取消赞 在看 ,轻点两下取消在看")
    assert len(body) < 200, "原始那页就是 65 个字符"
    assert _fetch_looks_blocked(body)
    src = inspect.getsource(tools_mod.ingest_material)
    assert src.index("_fetch_looks_blocked") < src.index("AgentMaterialORM(")


# ── Full run: after four turns it should start smoothly ──────────────────────────────────────────

def test_the_whole_sequence_now_reaches_ready():
    st = DiagnosisState()

    # Turn 1: user gives the brand -> tool returns a to-fill card (doesn't start)
    absorb(st, [("run_full_diagnosis", {"status": "need_input", "brand": "<brand-a>",
                                        "field": "industry_terms", "suggestions": []})])
    assert st.brand == "<brand-a>" and blocks_other_lines(st)

    # Turn 2: user fills via the card -> slots complete, this turn still locks the geo line
    lock = blocks_other_lines(st)
    st.merge(extract("行业词:牛羊肉 火锅食材"))
    assert lock is True
    assert next_step(st) == READY

    # Turn 3: started successfully -> slots remember this run
    absorb(st, [("run_full_diagnosis", {"report_id": 100, "status": "generating",
                                        "brand": "<brand-a>", "industry_terms": ["牛羊肉", "火锅食材"]})])
    assert next_step(st) == RUNNING and st.report_id == 100
    assert "#100" in preamble(st) and "不要重复发起" in preamble(st)
