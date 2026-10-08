"""Automatic platform rule adaptation -- new platforms enter the rule library automatically, rejection reasons are distilled into rule increments automatically.

## Why automate

The rule library (`platform_rules`) used to have only two manual paths: admin hand-editing, or manually clicking "learn from rejections".
Two holes:

1. **New platforms unclaimed**: when a publish plan picks a platform not in the rule library (especially after one-article-to-many
   extra_platforms shipped, a row can carry several platforms), `_platform_rules_block` finds no row at generation
   -> **empty rules injected**, meaning the article has no platform guardrails at all, and nobody knows.
   Old code only created a row when "the platform already had backfilled rejection reasons" -- adding guardrails **after being rejected**,
   the wrong order.
2. **Rejection reasons pile up undistilled**: learn could only be clicked manually, and rejection reasons backfilled by operators sat in the DB producing no rules.

## What this does

`sync_tick()` does two things per run:

  (1) Discover platforms in use -> auto-create rows missing from the rule library + LLM-generated first drafts
     **The first version of a new platform takes effect directly** (rules_text): going from "empty rules" to "AI first draft" carries no overwrite risk,
     and waiting for manual adoption only leaves it unguarded in the meantime. `updated_by_id IS NULL` means "not reviewed by a human";
     the frontend labels it accordingly, and admins can edit any time.
  (2) Existing platforms -> aggregate the platform's rejection reasons into an increment -> write `pending_rules_text` for manual adoption
     **Never automatically change guardrails already in effect**: existing rules were human-reviewed, and auto-inserting could introduce wrong constraints.

Sources of "platforms in use" (far broader than just rejections):
  - publish plan rows' platform + extra_platforms (once scheduled, a plan should have guardrails)
  - placement tasks publish_tasks.platform
  - generated drafts docs.platform

Channel names are not platforms (Ruanwenjiang is a paid placement channel, mediumsly is the own-site publisher), so no rule rows for them --
writing rules always follow real platforms; this metric definition matches publish_preset / the publish plan editor.

How it runs:
  - with an APScheduler leader: content_scheduler.attach mounts it once a day
  - prod without a leader: crontab runs `python -m geo.services.platform_rules_auto`
  - manually: POST /api/admin/platform-rules/sync

ENV:
    GEO_PLATFORM_RULES_AUTO       1 on (default) / 0 off
    GEO_PLATFORM_RULES_AUTO_SEED  1 auto LLM first draft for new platforms (default) / 0 only create empty rows for humans to write
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryTopicExecutionPlanORM, PlatformRuleORM, TopicDocPublishTaskORM,
    TopicGeneratedDocORM,
)

log = logging.getLogger(__name__)

# Placement channel names, not platforms -- no writing rule rows for them
CHANNEL_NAMES = {"软文匠", "mediumsly", "aitoearn", "rwj", "own"}
MAX_PLATFORM_LEN = 32

# Placeholder / fallback names: not real platforms, cannot have writing rules, and should not appear in the rule library.
# They come from historical dirty data -- docs.platform / plan row platform are free text, and early batches filled in these words.
PLACEHOLDER_NAMES = {
    "其他", "其它", "未知", "无", "暂无", "待定", "平台", "自媒体",
    "付费稿件", "外网平台", "外网", "n/a", "na", "none", "null", "-", "—",
}

# Alias -> canonical name. One platform has several spellings in the DB (搜狐 / 搜狐号, 今日头条 / 头条号 -- Sohu / Sohu account, Toutiao / Toutiao account),
# and without normalizing, multiple rule rows would be created, each learning separately, diluting the guardrails.
# Only merge spellings that really are the same platform: Zhihu account / Zhihu organization account are account types, merged into Zhihu;
# but **Zhihu Q&A / Zhihu comments are not merged** -- answers and comments have different writing requirements than column articles,
# so keeping a separate rule set for each makes sense.
PLATFORM_ALIASES = {
    "搜狐": "搜狐号",
    "今日头条": "头条号",
    "头条": "头条号",
    "百度百家号": "百家号",
    "百家": "百家号",
    "微信公众号": "公众号",
    "微信": "公众号",
    "知乎号": "知乎",
    "知乎机构号": "知乎",
    "知乎专栏": "知乎",
    "小红书图文": "小红书",
    "官网": "官网博客",
    "自有站点": "官网博客",
}
# Max new platforms to generate first drafts for per run (LLM calls; do not let one cron round run away)
SEED_LIMIT = int(os.environ.get("GEO_PLATFORM_RULES_SEED_LIMIT", "5"))
# Max platforms to learn rejection increments for per run
LEARN_LIMIT = int(os.environ.get("GEO_PLATFORM_RULES_LEARN_LIMIT", "8"))


def enabled() -> bool:
    return os.environ.get("GEO_PLATFORM_RULES_AUTO", "1").strip().lower() not in {"0", "false", ""}


def _auto_seed_enabled() -> bool:
    return os.environ.get("GEO_PLATFORM_RULES_AUTO_SEED", "1").strip().lower() not in {"0", "false", ""}


def _norm(p: Optional[str]) -> str:
    """Free-text platform name -> canonical platform name; channel names / placeholder names / dirty values return empty string (= no rule row)."""
    v = (p or "").strip()
    if not v or len(v) > MAX_PLATFORM_LEN:
        return ""
    low = v.lower()
    if v in CHANNEL_NAMES or low in CHANNEL_NAMES:
        return ""
    if v in PLACEHOLDER_NAMES or low in PLACEHOLDER_NAMES:
        return ""
    return PLATFORM_ALIASES.get(v, v)


def discover_platforms(db: Session) -> dict[str, set[str]]:
    """Scan platforms "actually in use" -> {platform: {source labels}}. Source labels tell the frontend where it came from."""
    found: dict[str, set[str]] = {}

    def hit(name: Optional[str], src: str) -> None:
        v = _norm(name)
        if v:
            found.setdefault(v, set()).add(src)

    # (1) Publish plan rows: platform primary + extra_platforms additional
    for (raw,) in db.query(AiTelemetryTopicExecutionPlanORM.publishing_plan_json).all():
        try:
            items = json.loads(raw or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            hit(it.get("platform"), "发文计划")
            for extra in (it.get("extra_platforms") or []):
                hit(extra if isinstance(extra, str) else "", "发文计划")

    # (2) Placement tasks
    for (p,) in db.query(TopicDocPublishTaskORM.platform).distinct().all():
        hit(p, "投放任务")

    # (3) Generated drafts
    for (p,) in db.query(TopicGeneratedDocORM.platform).distinct().all():
        hit(p, "已出稿")

    return found


def _seed_prompt(platform: str) -> str:
    return (
        f"你是内容平台合规专家。请整理「{platform}」平台对图文内容的审核红线清单,"
        f"供 AI 写稿时遵守(违反任何一条稿件会被平台拒审)。\n"
        f"涵盖维度:广告法用语(绝对化用语/功效承诺)、外链与联系方式政策、"
        f"诱导关注与导流限制、标题规范、敏感领域(医疗/金融/教育)表述、"
        f"图片与素材要求、字数与排版习惯。\n"
        f"输出要求:每条一行,以「- 」开头,只写可执行的禁止/必须项,"
        f"不超过 20 条,不要解释,不要标题。"
    )


def _learn_prompt(platform: str, existing: str, reasons: list[str]) -> str:
    bullets = "\n".join(f"- {s[:300]}" for s in reasons)
    return (
        f"你是内容平台合规专家。下面是「{platform}」平台真实的拒稿原因记录,"
        f"请提炼成可执行的审核规则条目,供 AI 写稿时遵守。\n\n"
        f"【现有规则(已生效,不要重复输出)】\n{existing or '(暂无)'}\n\n"
        f"【拒稿原因记录】\n{bullets}\n\n"
        f"输出要求:只输出现有规则没覆盖的新规则,每条一行以「- 」开头,"
        f"合并同类原因,不超过 10 条;没有新规则就输出「(无新增)」。"
    )


def _llm(prompt: str) -> str:
    """Plain-text LLM call. Reuses the one in api/platform_rules (deepseek first, fallback OpenRouter)."""
    from geo.api.platform_rules import _llm_text
    return _llm_text(prompt)


def sync_tick(db: Optional[Session] = None) -> dict:
    """One adaptation round: new platforms get rows + first drafts, existing platforms learn rejection increments. Returns stats for logs/API echo."""
    own = db is None
    db = db or SessionLocal()
    stat: dict = {"in_use": 0, "created": [], "seeded": [], "seed_failed": [],
                  "learned": [], "learn_failed": [], "skipped_seed": []}
    try:
        in_use = discover_platforms(db)
        stat["in_use"] = len(in_use)
        existing = {r.platform: r for r in db.query(PlatformRuleORM).all()}

        # (1) Platforms in use but **without rule text** -> create a row if missing, then fill a first draft (first version takes effect directly).
        #
        # The criterion is "are the rules empty", not "does the row exist": the row may have been created long ago by the old learn path,
        # or left empty by a failed first draft last round, or added manually by an admin without content -- these all count as
        # "in use with zero guardrails", exactly what needs filling. Looking only at "row missing" would always miss them.
        def _has_rules(p: str) -> bool:
            row = existing.get(p)
            return bool(row is not None and (row.rules_text or "").strip())

        need_seed = [p for p in in_use if not _has_rules(p)]
        seed_attempts = 0
        for platform in need_seed:
            row = existing.get(platform)
            if row is None:
                row = PlatformRuleORM(platform=platform)
                db.add(row)
                db.commit()
                db.refresh(row)
                existing[platform] = row
                stat["created"].append(platform)
            if not _auto_seed_enabled():
                stat["skipped_seed"].append(platform)
                continue
            # The quota counts **attempts**, not successes -- otherwise when the LLM keeps failing the quota never triggers,
            # and one round could wait out a 120s timeout for each of 20 platforms.
            if seed_attempts >= SEED_LIMIT:
                stat["skipped_seed"].append(platform)   # This round's quota is used up; fill next round
                continue
            seed_attempts += 1
            try:
                draft = _llm(_seed_prompt(platform)).strip()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                stat["seed_failed"].append(f"{platform}: {str(e)[:120]}")
                log.warning("[platform-rules] %s first draft generation failed: %s", platform, e)
                continue
            if draft:
                # First version takes effect directly: going from "empty rules" to "AI first draft" has no overwrite risk;
                # updated_by_id left empty = not reviewed by a human, which the frontend labels.
                row.rules_text = draft
                row.updated_at = datetime.utcnow()
                row.updated_by_id = None
                db.commit()
                stat["seeded"].append(platform)
                log.info("[platform-rules] new platform %s rules created (AI first draft, %d chars, pending human review)",
                         platform, len(draft))

        # (2) Platforms with existing rejection reasons -> distill increments into pending (rules in effect untouched)
        docs = (
            db.query(TopicGeneratedDocORM.platform, TopicGeneratedDocORM.platform_reject_reason)
              .filter(TopicGeneratedDocORM.platform_review_status == "rejected")
              .filter(TopicGeneratedDocORM.platform_reject_reason.isnot(None))
              .all()
        )
        by_platform: dict[str, list[str]] = {}
        for p, reason in docs:
            plat = _norm(p)
            txt = (reason or "").strip()
            if plat and txt:
                by_platform.setdefault(plat, []).append(txt)

        for platform, reasons in list(by_platform.items())[:LEARN_LIMIT]:
            row = existing.get(platform)
            if row is None:
                row = PlatformRuleORM(platform=platform)
                db.add(row)
                db.commit()
                db.refresh(row)
                existing[platform] = row
                stat["created"].append(platform)
            uniq = list(dict.fromkeys(reasons))[-50:]
            try:
                inc = _llm(_learn_prompt(platform, (row.rules_text or "").strip(), uniq)).strip()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                stat["learn_failed"].append(f"{platform}: {str(e)[:120]}")
                log.warning("[platform-rules] %s learning from rejections failed: %s", platform, e)
                continue
            if inc and "(无新增)" not in inc:
                row.pending_rules_text = inc
                stat["learned"].append(platform)
            row.learned_at = datetime.utcnow()
            db.commit()

        log.info("[platform-rules] automatic adaptation finished %s", {k: v for k, v in stat.items() if v})
        return stat
    finally:
        if own:
            db.close()


if __name__ == "__main__":  # pragma: no cover -- crontab entry point
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # When crontab runs python -m the environment is clean, so .env must be loaded first (see geo/cron_env.py)
    from geo.cron_env import load_env_for_cron
    has_key = load_env_for_cron(required=("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"))
    if not enabled():
        print("GEO_PLATFORM_RULES_AUTO=0,跳过")
    elif not has_key:
        # Creating rows needs no key, but first drafts and learning increments do; without a key skip the whole round rather than create a pile of empty placeholder rows
        print("没有 LLM key,跳过本轮(建了空行也生成不出初稿)")
    else:
        print(sync_tick())
