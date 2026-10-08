"""Sales Agent end-to-end -- hits a real running geo-agent, not a mock.

Complements the test_*.py under backend/tests/ (pure functions, no network, run in CI): this goes through the full HTTP chain,
verifying that deterministic flow orchestration, recommendation output, email draft compliance and session readback really connect.

**Only free paths**:
  Covers  profile completion -> four target-filter cards -> recommend 100 companies -> email draft without report -> session readback -> old-account confirm card
  Avoids  generating diagnosis reports (real audits, 8-20 minutes and uses quota), confirm send (really sends mail), emails with reports

Each run uses a separate account, deleted automatically at the end (company master data is a shared seed pool and is kept).

Usage (on the deployment machine, with the agent venv):
    /opt/geo/agent-venv/bin/python backend/tests/e2e/sales_agent.py
    ... sales_agent.py --keep                    keep the state for troubleshooting
    ... sales_agent.py --base http://127.0.0.1:8010/api/sales-agent
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]      # backend/
sys.path.insert(0, str(BACKEND))
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
except ImportError:
    pass

import geo  # noqa: F401,E402  initialize the geo package first, avoiding the existing circular import with sales_agent.models
import requests  # noqa: E402
import jwt  # noqa: E402
from geo.database import SessionLocal, settings  # noqa: E402
from geo.models.user import UserORM  # noqa: E402


def _arg(name: str, default: str) -> str:
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


BASE = _arg("--base", os.environ.get("SALES_AGENT_E2E_BASE", "http://127.0.0.1:8010/api/sales-agent"))
KEEP = "--keep" in sys.argv
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name, ("  ← " + str(detail)) if detail and not cond else ""))
    return bool(cond)


db = SessionLocal()
stamp = datetime.utcnow().strftime("%m%d%H%M%S")
email = "e2e-sales-%s@vigilath.test" % stamp
user = UserORM(email=email, password_hash="!e2e-no-login!")
db.add(user); db.commit(); db.refresh(user)
print("专用账号 %s (id=%s)\n" % (email, user.id))

token = jwt.encode({"sub": email, "exp": datetime.utcnow() + timedelta(minutes=30)},
                   settings.SECRET_KEY, algorithm="HS256")
H = {"Authorization": "Bearer %s" % token, "Content-Type": "application/json"}
SID = {"v": None}


def say(msg, new_session=False):
    body = {"message": msg, "page_context": {"page": "/sales-agent"}}
    if new_session:
        body["new_session"] = True
    elif SID["v"]:
        body["session_id"] = SID["v"]
    t0 = time.time()
    r = requests.post(BASE + "/chat/a2ui", headers=H, json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    SID["v"] = ((d.get("session") or {}).get("id")) or SID["v"]
    last = (d.get("messages") or [{}])[-1]
    cards = ((last.get("metadata") or {}).get("tool_cards")) or []
    print("› %-26s %4.1fs  %s" % (repr(msg), time.time() - t0, str(last.get("content"))[:42]))
    return {"data": (cards[0].get("data") if cards else {}) or {},
            "tool": cards[0].get("tool") if cards else None,
            "text": last.get("content") or ""}


def question(res):
    return (res["data"].get("question_card") or {}) if isinstance(res["data"], dict) else {}


def is_profile_card(res):
    return res["tool"] == "sales_agent_seller_profile_summary" or res["data"].get("title") == "我方画像"


PREFER = {
    "seller_capabilities": ("AI-GEO", "GEO", "SEO"),
    "seller_company_size": ("11-100",),
    "seller_typical_customers": ("有官网",),
    "campaign_target_company_size": ("11-100",),
    "campaign_target_industries": ("工业制造",),
    "campaign_target_regions": ("长三角",),
}


def answer_for(q):
    opts = [str(o.get("value") or o.get("label")) for o in (q.get("options") or [])]
    for p in PREFER.get(str(q.get("id") or ""), ()):
        for o in opts:
            if p in o:
                return o
    return opts[0] if opts else ""


def drive(res, max_steps=10):
    """Answer all the way until recommendations come out; returns (last response, card ids passed, leads)."""
    seen = []
    for _ in range(max_steps):
        if is_profile_card(res):
            seen.append("我方画像确认")
            res = say("继续")
        else:
            q = question(res)
            if not q:
                break
            seen.append(str(q.get("id") or "?"))
            ans = answer_for(q)
            if not ans:
                break
            if str(q.get("id")) == "campaign_target_regions":
                ans += "、100家"
            res = say(ans)
        leads = res["data"].get("leads") or []
        if leads:
            return res, seen, leads
    return res, seen, []


print("=== 1. 新账号:先补画像 ===")
r = say("找客户", new_session=True)
q = question(r)
check("走进找客户流程", r["tool"] is not None, r["tool"])
check("新账号先问画像(不是直接推荐)", bool(q) and not r["data"].get("leads"), list(r["data"].keys())[:6])
check("首问是画像项", str(q.get("id") or "").startswith("seller"), q.get("id"))

print("\n=== 2. 跟着卡片作答到出推荐 ===")
r, seen, leads = drive(r)
print("    经过:", " → ".join(seen))
for cid, label in (("campaign_target_company_size", "目标规模"), ("campaign_target_industries", "目标行业"),
                   ("campaign_target_regions", "目标地区")):
    check("%s卡出现" % label, cid in seen, seen)

print("\n=== 3. 推荐结果 ===")
d = r["data"]
check("返回推荐企业", bool(leads), list(d.keys())[:8])
check("推荐 100 家", len(leads) == 100, "%d 家" % len(leads))
check("requested_limit=100", d.get("requested_limit") == 100, d.get("requested_limit"))
check("actual_count 一致", d.get("actual_count") == len(leads), "%s vs %s" % (d.get("actual_count"), len(leads)))
check("status=ok 无缺口", d.get("status") == "ok", "status=%s shortfall=%s" % (d.get("status"), d.get("shortfall")))
check("来源为写死种子池", (d.get("discovery") or {}).get("discovery_mode") == "seed",
      (d.get("discovery") or {}).get("discovery_mode"))
check("企业带官网", len([x for x in leads if (x.get("company") or {}).get("website")]) >= 95)
lead_ids = [x.get("id") for x in leads if x.get("id")]
check("每条带 lead_id", len(lead_ids) == len(leads))
check("带推荐理由", all(str(x.get("reason") or "").strip() for x in leads[:20]))
check("带证据链", any(x.get("evidence") for x in leads[:20]))
check("选了行业不至于只剩几家", len(leads) > 10, len(leads))
if leads:
    print("    首条: %s | %s" % ((leads[0].get("company") or {}).get("name"), str(leads[0].get("reason"))[:38]))

print("\n=== 4. 无报告写邮件 ===")
lid = lead_ids[0]
t0 = time.time()
resp = requests.post(BASE + "/outreach/drafts/auto", headers=H, timeout=300,
                     json={"lead_id": lid, "channel": "email"})
print("› 草稿 lead_id=%s  %.1fs  HTTP %s" % (lid, time.time() - t0, resp.status_code))
check("接口 200", resp.status_code == 200, resp.text[:100])
dj = resp.json() if resp.status_code == 200 else {}
check("未被 needs_report 阻塞", not dj.get("blocked"), json.dumps(dj, ensure_ascii=False)[:90])
draft = dj.get("draft") or {}
content, subject = str(draft.get("content") or ""), str(draft.get("subject") or "")
check("用无报告默认模板", (draft.get("metadata") or {}).get("kind") == "no_report_default_email",
      (draft.get("metadata") or {}).get("kind"))
check("主题为默认介绍模板", subject == "Vigilath - GEO&AEO，让您的品牌被 AI 优先推荐", subject[:40])
check("不带报告链接", "/sales-agent/report/" not in content and not dj.get("public_report_url"))
banned = [w for w in ("报告显示", "检测报告显示", "已完成诊断", "完整诊断报告", "llms.txt 缺失", "结构化数据不足") if w in content]
check("无诊断结论", not banned, banned)
check("requires_approval=True", draft.get("requires_approval") is True, draft.get("requires_approval"))
check("状态为 draft", str(draft.get("send_status")) == "draft", draft.get("send_status"))
check("HTML 无危险标签", not any(t in str(draft.get("html_content") or "").lower()
                                for t in ("<script", "<iframe", "onclick=", "javascript:")))

print("\n=== 5. 会话回读 ===")
b = requests.get(BASE + "/sessions/" + str(SID["v"]), headers=H, timeout=60)
check("会话接口 200", b.status_code == 200, "%s %s" % (b.status_code, b.text[:80]))
msgs = (b.json().get("messages") or []) if b.status_code == 200 else []
check("消息完整回读", len(msgs) >= 8, "%d 条" % len(msgs))
hist = [m for m in msgs if any((c.get("data") or {}).get("leads")
                               for c in ((m.get("metadata") or {}).get("tool_cards") or []))]
check("历史保留推荐结果", bool(hist), "%d 条" % len(hist))

print("\n=== 6. 已有画像:新会话应直接出确认卡 ===")
r2 = say("找客户", new_session=True)
check("直接出我方画像确认卡", is_profile_card(r2), "tool=%s" % r2["tool"])
summary = str(r2["data"].get("summary_text") or r2["data"].get("message") or "")
check("文案无「我方画像已组装」", "我方画像已组装" not in summary)
check("不展开编号明细", r2["data"].get("hide_items") is True and not r2["data"].get("items"))
check("画像回显了已保存能力", bool(r2["data"].get("service_capabilities")), r2["data"].get("service_capabilities"))
print("    画像:", summary[:66])
r3 = say("继续")
check("点继续进入下一步(不重复问画像)", not is_profile_card(r3), r3["tool"])

print("\n" + "=" * 52)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)

if not KEEP:
    from agents.sales_agent import models as M
    db.expire_all()
    uid = user.id
    sids = [s.id for s in db.query(M.SalesAgentChatSessionORM).filter_by(user_id=uid).all()]
    lids = [x.id for x in db.query(M.SalesAgentLeadRecommendationORM).filter_by(user_id=uid).all()]
    n = 0
    if lids:
        n += db.query(M.SalesAgentOutreachLogORM).filter(M.SalesAgentOutreachLogORM.lead_id.in_(lids)).delete(synchronize_session=False)
        n += db.query(M.SalesAgentLeadActivityORM).filter(M.SalesAgentLeadActivityORM.lead_id.in_(lids)).delete(synchronize_session=False)
    if sids:
        n += db.query(M.SalesAgentChatMessageORM).filter(M.SalesAgentChatMessageORM.session_id.in_(sids)).delete(synchronize_session=False)
    for Model in (M.SalesAgentLeadEvidenceORM, M.SalesAgentLeadRecommendationORM, M.SalesAgentRecommendationCampaignORM,
                  M.SalesAgentBuyerPersonaORM, M.SalesAgentAccountICPORM, M.SalesAgentSellerProfileORM,
                  M.SalesAgentCustomerProfileORM, M.SalesAgentComplianceLogORM, M.SalesAgentChatSessionORM):
        n += db.query(Model).filter_by(user_id=uid).delete(synchronize_session=False)
    db.query(UserORM).filter_by(id=uid).delete(synchronize_session=False)
    db.commit()
    print("已清理 %s 行 + 账号 %s(企业主数据是共享种子池,保留)" % (n, email))

sys.exit(1 if FAIL else 0)
