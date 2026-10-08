"""Seed publish_platforms -- platform capability matrix for the self-hosted account pool.

Idempotent: only inserts rows whose `code` isn't in the table; existing rows are untouched (ops may have changed limits in the UI).

Usage (after alembic upgrade head):
    cd backend && .venv/bin/python -m seeds.publish_platforms

This only maintains the platform catalog for the self-hosted publishing account pool (`server_pool`). A platform being in the catalog doesn't mean its publish
adapter is finished; the account center decides whether login and publishing are possible based on browser-service's actual capabilities.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# (code, name_zh, channel, supports_article, title_limit, body_limit,
#  image_min, image_max, needs_cover, content_format, identity_cookies_json, sort_order)
#
# Value 0 = no limit (follows the engine_sessions.daily_cap convention).
#
# ⚠️ TODO(calibration): title_limit / body_limit / image_max for server_pool platforms are currently **estimates** based on each platform's
# editor. Calibrate one by one against real pages on the first dry_run -- after calibrating change them here, then run an UPDATE
# for existing rows (rerunning the seed won't overwrite existing rows, deliberately).
SEED: list[tuple[str, str, str, bool, int, int, int, int, bool, str, str, int]] = [
    # ── server_pool: not covered by Aitoearn + no official publish API, so we have to drive a browser ourselves ──
    # The only platform enabled in M1
    # content_format=markdown (changed after measurement on 2026-08-13; it used to say html): Zhihu's editor
    # is Draft.js and only takes keyboard events; typing character by character turns `<p>` into visible text; and it has a built-in
    # "Markdown 语法输入中" ("Markdown syntax input on") mode where markup is interpreted into formatting on the spot. So markdown is its native
    # form; html goes through the adapter's tag-stripping fallback = losing formatting for nothing.
    # ⚠️ Rerunning the seed **won't** change existing rows; for an existing DB do it manually:
    #     UPDATE publish_platforms SET content_format='markdown' WHERE code='zhihu';
    ("zhihu",    "知乎",   "server_pool", True, 100, 0, 0, 20, False, "markdown", '["z_c0"]', 10),
    # identity_cookies_json must stay consistent with the identity cookie lists used by the publish
    # adapters; change them together.
    #
    # Before 2026-08-27 these 9 rows were all "[]" -- with an empty array has_login_cookies degrades to
    # "allow if there's any cookie", meaning the gate wasn't on. Now filled in per the contract.
    #
    # ⚠️ Rerunning the seed **won't** change existing rows (insert-if-missing). For existing DBs do it manually:
    #     UPDATE publish_platforms SET identity_cookies_json='["sessionid","sid_tt","uid_tt","passport_auth_status"]' WHERE code='toutiao';
    #     UPDATE publish_platforms SET identity_cookies_json='["BDUSS","STOKEN","BAIDUID"]' WHERE code='baijia';
    #     UPDATE publish_platforms SET identity_cookies_json='["SUV","gidinf","ppinf","pprdig"]' WHERE code='sohu';
    #     UPDATE publish_platforms SET identity_cookies_json='["UserToken","UserName","uuid_tt_dd"]' WHERE code='csdn';
    #     UPDATE publish_platforms SET identity_cookies_json='["sessionid","sid_tt","uid_tt"]' WHERE code='douyin';
    #     UPDATE publish_platforms SET identity_cookies_json='["web_session"]' WHERE code='xhs';
    #     UPDATE publish_platforms SET identity_cookies_json='["slave_user","bizuin","slave_sid"]' WHERE code='wxgzh';
    #     UPDATE publish_platforms SET identity_cookies_json='["sess","smzdm_id","user","user_info"]' WHERE code='smzdm';
    #     UPDATE publish_platforms SET identity_cookies_json='[".Cnblogs.AspNetCore.Cookies","UserToken"]' WHERE code='cnblogs';
    # 🔴 This tightens login session validation from "any cookie will do". Run it on the test DB first and confirm existing accounts
    # are still schedulable (scripts/publish_state_dump.py) before touching production.
    ("toutiao",  "头条号", "server_pool", True, 30,  0, 0, 20, False, "html",  '["sessionid", "sid_tt", "uid_tt", "passport_auth_status"]', 20),
    ("baijia",   "百家号", "server_pool", True, 30,  0, 0, 20, False, "html",  '["BDUSS", "STOKEN", "BAIDUID"]', 30),
    # Sohu account: the business side confirmed on 2026-08-11 "no impact, can be restored"; put back into the server_pool list
    ("sohu",     "搜狐号", "server_pool", True, 60,  0, 0, 20, False, "html",  '["SUV", "gidinf", "ppinf", "pprdig"]', 40),
    ("csdn",     "CSDN",   "server_pool", True, 100, 0, 0, 20, False, "html",  '["UserToken", "UserName", "uuid_tt_dd"]', 50),
    ("douyin",   "抖音",       "server_pool", False, 55, 0,     1, 35, True,  "plain", '["sessionid", "sid_tt", "uid_tt"]', 60),
    ("xhs",      "小红书",     "server_pool", True,  20, 1000,  1, 18, True,  "plain", '["web_session"]', 70),
    ("wxgzh",    "公众号",     "server_pool", True,  64, 20000, 1, 9,  True,  "html",  '["slave_user", "bizuin", "slave_sid"]', 80),
    ("smzdm",    "什么值得买", "server_pool", True,  100, 0,    0, 20, False, "html",  '["sess", "smzdm_id", "user", "user_info"]', 90),
    ("cnblogs",  "博客园",     "server_pool", True,  200, 0,    0, 20, False, "html",  '[".Cnblogs.AspNetCore.Cookies", "UserToken"]', 100),
    ("ctrip",    "携程",       "server_pool", True,  30,  0,    1, 20, True,  "plain", '["cticket", "weticket"]', 110),
]


def main() -> None:
    from geo.database import SessionLocal
    from geo.models.publish_accounts import PublishPlatformORM
    from sqlalchemy import select

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        existing_codes = set(db.execute(select(PublishPlatformORM.code)).scalars().all())
        to_insert = [row for row in SEED if row[0] not in existing_codes]
        if not to_insert:
            print(f"[seed:publish_platforms] all {len(SEED)} codes already present "
                  f"— nothing to do")
            return

        for (code, name_zh, channel, supports_article, title_limit, body_limit,
             image_min, image_max, needs_cover, content_format,
             identity_cookies_json, sort_order) in to_insert:
            db.add(PublishPlatformORM(
                code=code,
                name_zh=name_zh,
                channel=channel,
                supports_article=supports_article,
                title_limit=title_limit,
                body_limit=body_limit,
                image_min=image_min,
                image_max=image_max,
                needs_cover=needs_cover,
                content_format=content_format,
                identity_cookies_json=identity_cookies_json,
                enabled=True,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        print(f"[seed:publish_platforms] inserted {len(to_insert)} rows "
              f"({len(existing_codes)} pre-existing left untouched)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
