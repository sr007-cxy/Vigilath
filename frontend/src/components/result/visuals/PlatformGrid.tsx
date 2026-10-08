import { useTranslation } from 'react-i18next';
import { parseCrossPlatform, type PlatformState } from '../lib/parseChecks';
import type { CheckResult } from '../../../types/geo';

interface Props {
  checks: CheckResult[];
  variant?: 'cross-platform' | 'social';
}

// Platform badges use a "first character" glyph block, without any third-party brand logos.
const monogram = (name: string): string => {
  if (/^[\x00-\x7F]+$/.test(name)) return name.slice(0, 2).toUpperCase();
  return name.slice(0, 1);
};

const STATE_THEME: Record<PlatformState, { ring: string; text: string; bg: string }> = {
  linked: {
    ring: 'border-emerald-500/50 ring-1 ring-emerald-400/30',
    text: 'text-emerald-600',
    bg: 'bg-emerald-500/10',
  },
  probed: {
    ring: 'border-cyan-500/50 ring-1 ring-cyan-400/30',
    text: 'text-cyan-600',
    bg: 'bg-cyan-500/10',
  },
  notFound: {
    ring: 'border-border',
    text: 'text-muted',
    bg: 'bg-tertiary',
  },
  unknown: {
    ring: 'border-border',
    text: 'text-muted',
    bg: 'bg-tertiary',
  },
};

export function PlatformGrid({ checks, variant = 'cross-platform' }: Props) {
  const { t } = useTranslation();
  const parsed = parseCrossPlatform(checks);
  if (!parsed) return null;

  const header =
    variant === 'social'
      ? t('result.visuals.platform.titleSocial')
      : t('result.visuals.platform.titleCross');

  return (
    <div className="bg-card border border-border rounded-xl p-4 sm:p-5 relative overflow-hidden">
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-pink-400/50 to-transparent" />

      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg gradient-bg flex items-center justify-center shadow-glow">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-xs font-semibold text-primary">{header}</span>
            <span className="text-[10px] uppercase tracking-wider text-muted">
              {t('result.visuals.platform.subtitle')}
            </span>
          </div>
        </div>
        <span className="text-[10px] font-mono text-muted">
          {parsed.foundCount}/{parsed.totalCount}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        {parsed.platforms.map((p) => {
          const theme = STATE_THEME[p.state];
          const isFound = p.state === 'linked' || p.state === 'probed';
          return (
            <a
              key={p.name}
              href={p.url || undefined}
              target={p.url ? '_blank' : undefined}
              rel={p.url ? 'noopener noreferrer' : undefined}
              className={`flex flex-col gap-1 px-2.5 py-2 rounded-lg border ${theme.ring} ${theme.bg} transition-all ${
                p.url ? 'hover:-translate-y-0.5 hover:shadow-glow cursor-pointer' : 'cursor-default'
              }`}
              title={p.url || t('result.visuals.platform.notDetected', { name: p.name })}
            >
              <div className="flex items-center justify-between">
                <span className={`inline-flex items-center justify-center h-5 min-w-5 px-1 rounded text-[10px] font-semibold ${theme.bg} ${theme.text}`}>
                  {monogram(p.name)}
                </span>
                {isFound && (
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="h-3 w-3 text-emerald-600" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
              <span className="text-[10px] font-medium text-primary truncate" title={p.name}>
                {p.name}
              </span>
            </a>
          );
        })}
      </div>
    </div>
  );
}
