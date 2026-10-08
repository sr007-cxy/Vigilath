// Site-wide command palette (⌘K / Ctrl+K) -- following Linear / Vercel / Supabase Studio.
//
// Why it exists: operators jump between 20+ workbench pages a day, and finding a page via collapsible sidebar groups
// takes two clicks + a visual scan on average; the group matching the route is also forced open, making scanning harder.
// The command palette turns "finding a page" into "type two characters and press Enter", the single biggest interaction improvement in this admin.
//
// No third-party (cmdk) dependency: the palette logic is just a controlled input + scored ranking + keyboard navigation,
// doable within 200 lines, saving a runtime dependency and the cost of overriding its styles.
//
// Data sources:
//   - ops pages from constants/opsNav, customer pages from constants/userNav (both the same as their sidebars, filtered by role)
//   - user-side pages / global actions in USER_PAGES / ACTIONS in this file
// Recently used items are stored in localStorage and shown first for empty queries -- ops' paths are highly repetitive,
// and "where did I go last" hits far more often than any default ordering.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth, resolveRole } from '../../contexts/AuthContext';
import { visibleOpsGroups } from '../../constants/opsNav';
import { visibleUserGroups } from '../../constants/userNav';
import { registerCommandPalette } from './commandPaletteBus';

interface Command {
  id: string;
  label: string;
  group: string;
  /** English aliases -- so English words/paths beyond pinyin initials can match under Chinese labels */
  keywords: string;
  to?: string;
  run?: () => void;
}

const RECENT_KEY = 'cmdk.recent';
const RECENT_MAX = 5;

function readRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]).slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

function pushRecent(id: string) {
  try {
    const next = [id, ...readRecent().filter((x) => x !== id)].slice(0, RECENT_MAX);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch { /* not writable in private mode; recently used is only valid for this session */ }
}

// Subsequence scoring: consecutive hits weighted, word-initial hits weighted -- consistent with cmdk / fzf intuition;
// "ycg" can match paths like "yuqing-cankao-guanli", while Chinese degrades to substring matching.
function score(text: string, q: string): number {
  if (!q) return 0;
  const t = text.toLowerCase();
  const idx = t.indexOf(q);
  if (idx >= 0) return 1000 - idx;           // whole-string match scores highest, the earlier the higher
  let ti = 0;
  let hit = 0;
  let streak = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) return -1;                // a character missed -> no match
    streak = found === ti ? streak + 1 : 0;
    hit += 1 + streak;
    ti = found + 1;
  }
  return hit;
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user, clearAuth } = useAuth();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const role = user ? resolveRole(user) : '';
  const loggedIn = !!user;
  // Writing ⌘ on Windows / Linux is misleading; show Ctrl as it is
  const modKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl+';

  const commands = useMemo<Command[]>(() => {
    const out: Command[] = [];

    // 1) Admin workbench -- same source and role filtering as the ops sidebar
    if (role) {
      for (const g of visibleOpsGroups(role)) {
        const groupLabel = t(g.labelKey);
        for (const item of g.items) {
          out.push({
            id: `ops:${item.to}:${item.labelKey}`,
            label: t(item.labelKey),
            group: `${t('nav.opsTitle')} · ${groupLabel}`,
            keywords: item.to,
            to: item.to,
          });
        }
      }
      // Customer workbench -- same source and role filtering as the /app sidebar
      for (const g of visibleUserGroups(role)) {
        const groupLabel = t(g.labelKey);
        for (const item of g.items) {
          out.push({
            id: `app:${item.to}`,
            label: t(item.labelKey),
            group: `${t('nav.appTitle')} · ${groupLabel}`,
            keywords: item.to,
            to: item.to,
          });
        }
      }
    }

    // 2) Common user-side pages
    const userPages: Array<[string, string, string, string]> = [
      ['/app/reports', '报告中心', 'reports report', '数据与报告'],
      ['/app/reports/daily', '日报', 'daily report', '数据与报告'],
      ['/app/reports/weekly', '周报', 'weekly report', '数据与报告'],
      ['/app/reports/monthly', '月报', 'monthly report', '数据与报告'],
      ['/app/reports/compliance', '合规审计', 'compliance audit', '数据与报告'],
      ['/app/reports/history', '检测历史', 'history records', '数据与报告'],
      ['/app/task-monitor', '任务监控', 'task monitor runs', '数据与报告'],
      ['/app/sentiment', '舆情监测', 'sentiment', '数据与报告'],
      ['/app/overview', '一键全面诊断', 'diagnosis check overview', '检测'],
      ['/checker', '开始新检测', 'checker new check scan', '检测'],
      ['/account/profile', '账户资料', 'profile account settings', '账户'],
      ['/account/billing', '会员与计费', 'membership plan pricing balance payments usage', '账户'],
      ['/account/billing?tab=balance', '余额与充值', 'balance wallet topup', '账户'],
      ['/account/integrations', '集成与通知', 'agent integration api token email sms', '账户'],
    ];
    if (loggedIn) {
      for (const [to, label, keywords, group] of userPages) {
        out.push({ id: `u:${to}`, label, group, keywords: `${keywords} ${to}`, to });
      }
    }

    // 3) Public pages -- the command palette is useful even when logged out (find pricing/cases/knowledge base)
    const publicPages: Array<[string, string, string]> = [
      ['/', '首页', 'home index'],
      ['/pricing', '定价', 'pricing plans'],
      ['/products-services', '产品与服务', 'products services'],
      ['/cases', '客户案例', 'cases customers'],
      ['/industry', '行业库', 'industry library'],
      ['/geo-knowledge', 'GEO 知识库', 'geo knowledge docs'],
      ['/faq', '常见问题', 'faq help'],
      ['/blog', '博客', 'blog posts'],
      ['/contact', '联系我们', 'contact sales'],
    ];
    for (const [to, label, keywords] of publicPages) {
      out.push({ id: `p:${to}`, label, group: '站点', keywords: `${keywords} ${to}`, to });
    }

    // 4) Global actions
    out.push({
      id: 'a:theme',
      label: '切换主题',
      group: '动作',
      keywords: 'theme dark light toggle',
      run: () => {
        const themes = ['peec', 'light', 'dark'];
        const cur = localStorage.getItem('theme') || 'peec';
        const next = themes[(themes.indexOf(cur) + 1) % themes.length];
        localStorage.setItem('theme', next);
        document.body.setAttribute('data-theme', next);
      },
    });
    if (loggedIn) {
      out.push({
        id: 'a:logout',
        label: '退出登录',
        group: '动作',
        keywords: 'logout sign out',
        run: () => { clearAuth(); navigate('/'); },
      });
    }
    return out;
  }, [role, loggedIn, t, clearAuth, navigate]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      // Empty query: recently used on top, the rest in registration order
      const recent = readRecent();
      const recentCmds = recent
        .map((id) => commands.find((c) => c.id === id))
        .filter((c): c is Command => !!c)
        .map((c) => ({ ...c, group: '最近使用' }));
      const rest = commands.filter((c) => !recent.includes(c.id));
      return [...recentCmds, ...rest];
    }
    return commands
      .map((c) => ({ c, s: Math.max(score(c.label, q), score(c.keywords, q) - 200) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .map((x) => x.c);
  }, [commands, query]);

  // Open/close shortcut. ⌘K must also open from inside an input (Linear's behavior),
  // but plain "/" focusing only applies outside inputs, otherwise it swallows normal typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        // Clear on every open/close: summoning it again should give a clean input, not leftovers from last time
        setQuery('');
        setActive(0);
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    // Also register an imperative entry, for the visible "Search pages" button on the sidebar
    registerCommandPalette(() => { setQuery(''); setActive(0); setOpen(true); });
    return () => {
      window.removeEventListener('keydown', onKey);
      registerCommandPalette(null);
    };
  }, []);

  // Only handles focus; resetting query / active is already done at the moment of opening/closing.
  // Focus after the dialog is on screen, otherwise iOS Safari doesn't get the cursor.
  useEffect(() => {
    if (!open) return;
    const raf = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(raf);
  }, [open]);

  const runCommand = useCallback((cmd: Command) => {
    pushRecent(cmd.id);
    setOpen(false);
    if (cmd.run) cmd.run();
    else if (cmd.to) navigate(cmd.to);
  }, [navigate]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
    else if (e.key === 'End') { e.preventDefault(); setActive(results.length - 1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = results[active];
      if (cmd) runCommand(cmd);
    }
  };

  // Scroll the highlighted item into view on keyboard movement -- without this, pressing ↓ in a long list "gets lost"
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    el?.scrollIntoView({ block: 'nearest' });
  }, [active, results]);

  if (!open) return null;

  // Grouped rendering: consecutive items in the same group share one group header. Compute first, then render --
  // mutating outer variables during rendering gets group headers wrong under StrictMode double rendering.
  const rows = results.map((cmd, i) => ({
    cmd,
    head: i === 0 || results[i - 1].group !== cmd.group ? cmd.group : null,
  }));

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(2px)' }}
      onMouseDown={() => setOpen(false)}
      role="presentation"
    >
      <div
        className="w-full max-w-xl rounded-xl overflow-hidden shadow-2xl"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-strong)' }}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
          <svg className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="搜索页面或动作…"
            className="flex-1 text-sm"
            // This must be inline to override the global input border/background from index.css,
            // otherwise an extra input box frame appears in the palette.
            style={{ color: 'var(--text-primary)', background: 'transparent',
                     border: 'none', outline: 'none', boxShadow: 'none', padding: 0 }}
            aria-label="搜索页面或动作"
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded border" style={{ borderColor: 'var(--border-color)', color: 'var(--text-muted)' }}>ESC</kbd>
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto py-1">
          {results.length === 0 && (
            <div className="px-4 py-10 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
              没有匹配的页面或动作
            </div>
          )}
          {rows.map(({ cmd, head }, i) => {
            const isActive = i === active;
            return (
              <div key={cmd.id}>
                {head && (
                  <div className="px-4 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider"
                       style={{ color: 'var(--text-muted)' }}>
                    {head}
                  </div>
                )}
                <button
                  type="button"
                  data-active={isActive}
                  onMouseMove={() => setActive(i)}
                  onClick={() => runCommand(cmd)}
                  className="w-full text-left px-4 py-2 text-sm flex items-center justify-between gap-3"
                  style={{
                    background: isActive ? 'var(--bg-tertiary)' : 'transparent',
                    color: isActive ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  }}
                >
                  <span className="truncate">{cmd.label}</span>
                  {cmd.to && (
                    <span className="text-[10px] shrink-0 opacity-60 font-mono">{cmd.to}</span>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="px-4 py-2 border-t flex items-center gap-4 text-[10px]"
             style={{ borderColor: 'var(--border-color)', color: 'var(--text-muted)' }}>
          <span><kbd className="font-mono">↑↓</kbd> 选择</span>
          <span><kbd className="font-mono">↵</kbd> 打开</span>
          <span className="ml-auto"><kbd className="font-mono">{modKey}K</kbd> 唤起</span>
        </div>
      </div>
    </div>
  );
}
