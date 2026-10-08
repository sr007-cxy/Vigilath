"""Industry words must be **categories**, not a question; links whose body can't be fetched must not be persisted.

Both come from report #99 (<brand-a>, 2026-08-27):
  - The model drafted 「<brand-d>羊肉哪家好」 ("which <brand-d> mutton is best") in its suggestions, the user copied it and started, and the model **itself warned**
    "the characters look like <brand-d> and might make engines think of the dairy company <brand-d>" -- then ran anyway. Warnings can't stop it; tools must.
    Consequence: the narrative layer wrote beef and mutton as goat milk powder ("milk powder" 19 times / "mutton" 3 times),
    and all 12 pieces of sentiment evidence fetched were about <namesake-company>.
  - The official account link the user gave fetched an anti-scraping verification page (65 chars, "environment abnormal... go verify"), **still added to the knowledge base**,
    and later retrieved as material by ask_knowledge.
"""
from __future__ import annotations

import inspect

from agents.geo import tools as tools_mod
from agents.geo.tools import _fd_bad_terms, _fetch_looks_blocked


def _code(fn) -> str:
    """The code in the function body **with comments stripped**.

    Source-level assertions must look only at code: comments quoting the old form ("this used to be `terms = suggestions`")
    are good comments and shouldn't make the test falsely red.
    """
    out = []
    for line in inspect.getsource(fn).splitlines():
        stripped = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        out.append(stripped)
    return "\n".join(out)


# ── Industry word shape ──────────────────────────────────────────────────────────

def test_the_actual_bad_term_is_rejected():
    assert _fd_bad_terms(["<brand-d>羊肉哪家好"]) == ["<brand-d>羊肉哪家好"]


def test_real_category_words_pass():
    ok = ["牛羊肉", "火锅食材", "老楼加装电梯", "商用扫地机器人", "生鲜食品"]
    assert _fd_bad_terms(ok) == []


def test_question_shaped_terms_are_rejected():
    for bad in ("啤酒哪个好", "怎么样选牛羊肉", "十大啤酒品牌排名", "牛羊肉推荐", "值得买吗?"):
        assert _fd_bad_terms([bad]) == [bad], bad


def test_overlong_terms_are_rejected():
    assert _fd_bad_terms(["一二三四五六七八九十十一十二十三"]) != []


def test_blank_terms_are_ignored_not_rejected():
    assert _fd_bad_terms(["", "   ", "牛羊肉"]) == []


def test_suggestions_are_never_used_as_terms_directly():
    """★ It used to be `terms = suggestions` -- accounts with topics were never asked for industry words,
    and _fd_industry_terms's second fallback is **seed words**, which are full questions.
    So the "model must not guess" discipline only applied when "the account has no topic at all".
    """
    head = _code(tools_mod.run_full_diagnosis).split("running = _fd_latest")[0]
    assert "terms = suggestions" not in head, "候选词又被直接拿来开跑了"
    assert "_fd_bad_terms" in head, "形状校验必须排在发起之前"


def test_shape_check_runs_before_any_http_call():
    """The bounce must come before building the token / sending HTTP -- otherwise it's "run first, ask later"."""
    src = _code(tools_mod.run_full_diagnosis)
    i = src.index("_fd_bad_terms")
    for marker in ("full/generate", "_jwt.encode", "AsyncClient"):
        assert i < src.index(marker), f"形状校验出现在 {marker} 之后"


def test_rejected_terms_come_back_as_choices_not_a_hard_no():
    """This is **a warning, not a ban**: the original word is still offered as a candidate; if the user insists, they click again."""
    head = _code(tools_mod.run_full_diagnosis).split("running = _fd_latest")[0]
    assert '"rejected"' in head
    assert "card[\"suggestions\"]" in head


# ── Run-hijack check by brand ───────────────────────────────────────────────────────

def test_running_check_is_brand_scoped():
    """Without checking the brand, another brand's generating run would swallow this request entirely,
    returning that brand's progress -- to the user it feels like "it didn't run".
    """
    src = _code(tools_mod.run_full_diagnosis)
    assert "_fd_latest(ctx, brand=brand)" in src
    assert "brand" in inspect.signature(tools_mod._fd_latest).parameters


# ── Fetch failures aren't persisted ───────────────────────────────────────────────────────

def test_the_actual_wechat_block_page_is_caught():
    body = "环境异常 当前环境异常,完成验证后即可继续访问。 去验证 : , 。 视频 小程序 赞 ,轻点两下取消赞 在看 ,轻点两下取消在看"
    assert _fetch_looks_blocked(body)


def test_common_block_pages_are_caught():
    for body in ("Just a moment...", "Access Denied", "请输入验证码", "访问过于频繁,请稍后再试"):
        assert _fetch_looks_blocked(body), body


def test_too_short_is_treated_as_blocked():
    assert _fetch_looks_blocked("短" * 50)


def test_real_article_passes():
    body = ("蒙羊的故事始于2004年,总经理赵学志的父亲赵旭峰老先生在北京创办了诚诚加工厂,"
            "专注牛羊肉,名字里就透着真诚。" * 10)
    assert _fetch_looks_blocked(body) == ""


def test_blocked_page_returns_a_result_not_a_modelretry():
    """ModelRetry makes the model refetch identically twice (retries=2), with identical results, burning tokens for nothing."""
    head = _code(tools_mod.ingest_material).split("m = AgentMaterialORM")[0]
    assert '"status": "fetch_blocked"' in head
    assert "_fetch_looks_blocked" in head


def test_blocked_page_never_reaches_the_db():
    src = _code(tools_mod.ingest_material)
    assert src.index("_fetch_looks_blocked") < src.index("AgentMaterialORM("), \
        "反爬判定必须排在入库之前"
