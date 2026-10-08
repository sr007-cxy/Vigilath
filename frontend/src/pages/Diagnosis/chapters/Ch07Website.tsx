// Chapter 07 Website — all on-site technical items are gathered in this chapter
//   7.1 Website health overview -> 7.2 five weakness clusters -> 7.3 twenty-five categories itemized -> 7.4 answer engine optimization -> 7.5 AI crawler accessibility
// Ordered from conclusion to detail; crawlers aren't first (usually all green, and an all-green opening misleads readers into thinking the whole chapter is fine).
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  subData, StatBox, SectionSub, NoDataCard, ChapterDataStatus, Prose, ScoreItemBar,
} from '../reportKit';
import { severityStyle } from '../palette';
import type { ChapterCtx } from './types';

interface CheckItem {
  category: string; status: string; message: string; fix?: string | null;
  message_key?: string | null; message_params?: Record<string, unknown> | null;
  fix_key?: string | null; fix_params?: Record<string, unknown> | null;
}

/**
 * Localized rendering of health-check conclusions: prefer message_key via the i18n entry, fall back to the original text.
 * Same strategy as renderCheckMessage in pages/Result.tsx -- works both before and after the backend fills in keys.
 */
function useCheckText() {
  const { t } = useTranslation();
  // fallbackLng is off, so with a missing entry t() returns the key as-is -- the report would then print
  // things like "result.checks.trust_safety.privacy_missing" (this appeared on the 2026-08-20 Acme Cloud
  // PDF). Missing keys always fall back to the backend's original text: English isn't perfect, but at least it's a human sentence.
  const pick = (key: string | null | undefined, params: any, raw: string): string => {
    if (!key) return raw || '';
    const out = String(t(key, params || {}));
    return out === key ? (raw || key) : out;
  };
  return {
    msg: (c: CheckItem): string => pick(c.message_key, c.message_params, c.message),
    fix: (c: CheckItem): string => pick(c.fix_key, c.fix_params, c.fix || ''),
  };
}

const STATUS_STYLE: Record<string, { bg: string; c: string }> = {
  PASS: { bg: 'var(--success-bg)', c: 'var(--success-fg)' },
  WARN: { bg: 'var(--warn-bg)',    c: 'var(--warn-fg)' },
  FAIL: { bg: 'var(--danger-bg)',  c: 'var(--danger-fg)' },
  INFO: { bg: 'var(--neutral-bg)', c: 'var(--neutral-fg)' },
};

export function Ch07Website({ report, n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const base = subData(sub, 'base');
  const aeo = subData(sub, 'aeo');
  const crawl = subData(sub, 'crawlTest');

  if (!base && !aeo && !crawl) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['base', 'aeo', 'crawlTest']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  const blocked: string[] = crawl
    ? [...(((crawl.robots as any)?.blocked as string[]) || []),
       ...(((crawl.waf as any)?.blocked as string[]) || [])]
    : [];

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['base', 'aeo', 'crawlTest']} sub={sub} />

      {n.infrastructure_note?.summary && <Prose tinted>{n.infrastructure_note.summary}</Prose>}

      {/* 7.1 Overview */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {base && (
          <StatBox label={t('diagnosis.report.sections.website.baseScore')}
                   value={`${base.score}/100 ${base.grade || ''}`} accent />
        )}
        {base && (
          <StatBox label={t('diagnosis.report.sections.website.passCount')} value={base.pass_count ?? 0} />
        )}
        {base && (
          <StatBox label={t('diagnosis.report.sections.website.warnCount')} value={base.warn_count ?? 0}
                   hue="var(--warn-solid)" accent={Number(base.warn_count) > 0} />
        )}
        {base && (
          <StatBox label={t('diagnosis.report.sections.website.failCount')} value={base.fail_count ?? 0}
                   hue="var(--danger-solid)" accent={Number(base.fail_count) > 0} />
        )}
      </div>

      {/* 7.2 Five weakness clusters */}
      {base && (base.clusters?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.website.clusters')} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(base.clusters as any[]).map((c) => (
              <ClusterCard key={c.key} cluster={c} />
            ))}
          </div>
        </div>
      )}

      {/* 7.3 Twenty-five categories itemized */}
      {base && ((base.all_checks as CheckItem[])?.length ?? 0) > 0 && (
        <AllChecksTable checks={base.all_checks as CheckItem[]} />
      )}

      {/* 7.4 Answer engine optimization */}
      {aeo && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.website.aeoTitle')} />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <StatBox label={t('diagnosis.report.sections.website.aeoScore')}
                     value={`${aeo.score}/100`} accent />
          </div>
          {(aeo.priority_improvements?.length ?? 0) > 0 && (
            <>
              <div className="text-sm text-secondary mb-1.5">
                {t('diagnosis.report.sections.website.aeoImprovements')}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(aeo.priority_improvements as { category: string; percent: number }[])
                  .slice(0, 10).map((p) => (
                    <span key={p.category} className="px-2 py-0.5 rounded-md text-xs"
                          style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}>
                      {p.category} {p.percent}%
                    </span>
                  ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* 7.5 AI crawler accessibility */}
      {crawl && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.website.crawlTitle')} />
          <p className="text-sm text-primary leading-relaxed m-0">
            {blocked.length === 0
              ? t('diagnosis.report.sections.website.crawlAllOk')
              : `${t('diagnosis.report.sections.website.crawlBlocked')}:${blocked.join('、')}`}
          </p>
          {n.infrastructure_note?.crawler_note && (
            <p className="text-sm text-secondary leading-relaxed mt-1.5 m-0">
              {n.infrastructure_note.crawler_note}
            </p>
          )}
        </div>
      )}

      {/* Key issues and fixes from the model */}
      {(n.infrastructure_note?.top_issues?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.website.topIssues')} />
          <div className="space-y-2">
            {n.infrastructure_note!.top_issues!.map((it: any, i: number) => {
              const sv = severityStyle(it.severity);
              return (
                <div key={i} className="p-3 rounded-lg" style={{ background: sv.bg }}>
                  <div className="text-sm font-semibold" style={{ color: sv.c }}>
                    {it.cluster ? `【${it.cluster}】` : ''}{it.issue}
                  </div>
                  {it.fix && (
                    <p className="text-sm text-primary leading-relaxed mt-1 m-0">{it.fix}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}

function ClusterCard({ cluster: c }: { cluster: any }) {
  const { t } = useTranslation();
  const ct = useCheckText();
  const [open, setOpen] = useState(false);
  const sv = severityStyle(c.severity);
  const checks = (c.checks || []) as CheckItem[];
  const bad = checks.filter((x) => x.status === 'FAIL' || x.status === 'WARN');

  return (
    <div className="p-3 rounded-xl" style={{ background: sv.bg }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold text-primary">{c.title_zh}</span>
        <span className="text-xs font-bold" style={{ color: sv.c }}>
          {t(`diagnosis.report.sections.website.severity.${c.severity}`, { defaultValue: c.severity })}
        </span>
      </div>
      {/* The bullets from the backend are geo_checker's original English text (truncated messages);
          rendering via i18n entries gives the localized text; fall back to bullets only when checks are unavailable */}
      {(bad.length ? bad.slice(0, 3).map(ct.msg) : (c.bullets || []).slice(0, 3))
        .map((b: string, i: number) => (
          <p key={i} className="text-sm text-secondary m-0 leading-relaxed">· {b}</p>
        ))}
      {bad.length > 3 && (
        <>
          <button type="button" onClick={() => setOpen((v) => !v)}
                  className="text-xs mt-1.5 font-semibold print-hidden" style={{ color: sv.c }}>
            {open
              ? t('diagnosis.report.sections.website.collapse')
              : t('diagnosis.report.sections.website.expandCluster', { n: bad.length })}
          </button>
          {open && (
            <div className="mt-1.5 space-y-1">
              {bad.map((x, i) => (
                <p key={i} className="text-sm text-secondary m-0 leading-relaxed">
                  · {ct.msg(x)}
                </p>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AllChecksTable({ checks }: { checks: CheckItem[] }) {
  const { t } = useTranslation();
  const ct = useCheckText();
  const [open, setOpen] = useState(false);
  // Expand only items with issues by default (this also decides what gets captured in PDF export)
  const bad = checks.filter((c) => c.status === 'FAIL' || c.status === 'WARN');
  const shown = open ? checks : bad;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <SectionSub title={t('diagnosis.report.sections.website.allChecks')} />
        <button type="button" onClick={() => setOpen((v) => !v)}
                className="text-xs font-semibold print-hidden" style={{ color: 'var(--data-accent)' }}>
          {open
            ? t('diagnosis.report.sections.website.showProblemsOnly')
            : t('diagnosis.report.sections.website.showAll', { n: checks.length })}
        </button>
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-secondary m-0">{t('diagnosis.report.sections.website.allPassed')}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-soft">
          <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-tertiary)' }}>
                <th className="text-left px-3 py-2 font-medium text-secondary whitespace-nowrap">
                  {t('diagnosis.report.sections.website.colCategory')}
                </th>
                <th className="px-2 py-2 font-medium text-secondary">
                  {t('diagnosis.report.sections.website.colStatus')}
                </th>
                <th className="text-left px-3 py-2 font-medium text-secondary">
                  {t('diagnosis.report.sections.website.colConclusion')}
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c, i) => {
                const st = STATUS_STYLE[c.status] || STATUS_STYLE.INFO;
                const fix = ct.fix(c);
                return (
                  <tr key={i} style={{ borderTop: '1px solid var(--grid-line)' }}>
                    <td className="px-3 py-2 text-primary whitespace-nowrap">{c.category}</td>
                    <td className="px-2 py-2 text-center">
                      <span className="inline-block px-1.5 py-0.5 rounded text-xs font-semibold"
                            style={{ background: st.bg, color: st.c }}>
                        {t(`diagnosis.report.sections.website.checkStatus.${c.status}`,
                           { defaultValue: c.status })}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="text-primary leading-relaxed">{ct.msg(c)}</div>
                      {fix && (
                        <div className="text-sm text-secondary leading-relaxed mt-1">
                          {t('diagnosis.report.sections.website.colFix')}:{fix}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export { ScoreItemBar };
