#!/usr/bin/env python3
"""Skill package structure checks - basic mistakes that should be caught before shipping.

The host agent picks skills by frontmatter: a wrong name fails to install, a missing description never gets picked,
and overlapping triggers make it pick the wrong one among several skills. These are all "looks fine, breaks in use" problems.

    python3 tests/test_skills_meta.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p for p in ROOT.glob("vigilath-*/SKILL.md"))
FREE = {"vigilath-site-audit", "vigilath-seo-audit", "vigilath-mention"}


def frontmatter(md: Path) -> dict:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    block = text.split("---", 2)[1]
    out, key = {}, None
    for line in block.splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2).strip()
        elif key and line.strip():
            out[key] += " " + line.strip()
    return out


class StructureTest(unittest.TestCase):
    def test_at_least_eight_skills(self):
        self.assertGreaterEqual(len(SKILLS), 8, f"只找到 {len(SKILLS)} 个技能")

    def test_每个技能都有可用的_frontmatter(self):
        for md in SKILLS:
            with self.subTest(skill=md.parent.name):
                fm = frontmatter(md)
                self.assertIn("name", fm)
                self.assertIn("description", fm)
                self.assertEqual(fm["name"], md.parent.name,
                                 "name 必须等于目录名,否则宿主装进去找不到")
                self.assertGreaterEqual(len(fm["description"]), 40,
                                        "description 太短,商店里选不中")
                self.assertLessEqual(len(fm["description"]), 800,
                                     "description 过长,有的宿主会截断")

    def test_描述里要有触发语(self):
        """Without trigger cues like "use when the user asks...", the host cannot tell when to call it."""
        for md in SKILLS:
            with self.subTest(skill=md.parent.name):
                d = frontmatter(md)["description"]
                self.assertTrue(("当用户" in d) or ("使用" in d), "缺触发语描述")

    def test_免登录技能必须写明不需要登录(self):
        for md in SKILLS:
            if md.parent.name not in FREE:
                continue
            with self.subTest(skill=md.parent.name):
                d = frontmatter(md)["description"]
                self.assertTrue(("免登录" in d) or ("不需要登录" in d) or ("不需要注册" in d),
                                "免登录是最大卖点,描述里必须写")

    def test_正文提到的命令都真实存在(self):
        """SKILL.md mentioning a command the client does not have = the agent following it will certainly fail."""
        client = (ROOT / "vigilath-geo" / "scripts" / "geo_client.py").read_text(encoding="utf-8")
        known = set(re.findall(r'cmd == "([a-z-]+)"', client))
        self.assertTrue(known, "没从客户端解析出任何命令")
        for md in SKILLS:
            used = set(re.findall(r"geo_client\.py\s+([a-z-]+)", md.read_text(encoding="utf-8")))
            for c in used - {"--no-seo", "--seo-only"}:
                with self.subTest(skill=md.parent.name, cmd=c):
                    self.assertIn(c, known, f"SKILL.md 写了不存在的命令 {c!r}")

    def test_skillhub_发布字段齐全(self):
        """SkillHub publishing requires slug / version (SemVer) / displayName - missing any one makes publish fail outright.

        Claude and OpenClaw only recognize name + description and ignore extra fields,
        so one SKILL.md can satisfy all three platforms. Do not change name to accommodate any one of them.
        """
        semver = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")
        slug_pat = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        for md in SKILLS:
            with self.subTest(skill=md.parent.name):
                fm = frontmatter(md)
                self.assertEqual(fm.get("slug"), md.parent.name, "slug 必须等于目录名")
                self.assertTrue(slug_pat.match(fm.get("slug", "")), "slug 必须是 kebab-case")
                self.assertTrue(semver.match(fm.get("version", "")), f"version 不是 SemVer: {fm.get('version')}")
                self.assertTrue((fm.get("displayName") or "").strip(), "缺 displayName")
                self.assertTrue((fm.get("summary") or "").strip(), "缺 summary(商店列表页展示这句)")
                self.assertLessEqual(len(fm.get("summary", "")), 120, "summary 太长,列表页会截断")

    def test_全能包覆盖客户端所有命令(self):
        """The all-in-one package is the single entry point (it is what is listed on SkillHub); it must document every command the client has.

        Listing only the Chinese capability names in a table does not count - the agent has to type the commands,
        and without the command names in the docs it cannot run them. mention was missed on 2026-08-20.
        """
        client = (ROOT / "vigilath-geo" / "scripts" / "geo_client.py").read_text(encoding="utf-8")
        umbrella = (ROOT / "vigilath-geo" / "SKILL.md").read_text(encoding="utf-8")
        cmds = set(re.findall(r'cmd == "([a-z-]+)"', client))
        for c in sorted(cmds):
            with self.subTest(cmd=c):
                self.assertIn(c, umbrella, f"全能包文档没提到命令 {c!r}")
        for mode in re.findall(r'^\s+"([a-z-]+)":\s+\("(?:url|entity|urls)"', client, re.M):
            with self.subTest(advanced_mode=mode):
                self.assertIn(mode, umbrella, f"全能包文档没提到高级检测模式 {mode!r}")

    def test_网站九张卡片全部列入(self):
        """The nine cards on the website's "Advanced audits" screen must each be listed in the all-in-one package docs.

        Users ask using the card names they see on the website; if the docs lack the term, the agent cannot follow.
        Names come from i18n home.advanced.cards.*; when changing the copy, change both sides together.
        """
        cards = ["一键全面诊断", "AEO 可见性审计", "竞争对比检测", "AI 爬虫测试",
                 "权威信号审计", "AI 引用检测", "AI 可见性审计", "实体 GEO 审计", "舆情监测"]
        doc = (ROOT / "vigilath-geo" / "SKILL.md").read_text(encoding="utf-8")
        for c in cards:
            with self.subTest(card=c):
                self.assertIn(c, doc, f"网站上有「{c}」这张卡片,全能包文档里没提")

    def test_合集列全八个窄技能(self):
        """The all-in-one package is the "collection" and is what is listed on SkillHub - the names of all eight narrow skills must appear in it.

        A user who installed the collection should see in the docs which capabilities they got;
        if one is missing from the list, that capability effectively does not exist for them.
        """
        umbrella = (ROOT / "vigilath-geo" / "SKILL.md").read_text(encoding="utf-8")
        for md in SKILLS:
            if md.parent.name == "vigilath-geo":
                continue
            name = frontmatter(md).get("displayName", "")
            with self.subTest(skill=md.parent.name, displayName=name):
                self.assertIn(name, umbrella, f"合集里没提到「{name}」({md.parent.name})")

    def test_没有内部信息泄漏(self):
        """These files go into a public repo - no internal addresses, internal service names or credential-like strings."""
        bad = re.compile(r"127\.0\.0\.1|localhost|172\.80|123\.125|/opt/geo|ec2-|"
                         r"geo-backend|geo-telemetry|browser-service|github_pat_")
        for f in list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.sh")):
            if "tests/" in str(f.relative_to(ROOT)):
                continue
            with self.subTest(file=str(f.relative_to(ROOT))):
                hits = bad.findall(f.read_text(encoding="utf-8", errors="ignore"))
                self.assertFalse(hits, f"疑似内部信息:{set(hits)}")

    def test_客户端零三方依赖(self):
        """Works right after install is the foundation of this skill set; importing any third-party package breaks it."""
        client = (ROOT / "vigilath-geo" / "scripts" / "geo_client.py").read_text(encoding="utf-8")
        # The allowlist is a **hand-enumerated subset of the standard library**, not "the whole standard library" -
        # when adding a new stdlib module, add it here too (re was introduced by URL normalization on 2026-08-30).
        stdlib_ok = {"json", "os", "re", "sys", "urllib", "base64", "time", "socket",
                     "uuid", "hashlib", "importlib", "__future__", "typing", "datetime"}
        for mod in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)", client, re.M):
            with self.subTest(module=mod):
                self.assertIn(mod.split(".")[0], stdlib_ok, f"引入了非标准库 {mod}")


class CoverageAgainstBackendTest(unittest.TestCase):
    """Alignment with backend capabilities - only runs when the backend source is visible (skipped automatically in the public repo).

    Two misses on 2026-08-20: first visibility was missed (only six of the website's seven cards were wired up),
    then competitive-intel was missed (the backend had the endpoint marked free, but it was not among the website cards).
    Comparing by eye will always miss again, so the test counts the backend directly.
    """

    def test_高级检测模式与后端一致(self):
        backend = ROOT.parent / "backend" / "geo" / "api" / "advanced_anon.py"
        if not backend.exists():
            self.skipTest("看不到后端源码(公开仓库里正常)")
        server_modes = set(re.findall(r'@router\.post\("/check/advanced-anon/([a-z-]+)"\)',
                                      backend.read_text(encoding="utf-8")))
        client = (ROOT / "vigilath-geo" / "scripts" / "geo_client.py").read_text(encoding="utf-8")
        client_modes = set(re.findall(r'^\s+"([a-z-]+)":\s+\("(?:url|entity|urls)"', client, re.M))
        self.assertEqual(server_modes, client_modes,
                         f"后端有而 skill 没接:{server_modes - client_modes};"
                         f"skill 有而后端没有:{client_modes - server_modes}")


class BackendSanityTest(unittest.TestCase):
    """Backend self-check - only runs when the backend source is visible (skipped automatically in the public repo)."""

    def test_微信服务里调用的自身方法都存在(self):
        """When `self.xxx()` calls a nonexistent method, Python only errors when that line actually runs.

        That is exactly how it blew up on 2026-08-20: create_topup_native_order called self.is_configured(),
        a method that did not exist; unit tests mocked the whole function, and it was only found on the first real call in production.
        """
        src = ROOT.parent / "backend" / "geo" / "services" / "wechat_pay_service.py"
        if not src.exists():
            self.skipTest("看不到后端源码(公开仓库里正常)")
        # Strip comments and docstrings before scanning - otherwise comments like "I originally wrote self.xxx()" cause false positives
        raw = src.read_text(encoding="utf-8")
        text = re.sub(r'"""[\s\S]*?"""', "", raw)
        text = "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())
        defined = set(re.findall(r"^\s{4}def ([a-zA-Z_]+)\(", text, re.M))
        called = set(re.findall(r"self\.([a-zA-Z_]+)\(", text))
        missing = {c for c in called if c not in defined}
        self.assertFalse(missing, f"调用了不存在的方法:{sorted(missing)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CoverageAgainstBackendTest(unittest.TestCase):
    """Alignment with backend capabilities - only runs when the backend source is visible (skipped automatically in the public repo).

    Two misses on 2026-08-20: first visibility was missed (only six of the website's seven cards were wired up),
    then competitive-intel was missed (the backend had the endpoint marked free, but it was not among the website cards).
    Comparing by eye will always miss again, so the test counts the backend directly.
    """

    def test_高级检测模式与后端一致(self):
        backend = ROOT.parent / "backend" / "geo" / "api" / "advanced_anon.py"
        if not backend.exists():
            self.skipTest("看不到后端源码(公开仓库里正常)")
        server_modes = set(re.findall(r'@router\.post\("/check/advanced-anon/([a-z-]+)"\)',
                                      backend.read_text(encoding="utf-8")))
        client = (ROOT / "vigilath-geo" / "scripts" / "geo_client.py").read_text(encoding="utf-8")
        client_modes = set(re.findall(r'^\s+"([a-z-]+)":\s+\("(?:url|entity|urls)"', client, re.M))
        self.assertEqual(server_modes, client_modes,
                         f"后端有而 skill 没接:{server_modes - client_modes};"
                         f"skill 有而后端没有:{client_modes - server_modes}")
