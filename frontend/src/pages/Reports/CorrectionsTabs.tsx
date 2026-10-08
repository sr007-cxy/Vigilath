// Weekly / monthly report "Correction & breakthrough" dual tabs — highlighting the two key delivery capabilities: correction (turning negatives positive) and breakthrough (fully covering unexposed queries).
// Copy uses a local mini dictionary (lang.ts is occupied by a parallel feature; can be merged up later).

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CorrectionRow } from '../../services/reportsApi';

const DICT = {
  zh: {
    correction: '纠偏',
    attack: '攻坚',
    correctionFull: '负面纠偏',
    attackFull: '未露出攻坚',
    summary: (fixed: number, doing: number, attack: number) =>
      `本期发现负面/偏差 ${fixed + doing} 项,已回正 ${fixed} 项;${attack} 个词待攻坚`,
    fixedTag: '已回正',
    doingTag: '纠偏进行中',
    attackTag: '待攻坚',
    emptyCorrection: '本期无负面/偏差应答,内容中正率保持高位',
    emptyAttack: '本期无未露出词,提示词全部打透',
    itemLabel: '发现',
    actionLabel: '处理方式',
    issueLabels: { negative: '负面/偏差表述', unhit: '本期未露出' } as Record<string, string>,
    actionLabels: { replace: '权威内容覆盖 + 信源置换', supply: '场景层内容补给 + 下期攻坚清单' } as Record<string, string>,
  },
  en: {
    correction: 'Correction',
    attack: 'Breakthrough',
    correctionFull: 'Negative correction',
    attackFull: 'Zero-exposure breakthrough',
    summary: (fixed: number, doing: number, attack: number) =>
      `${fixed + doing} negative issues found, ${fixed} corrected; ${attack} keywords to break through`,
    fixedTag: 'Corrected',
    doingTag: 'In progress',
    attackTag: 'To attack',
    emptyCorrection: 'No negative answers this period — accuracy stays high',
    emptyAttack: 'No zero-exposure keywords this period',
    itemLabel: 'Found',
    actionLabel: 'Action',
    issueLabels: { negative: 'Negative / biased answer', unhit: 'Zero exposure this period' } as Record<string, string>,
    actionLabels: { replace: 'Authoritative content + source replacement', supply: 'Scenario content supply + next attack list' } as Record<string, string>,
  },
};

const isAttack = (c: CorrectionRow) => c.issue_type === 'unhit' || c.status === 'attack';

export function CorrectionsTabs({ rows }: { rows: CorrectionRow[] }) {
  const { i18n } = useTranslation();
  const T = (i18n.language || 'zh').toLowerCase().startsWith('en') ? DICT.en : DICT.zh;
  const { corrections, attacks } = useMemo(() => ({
    corrections: rows.filter(c => !isAttack(c)),
    attacks: rows.filter(isAttack),
  }), [rows]);
  const fixed = corrections.filter(c => c.status === 'fixed').length;
  const [tab, setTab] = useState<'correction' | 'attack'>(corrections.length ? 'correction' : 'attack');
  const list = tab === 'correction' ? corrections : attacks;

  const tabs = [
    { key: 'correction' as const, label: T.correction, n: corrections.length, fg: 'var(--warn-fg)', bg: 'var(--warn-bg)' },
    { key: 'attack' as const, label: T.attack, n: attacks.length, fg: 'var(--info-fg)', bg: 'var(--info-bg)' },
  ];

  return (
    <div className="grid gap-3">
      {/* Capability summary banner */}
      <div className="px-4 py-2.5 rounded-lg text-xs font-medium"
        style={{ background: 'linear-gradient(90deg, rgba(37,99,235,0.08), rgba(124,58,237,0.05))',
          border: '1px solid rgba(37,99,235,0.22)', color: 'var(--text-primary)' }}>
        🛠️ {T.summary(fixed, corrections.length - fixed, attacks.length)}
      </div>

      {/* Tab switch */}
      <div className="flex items-center gap-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className="px-4 py-1.5 text-xs rounded-full transition flex items-center gap-1.5"
            style={tab === t.key
              ? { background: 'var(--accent-primary)', color: 'var(--accent-contrast)', fontWeight: 600 }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
            {t.label}
            <span className="px-1.5 rounded-full text-[10px] font-bold tabular-nums"
              style={tab === t.key
                ? { background: 'rgba(255,255,255,0.25)', color: 'var(--accent-contrast)' }
                : { background: t.bg, color: t.fg }}>
              {t.n}
            </span>
          </button>
        ))}
      </div>

      {/* List (card style) */}
      {list.length === 0 ? (
        <div className="text-xs text-muted py-4 text-center">
          {tab === 'correction' ? T.emptyCorrection : T.emptyAttack}
        </div>
      ) : (
        <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {list.map((c, i) => {
            const good = c.status === 'fixed';
            const tag = tab === 'attack' ? T.attackTag : good ? T.fixedTag : T.doingTag;
            const tagStyle = good
              ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
              : tab === 'attack'
                ? { background: 'var(--info-bg)', color: 'var(--info-fg)' }
                : { background: 'var(--warn-bg)', color: 'var(--warn-fg)' };
            return (
              <div key={i} className="p-3 rounded-lg grid gap-1.5"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] text-muted">{T.issueLabels[c.issue_type] || c.issue_type}</span>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap" style={tagStyle}>
                    {tag}
                  </span>
                </div>
                <div className="text-xs text-primary font-medium">{c.found}</div>
                <div className="text-[11px] text-secondary">{T.actionLabel}:{T.actionLabels[c.action] || c.action}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
