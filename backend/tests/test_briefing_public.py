from geo.services.briefing_public import public_fallback_body, sanitize_briefing_body


def test_historical_stub_is_replaced_with_customer_safe_kpis():
    body = sanitize_briefing_body(
        "暂无周报 LLM 输出 (llm_failed:stub)；配置 LLM_PROVIDER + API key",
        llm_model="stub",
        kpi={
            "hit_rate": 21.6,
            "ttfm_days": 4,
            "new_hits_count": 8,
            "lost_hits_count": 2,
        },
    )

    assert "21.6%" in body
    assert "新增命中 8 项" in body
    assert "流失 2 项" in body
    assert "llm" not in body.lower()
    assert "api" not in body.lower()
    assert "provider" not in body.lower()


def test_real_briefing_body_is_unchanged():
    body = "## 本周亮点\n- 品牌在三个平台的露出率提升。"
    assert sanitize_briefing_body(
        body,
        llm_model="deepseek-chat",
        kpi={"hit_rate": 42},
    ) == body


def test_daily_fallback_uses_daily_public_metrics():
    body = public_fallback_body(
        {"整体露出率": "27.7%", "文章引用率": 8.5},
        period_type="daily",
    )
    assert "整体露出率 27.7%" in body
    assert "文章引用率 8.5%" in body
    assert "智能解读暂未生成" in body
