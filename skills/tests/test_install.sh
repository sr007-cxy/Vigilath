#!/usr/bin/env bash
# Behavior tests for install.sh - no network (uses the local repo as source), never touches the real HOME.
#
# Tests three things that have broken or could break: where it installs, selective install, and **never using the source repo as the install target**.
#
#   bash tests/test_install.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$ROOT/vigilath-geo/install.sh"
PASS=0; FAIL=0
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }

command -v bash >/dev/null || { echo "需要 bash"; exit 1; }
bash -n "$INSTALL" && ok "install.sh 语法正确" || bad "install.sh 语法错误"

run_install() {  # run_install <fake_home> <extra args...>
  local home="$1"; shift
  HOME="$home" bash "$INSTALL" --token dummy-token --base https://example.invalid "$@" 2>&1
}

# 1) Install into the detected directory: if .openclaw/skills exists, install there
H=$(mktemp -d); mkdir -p "$H/.openclaw/skills"
out=$(cd "$H" && run_install "$H")
if [ -f "$H/.openclaw/skills/vigilath-site-audit/SKILL.md" ] &&
   [ -f "$H/.openclaw/skills/vigilath-site-audit/scripts/geo_client.py" ]; then
  ok "装进 ~/.openclaw/skills,且每个技能自带客户端副本"
else
  bad "没装进 ~/.openclaw/skills"; echo "$out" | tail -3
fi

# 2) By default install the eight narrow skills, **without** the fallback all-in-one package (installing both confuses the host's selection)
n=$(ls "$H/.openclaw/skills" | grep -c '^vigilath-')
if [ "$n" -ge 8 ] && [ ! -d "$H/.openclaw/skills/vigilath-geo" ]; then
  ok "默认装 $n 个窄技能,不含全能包"
else
  bad "默认安装集不对:$n 个,vigilath-geo 存在=$([ -d "$H/.openclaw/skills/vigilath-geo" ] && echo yes || echo no)"
fi

# 3) --skills selective install
H2=$(mktemp -d); mkdir -p "$H2/.claude/skills"
run_install "$H2" --skills site-audit,seo-audit >/dev/null
got=$(ls "$H2/.claude/skills" | tr '\n' ' ')
if [ "$(ls "$H2/.claude/skills" | wc -l)" = "2" ] && [ -d "$H2/.claude/skills/vigilath-site-audit" ]; then
  ok "--skills 只装指定的两个($got)"
else
  bad "--skills 选装不对:$got"
fi

# 4) A misspelled skill name is skipped without aborting the whole install
H3=$(mktemp -d); mkdir -p "$H3/.claude/skills"
out=$(run_install "$H3" --skills site-audit,nosuchskill)
if echo "$out" | grep -q "跳过" && [ -d "$H3/.claude/skills/vigilath-site-audit" ]; then
  ok "错误技能名被跳过,其余照装"
else
  bad "错误技能名处理不对"
fi

# 5) **When run at the skill source repo root, install output must not be written back into the source directory** (actually hit on 2026-08-19)
H4=$(mktemp -d); mkdir -p "$H4/.openclaw/skills"
before=$(find "$ROOT" -path '*/scripts/geo_client.py' | wc -l)
(cd "$ROOT/.." && HOME="$H4" bash "$INSTALL" --token t --base https://example.invalid >/dev/null 2>&1)
after=$(find "$ROOT" -path '*/scripts/geo_client.py' | wc -l)
if [ "$before" = "$after" ]; then
  ok "在源码仓库里跑不会污染源码目录(客户端副本仍只有 $after 份)"
else
  bad "污染了源码仓库:客户端副本从 $before 变成 $after"
fi

# 6) Credential file permissions must be 600
if [ "$(stat -c '%a' "$H/.vigilath/config" 2>/dev/null)" = "600" ]; then
  ok "~/.vigilath/config 权限 600"
else
  bad "凭证文件权限不是 600"
fi

# 7) ★ Install must succeed without a token - this is the most important case in this file.
#
# Every case above passes `--token dummy-token`, so the device code section was never exercised:
# CI was always green while the real path (the bare command copied from the website) was broken - the old unconditional
# `[ -n "$TOKEN" ] || device_login` ran **before** copying, so without approving in a browser it timed out after 10 minutes
# and died with not a single skill installed. Yet skills/README.md states in black and white "if you only want the three
# no-login capabilities, authorization can be skipped".
#
# Point --base at an unreachable domain so that even if it really connects, it fails immediately instead of hanging.
H7=$(mktemp -d); mkdir -p "$H7/.openclaw/skills"
out7=$(cd "$H7" && HOME="$H7" timeout 60 bash "$INSTALL" --base https://example.invalid 2>&1)
rc7=$?
if [ "$rc7" -ne 0 ]; then
  bad "不给 token 时退出码是 $rc7(应为 0 —— 技能装上了就算成功)"; echo "$out7" | tail -3
else
  ok "不给 token 也退出 0"
fi
n7=$(ls "$H7/.openclaw/skills" 2>/dev/null | grep -c '^vigilath-')
if [ "$n7" -ge 8 ]; then
  ok "不给 token 时八个窄技能照常装上($n7 个)"
else
  bad "不给 token 时只装上 $n7 个"
fi
if [ -f "$H7/.vigilath/config" ] && grep -q '^VIGILATH_BASE=' "$H7/.vigilath/config"; then
  ok "免登录也写了 ~/.vigilath/config(基址不回落到硬编码的生产域名)"
else
  bad "免登录时没写 ~/.vigilath/config —— 客户端会打到硬编码的 vigilath.cn"
fi
if grep -q '^VIGILATH_AGENT_TOKEN=' "$H7/.vigilath/config" 2>/dev/null; then
  bad "没有 token 却写了 VIGILATH_AGENT_TOKEN= 空行"
else
  ok "没有 token 时不写空的 token 行"
fi

echo
printf '通过 %d,失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
