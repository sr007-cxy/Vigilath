# UI Mockups

ASCII wireframes for the main user flows. Layout: left sidebar nav + main pane.
Tenant switcher in top-left (multi-tenant), user menu in top-right.

---

## 1. Dashboard (landing)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ [Acme Co ▼]   MarkAgent                                    🔔 3   [YW] ▼  │
├──────────┬─────────────────────────────────────────────────────────────────┤
│          │  Dashboard                                                       │
│ 🏠 Home  │                                                                  │
│ ✍  Compose│ ┌──── Needs your attention ─────────────────────────────────┐   │
│ 📨 Inbox │ │ 7 replies awaiting approval            [Review →]          │   │
│  └ 7 new │ │ 2 drafts ready to schedule             [Review →]          │   │
│ 📚 Posts │ │ LinkedIn token expires in 3 days       [Reconnect →]       │   │
│ 📊 Stats │ └───────────────────────────────────────────────────────────┘   │
│ 🎚 Policy│                                                                  │
│ 🔌 Accts │ ┌──── Recent activity ───────┐  ┌──── This week ────────────┐   │
│ ⚙  Settng│ │ 14:22  Reply sent (Reddit) │  │ Posts published    12      │   │
│          │ │ 14:10  Post published (YT) │  │ Replies sent       38      │   │
│          │ │ 13:45  Draft by Content    │  │ Approval rate      92%     │   │
│          │ │        agent for review    │  │ Agent cost         $4.12   │   │
│          │ └────────────────────────────┘  └───────────────────────────┘   │
└──────────┴─────────────────────────────────────────────────────────────────┘
```

---

## 2. Compose — platform picker (the pulldown you asked for)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Compose                                                     [Save] [Post▼]│
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│ Brief (tell the Content agent what to write)                               │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ Launch post for v2 of our SDK. Angle: migration is 1 line. Sources:│    │
│ │ https://blog.acme.co/v2, changelog.md, Reddit thread /r/devops/... │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ Target platforms                                                           │
│ ┌──────────────────────────────────────────────────────────┐              │
│ │ Select platforms...                                   ▼  │              │
│ ├──────────────────────────────────────────────────────────┤              │
│ │ ☑ 🟠 Reddit          @acme_dev        ● connected        │              │
│ │ ☑ 🔵 LinkedIn        Acme Co.         ● connected        │              │
│ │ ☑ 🔴 YouTube         Acme Channel     ● connected        │              │
│ │ ☑ 🟢 dev.to          @acme            ● connected        │              │
│ │ ☐ ⚫ Medium          @acme            ⚠ write-only       │              │
│ │ ─────────────────────────────────────────────────        │              │
│ │ ○ 🟣 Hashnode                         ＋ Connect          │              │
│ │ ○ ⚪ X (Twitter)                      ＋ Connect          │              │
│ └──────────────────────────────────────────────────────────┘              │
│                                                                            │
│ Schedule: ○ Now   ● At  [2026-04-22  09:00  PT]                            │
│                                                                            │
│ [✨ Draft with Content agent]   [Write manually]                           │
└────────────────────────────────────────────────────────────────────────────┘
```

Notes:
- Connected platforms are checkable; unconnected ones show `＋ Connect`.
- Medium is listed but marked `⚠ write-only` (can post, can't monitor/reply).
- Dropdown is multi-select; pills appear in the field once selected.

---

## 3. Content agent — brief → draft review

After clicking `✨ Draft with Content agent`:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Content agent is drafting…                                                 │
├────────────────────────────────────────────────────────────────────────────┤
│  [====================>          ] 62%   (view trace)                      │
│                                                                            │
│  • web_fetch https://blog.acme.co/v2                  ✓                    │
│  • read_source_doc changelog.md                       ✓                    │
│  • search_past_posts "SDK launch"                     ✓  (3 hits)          │
│  • drafting...                                        ◐                    │
└────────────────────────────────────────────────────────────────────────────┘
```

When done:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Draft review                                  [Regenerate]  [Approve →]   │
├────────────────────────────────────────────────────────────────────────────┤
│ Title                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ Acme SDK v2: migrate in one line                                   │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ Body (Markdown)                            Tags: #sdk #launch #devex       │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ After six months of rewrites, v2 is here. The highlights:          │    │
│ │                                                                    │    │
│ │ - One-line migration: `npm i acme@2` — no code changes for 90% of  │    │
│ │   callers (the rest: see Breaking changes).                        │    │
│ │ - 40% smaller bundle...                                            │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ Self-critique (from agent)                                                 │
│   ✓ Voice match: confident, low-jargon — matches voice.md                  │
│   ⚠ Claim "40% smaller" — sourced from changelog.md L42, verify before send│
│   ✓ No policy violations                                                   │
│                                                                            │
│ [◄ Back to brief]                                                          │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Per-platform variants (Publisher agent preview)

After approval, Publisher agent renders per-platform variants:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Preview variants                                [Edit any]   [Schedule →] │
├────────────────────────────────────────────────────────────────────────────┤
│ ┌─ 🟠 Reddit r/javascript ──────────────────┐ ┌─ 🔵 LinkedIn ─────────┐    │
│ │ Title: We just shipped Acme SDK v2 —      │ │ After six months of    │    │
│ │ migrate with one line                     │ │ rewrites, v2 is live.  │    │
│ │                                           │ │ The biggest wins:      │    │
│ │ Body: Longtime user, first time author... │ │ • 1-line migration     │    │
│ │ [More ▼]                     [Edit] [✗]   │ │ [More ▼]  [Edit] [✗]   │    │
│ └───────────────────────────────────────────┘ └───────────────────────┘    │
│ ┌─ 🔴 YouTube community post ───────────────┐ ┌─ 🟢 dev.to ───────────┐    │
│ │ SDK v2 is out! Blog + video links below.  │ │ # Acme SDK v2: migrate │    │
│ │                             [Edit] [✗]    │ │ in one line            │    │
│ │                                           │ │ canonical_url set ✓    │    │
│ │                                           │ │                        │    │
│ │                                           │ │ [More ▼]  [Edit] [✗]   │    │
│ └───────────────────────────────────────────┘ └───────────────────────┘    │
│                                                                            │
│ Schedule: 2026-04-22 09:00 PT    [Change]              [Approve & Queue]  │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Inbox (unified) — Monitor + Reply agents output

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Inbox                                                                      │
├────────────────────────────────────────────────────────────────────────────┤
│ [All] [Needs approval 7] [Sent] [Ignored]   Platform: [All ▼]  🔍         │
├──────────────────────────────────┬─────────────────────────────────────────┤
│ 🟠 u/kernel_panic  · 12m         │  Thread: "SDK v2 migration"             │
│ ▸ "Does this break webhook       │  on r/javascript · your post · 2h ago   │
│   signing?" [question]           │                                         │
│ ● Draft ready · 87% confidence   │  ── Original post ──                    │
│ ─────────────────────────────────│  We just shipped Acme SDK v2...         │
│ 🔵 @sara_dev  · 24m              │                                         │
│ ▸ "Great timing — just hit this" │  ── u/kernel_panic ──                   │
│   [praise]                       │  Does this break webhook signing? I saw │
│ ◯ Auto-sent ✓                    │  v1 used HMAC but the docs are unclear. │
│ ─────────────────────────────────│                                         │
│ 🔴 @troll42  · 31m               │  ── Draft reply (Claude Sonnet) ──      │
│ ▸ "worst library ever"           │  Signing behavior is unchanged in v2 —  │
│ ● Escalated [complaint/low-val]  │  same HMAC-SHA256, same secret rotation │
│ ─────────────────────────────────│  flow. The docs section on signing got  │
│ 🟢 nate_m  · 1h                  │  rewritten — here's the direct link: …  │
│ ▸ "Typo: 'the the' in section 3" │                                         │
│ ◯ Auto-fixed on dev.to ✓         │  ┌ Why this reply ───────────────────┐ │
│                                  │  │ matched rule: technical Q → answer│ │
│                                  │  │ confidence 87% · policy ✓          │ │
│                                  │  │ [view full trace]                  │ │
│                                  │  └────────────────────────────────────┘ │
│                                  │                                         │
│                                  │  [Edit] [Send as-is] [Ignore] [Escalate]│
└──────────────────────────────────┴─────────────────────────────────────────┘
```

---

## 6. Agent trace viewer (explainability)

Click `view full trace` on any agent run:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Agent run · reply-agent · job_7f2a · 2.4s · $0.003 · Sonnet 4.6            │
├────────────────────────────────────────────────────────────────────────────┤
│ ▼ System prompt (voice.md + policy.md)  [cached hit ✓]                     │
│ ▼ Input                                                                    │
│     response_id: resp_9812  (r/javascript · u/kernel_panic)                │
│     thread_ctx:  3 messages loaded                                         │
│ ▼ Step 1 · tool: classify(text)                                            │
│     → { type: "question", topic: "webhook signing", sentiment: "neutral" } │
│ ▼ Step 2 · tool: get_thread(post_id)                                       │
│     → 3 comments, original post body, author karma 2.1k                    │
│ ▼ Step 3 · reasoning                                                       │
│     "Author is asking a technical clarification. Rule 'tech Q → answer'    │
│      applies. Drafting with a direct link per voice.md 'show, don't tell'."│
│ ▼ Step 4 · tool: draft_reply(…)                                            │
│     → "Signing behavior is unchanged in v2 — same HMAC-SHA256…"            │
│ ▼ Step 5 · self-eval                                                       │
│     confidence: 0.87 · policy_pass: true · tone_match: 0.91                │
│ ▼ Step 6 · decision: queue_for_approval (threshold for auto-send: 0.9)     │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Accounts — connect platforms

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Accounts                                                                   │
├────────────────────────────────────────────────────────────────────────────┤
│ Platform      Handle           Status         Scopes         Expires       │
│ 🟠 Reddit     @acme_dev        ● connected    post, reply    —             │
│ 🔵 LinkedIn   Acme Co.         ● connected    post           2026-04-23 ⚠  │
│               └ Community Mgmt API: ⏳ pending approval                    │
│ 🔴 YouTube    Acme Channel     ● connected    upload, reply  —             │
│ 🟢 dev.to     @acme            ● connected    API key        —             │
│ ⚫ Medium     —                ○ not connected [Connect]                   │
│ 🟣 Hashnode   —                ○ not connected [Connect]                   │
│                                                                            │
│ [＋ Add platform]                                                          │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Policy / voice editor

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Policy   [Voice] [Rules] [Escalation]                     [Save · v12]     │
├────────────────────────────────────────────────────────────────────────────┤
│ voice.md (used by Content + Publisher + Reply agents)                      │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ # Voice                                                            │    │
│ │ - Confident, specific, low-jargon.                                 │    │
│ │ - Prefer show-don't-tell; always link to the primary source.       │    │
│ │ - Never hype. No "game-changing", no "revolutionary".              │    │
│ │ - Reddit: match the sub's register; dev.to: longer, more code;     │    │
│ │   LinkedIn: first sentence is the hook; YT: conversational.        │    │
│ │                                                                    │    │
│ │ # Hard no-fly                                                      │    │
│ │ - Don't engage with /r/programmingcirclejerk.                      │    │
│ │ - Never reply to users with < 0 karma.                             │    │
│ │ - Escalate anything involving legal, pricing, or security.         │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│ Last edited by yaqwang  · 2d ago · [Diff vs v11]   [Test on sample input] │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Flow summary

```
     ┌──────┐   brief    ┌─────────┐  draft  ┌─────────┐  variants ┌────────┐
user │brief │──────────▶ │ Content │────────▶│ review  │─────────▶ │Publish │──▶ posted
     └──────┘            │  agent  │         │(human)  │           │ agent  │
                         └─────────┘         └─────────┘           └────────┘
                                                                       │
                                                                       ▼
     ┌─────┐  approve   ┌─────────┐  classify  ┌──────────┐   fetch    │
user │inbox│◀───────────│  Reply  │◀───────────│ Monitor  │◀───────────┘
     └─────┘            │  agent  │            │  agent   │    (cron /
                        └─────────┘            └──────────┘    webhook)
```
