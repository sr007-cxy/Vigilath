// 风险等级分布饼图 — 纯 SVG。
import { useTranslation } from 'react-i18next';
import type { RiskLevel } from '../../../../mocks/sentiment';

interface Slice {
  level: RiskLevel;
  count: number;
  color: string;
}

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function RiskPie({ data }: { data: Slice[] }) {
  const { t } = useTranslation();
  const total = data.reduce((s, x) => s + x.count, 0) || 1;

  // 用 stroke-dasharray 画环形图,centred 在 50,50,半径 40,周长 ~251.3
  const R = 40;
  const C = 2 * Math.PI * R;
  // 预计算每段 offset,避免 render 中可变状态
  const segments = data.reduce<{ slice: Slice; dash: number; offset: number }[]>(
    (acc, s) => {
      const dash = (s.count / total) * C;
      const offset = -acc.reduce((sum, x) => sum + x.dash, 0);
      acc.push({ slice: s, dash, offset });
      return acc;
    },
    [],
  );

  return (
    <section className="rounded-xl p-5" style={cardStyle}>
      <h3 className="text-sm font-semibold text-primary uppercase tracking-wide mb-3">
        {t('dashboard.sentiment.today.riskDist')}
      </h3>
      <div className="flex items-center gap-4">
        <svg viewBox="0 0 100 100" width={120} height={120} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx="50" cy="50" r={R} fill="none" stroke="var(--bg-tertiary)" strokeWidth="14" />
          {segments.map(({ slice: s, dash, offset }) => (
            <circle
              key={s.level}
              cx="50" cy="50" r={R}
              fill="none"
              stroke={s.color}
              strokeWidth="14"
              strokeDasharray={`${dash} ${C}`}
              strokeDashoffset={offset}
            >
              <title>{`${s.level}: ${s.count} (${((s.count/total)*100).toFixed(0)}%)`}</title>
            </circle>
          ))}
        </svg>
        <ul className="flex-1 space-y-1.5 text-xs">
          {data.map(s => (
            <li key={s.level} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: s.color }} />
                <span className="text-secondary">{t(`dashboard.sentiment.articles.risk.${s.level}`)}</span>
              </span>
              <span className="font-mono tabular-nums text-primary">
                {s.count} <span className="text-muted">({((s.count/total)*100).toFixed(0)}%)</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
