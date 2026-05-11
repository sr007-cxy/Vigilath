// 监测任务侧栏 — 对齐 WisersOne 原型.
//
// 数据来自 useSentimentAccounts():扁平 SentimentAccount 列表.UI 按 `target`
// 折叠分组 — 同一品牌下多个监测任务(不同 keyword_groups / 不同媒体白名单)收到
// 一个可展开的 group header 下;单账户 group 直接渲染叶子.
//
// 折叠状态用 localStorage 持久化,刷新页面不丢.
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { SentimentAccount } from '../../../../types/sentiment';

interface Props {
  accounts: SentimentAccount[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

const EXPANDED_KEY = 'sentiment.sidebar.collapsedGroups';

// 单账户的「任务名」— 优先用第一个 keyword_group 的 name(更具体),否则用 target+ticker
function leafLabel(a: SentimentAccount): string {
  const first = a.keyword_groups?.[0]?.name?.trim();
  if (first) return first;
  return a.ticker ? `${a.target} · ${a.ticker}` : a.target;
}

export function AccountSidebar({ accounts, selectedId, onSelect }: Props) {
  const { t } = useTranslation();

  // 按 target group;Map 迭代顺序 = 插入顺序 = accounts 的原顺序
  const groups = useMemo(() => {
    const m = new Map<string, SentimentAccount[]>();
    for (const a of accounts) {
      const key = a.target || '—';
      const arr = m.get(key) ?? [];
      arr.push(a);
      m.set(key, arr);
    }
    return Array.from(m.entries());
  }, [accounts]);

  // 折叠状态:存「折叠的 target」集合(默认全部展开)
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(EXPANDED_KEY);
      return new Set<string>(raw ? JSON.parse(raw) : []);
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify(Array.from(collapsed)));
    } catch { /* ignore quota */ }
  }, [collapsed]);

  const toggleGroup = (target: string) => {
    setCollapsed(prev => {
      const next = new Set(prev);
      if (next.has(target)) next.delete(target);
      else next.add(target);
      return next;
    });
  };

  if (!accounts.length) {
    return (
      <aside className="rounded-xl p-4 text-xs text-muted" style={cardStyle}>
        {t('dashboard.sentiment.sidebar.empty')}
      </aside>
    );
  }

  return (
    <aside className="rounded-xl py-3 self-start" style={cardStyle}>
      <header className="px-4 pb-2 mb-1 flex items-center justify-between"
        style={{ borderBottom: '1px solid var(--border-color)' }}>
        <span className="text-xs font-semibold text-secondary uppercase tracking-wider">
          {t('dashboard.sentiment.sidebar.title')}
        </span>
      </header>

      <nav className="px-2 space-y-0.5">
        {groups.map(([target, items]) => {
          // 单账户 group:不显示 header,直接渲染叶子
          if (items.length === 1) {
            const a = items[0];
            return (
              <LeafButton
                key={a.id}
                label={leafLabel(a)}
                selected={a.id === selectedId}
                onClick={() => onSelect(a.id)}
              />
            );
          }

          const isCollapsed = collapsed.has(target);
          const groupSelected = items.some(a => a.id === selectedId);
          return (
            <div key={target}>
              <button
                type="button"
                onClick={() => toggleGroup(target)}
                className="w-full px-2.5 py-1.5 rounded text-left text-xs font-semibold flex items-center justify-between transition-colors"
                style={{
                  color: groupSelected ? 'var(--accent-primary)' : 'var(--text-primary)',
                  background: 'transparent',
                }}
              >
                <span className="truncate">{target}</span>
                <span className="text-[10px] text-muted ml-1">{isCollapsed ? '▸' : '▾'}</span>
              </button>
              {!isCollapsed && (
                <div className="ml-2 mt-0.5 mb-1 space-y-0.5"
                  style={{ borderLeft: '1px solid var(--border-color)', paddingLeft: 4 }}>
                  {items.map(a => (
                    <LeafButton
                      key={a.id}
                      label={leafLabel(a)}
                      selected={a.id === selectedId}
                      onClick={() => onSelect(a.id)}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <footer className="px-3 pt-2 mt-2" style={{ borderTop: '1px solid var(--border-color)' }}>
        <Link
          to="/account/brand"
          className="block w-full text-xs px-2 py-1.5 rounded text-center hover:brightness-110"
          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
        >
          {t('dashboard.sentiment.sidebar.newTask')}
        </Link>
      </footer>
    </aside>
  );
}

function LeafButton({ label, selected, onClick }:
  { label: string; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full px-2.5 py-1.5 rounded text-left text-xs truncate transition-colors"
      style={{
        background: selected ? 'var(--bg-tertiary)' : 'transparent',
        color: selected ? 'var(--accent-primary)' : 'var(--text-secondary)',
        fontWeight: selected ? 600 : 400,
        borderLeft: selected ? '2px solid var(--accent-primary)' : '2px solid transparent',
      }}
      title={label}
    >
      {label}
    </button>
  );
}

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};
