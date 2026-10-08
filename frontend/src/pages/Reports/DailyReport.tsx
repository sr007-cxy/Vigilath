// P2 daily report detail — four sections: metric day-over-day / high-value query cards / today's actions / full details.

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ReportsShell, type ShellState } from './shell';
import { reportsApi, type DailyReport as DailyReportData } from '../../services/reportsApi';
import { useReportsLang } from './lang';
import {
  EngineBadge, InfoHint, PeriodNavButtons, RankBadge, SectionCard, TD, TH,
  TrendBadge, usePeriodKeys,
} from './components';
import { exportElementPdf, exportReportPdfServer } from './exportPdf';
import { CompetitiveCompact } from './Competitive';

export function DailyReport() {
  const L = useReportsLang();
  return (
    <ReportsShell title={L.dailyTitle} backTo="/reports/daily">
      {(state) => <Body state={state} />}
    </ReportsShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic } = state;
  const L = useReportsLang();
  const { day = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<DailyReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showFlat, setShowFlat] = useState(false);
  const [showAllChanged, setShowAllChanged] = useState(false);
  const [exporting, setExporting] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const periodKeys = usePeriodKeys(topic?.id, token, 'daily');

  useEffect(() => {
    if (!topic || !day) return;
    setLoading(true);
    setError('');
    reportsApi.dailyReport(topic.id, token, day)
      .then(setData)
      .catch(e => { setData(null); setError(e.message || 'error'); })
      .finally(() => setLoading(false));
  }, [token, topic?.id, day, L]);   // L changes with the language -> refetch the bilingual text

  if (!topic) return null;
  if (loading) return <div className="text-xs text-muted text-center py-10">{L.loading}</div>;
  if (!data) return <div className="text-sm text-muted text-center py-16">{error || L.empty}</div>;

  // Daily detail analysis metric definition: changed rows (up/down/new) shown in full first, unchanged rows collapsed
  const changed = data.highlights.filter(h => h.change !== 'flat');
  const flats = data.highlights.filter(h => h.change === 'flat');
  const ups = changed.filter(h => h.change === 'up').length;
  const downs = changed.filter(h => h.change === 'down').length;
  const news = changed.filter(h => h.change === 'new').length;
  const top1Cells = data.highlights.filter(h => h.today_label === 'top1').length;
  const missCells = data.highlights.filter(h => h.today_label === 'miss').length;

  const onExport = async () => {
    if (!pageRef.current || exporting) return;
    setExporting(true);
    try {
      const filename = `daily-report-${data.date}.pdf`;
      if (!(await exportReportPdfServer(token, topic.id, filename))) {
        await exportElementPdf(pageRef.current, filename);
      }
    } finally {
      setExporting(false);
    }
  };

  const changeIcon = { up: '↑', down: '↓', flat: '—', new: '＋' };
  const changeColor = { up: '#15803d', down: '#b91c1c', flat: '#64748b', new: '#2563eb' };

  return (
    <div className="grid gap-4" ref={pageRef} data-export-root>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-primary">{L.dailyTitle} · {data.date}</div>
          <div className="text-[11px] text-muted mt-0.5">
            {L.dailyScope(
              new Set(data.detail.map(r => r.query)).size,
              new Set(data.detail.map(r => r.engine)).size,
              Math.max(1, ...data.highlights.map(h => h.runs)),
              data.detail.length,
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <PeriodNavButtons keys={periodKeys} current={day}
            onGo={k => navigate(`/reports/daily/${k}?topic=${topic.id}`)} />
          <button type="button" onClick={onExport} disabled={exporting}
            className="px-3 py-1 text-xs rounded disabled:opacity-60"
            style={{ background: 'var(--accent-primary)', color: 'white' }}>
            {exporting ? L.exporting : L.exportPdf}
          </button>
        </div>
      </div>

      {data.ai_summary && (
        <SectionCard title={L.secAiSummary} hint={L.aiSummaryHint}>
          <div className="text-xs text-secondary leading-relaxed whitespace-pre-wrap">
            {data.ai_summary}
          </div>
        </SectionCard>
      )}

      <SectionCard title={L.secKpiDiff} hint={`${L.metricHints.exposure}\n\n${L.hintTarget}`}>
        <table className="w-full">
          <thead>
            <tr>
              <TH>{L.colMetric}</TH>
              <TH right>{L.colYesterday}</TH>
              <TH right>{L.colToday}</TH>
              <TH right>{L.colDelta}</TH>
              <TH>{L.colNote}</TH>
            </tr>
          </thead>
          <tbody>
            {data.kpi.map(k => (
              <tr key={k.key}>
                <TD className="text-primary font-medium">
                  <span className="inline-flex items-center gap-1.5">
                    {L.metricLabels[k.key]}
                    <InfoHint text={L.metricHints[k.key]} />
                  </span>
                </TD>
                <TD right>{k.yesterday === null ? '—' : `${k.yesterday}%`}</TD>
                <TD right className="font-semibold">{k.today}%</TD>
                <TD right><TrendBadge deltaPp={k.delta_pp} /></TD>
                <TD className="text-secondary">{k.note}</TD>
              </tr>
            ))}
          </tbody>
        </table>
      </SectionCard>

      <CompetitiveCompact topicId={topic.id} token={token}
        periodType="daily" periodKey={day} title={L.secCompetitive} />

      <SectionCard title={L.secHighlights} hint={L.hintDailyDetail}>
        {data.highlights.length === 0 ? (
          <div className="text-xs text-muted py-4 text-center">{L.empty}</div>
        ) : (
          <div className="grid gap-3">
            {/* Daily detail analysis bar: total + up/down/new + placeholders at both ends */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 rounded-xl text-xs"
              style={{ background: 'linear-gradient(90deg, rgba(37,99,235,0.08), rgba(13,148,136,0.05))',
                border: '1px solid rgba(37,99,235,0.22)' }}>
              <span className="font-semibold text-primary">
                {L.detailSummary(data.highlights.length)}
              </span>
              <span style={{ color: '#15803d' }}>↑ {L.hlUp} <b className="tabular-nums">{ups}</b></span>
              <span style={{ color: '#b91c1c' }}>↓ {L.hlDown} <b className="tabular-nums">{downs}</b></span>
              <span style={{ color: '#2563eb' }}>＋ {L.hlNew} <b className="tabular-nums">{news}</b></span>
              <span className="text-secondary">TOP1 <b className="tabular-nums text-primary">{top1Cells}</b></span>
              <span className="text-secondary">{L.notExposed} <b className="tabular-nums text-primary">{missCells}</b></span>
            </div>

            {/* Changed rows (up/down/new) shown in full */}
            {changed.length === 0 ? (
              <div className="text-xs text-muted py-2 text-center">{L.noChangeToday}</div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr>
                    <TH>{L.colQuery}</TH>
                    <TH>{L.colEngine}</TH>
                    <TH>{L.colTodayRank}</TH>
                    <TH>{L.colYesterday}</TH>
                    <TH>{L.colChange}</TH>
                  </tr>
                </thead>
                <tbody>
                  {(showAllChanged ? changed : changed.slice(0, 20)).map((h, i) => (
                    <tr key={i}>
                      <TD className="text-primary max-w-[260px] truncate">{h.query}</TD>
                      <TD><EngineBadge engine={h.engine} /></TD>
                      <TD>
                        <RankBadge label={h.today_label} />
                      </TD>
                      <TD>
                        {h.change === 'new'
                          ? <span className="text-muted text-[11px]">{L.hlNew}</span>
                          : <RankBadge label={h.yesterday_label} />}
                      </TD>
                      <TD>
                        <span className="font-medium" style={{ color: changeColor[h.change] }}>
                          {changeIcon[h.change]}
                        </span>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {!showAllChanged && changed.length > 20 && (
              <button type="button" onClick={() => setShowAllChanged(true)}
                className="px-3 py-1.5 text-xs rounded-full justify-self-start"
                style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                ▸ {L.moreChanged(changed.length - 20)}
              </button>
            )}

            {/* Unchanged rows collapsed: only the count by default, expand to see */}
            {flats.length > 0 && (
              <div>
                <button type="button" onClick={() => setShowFlat(v => !v)}
                  className="px-3 py-1.5 text-xs rounded-full"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                  {showFlat ? '▾' : '▸'} {L.flatCollapsed(flats.length)}
                </button>
                {showFlat && (
                  <div className="mt-2" style={{ maxHeight: 320, overflowY: 'auto' }}>
                    <table className="w-full">
                      <tbody>
                        {flats.map((h, i) => (
                          <tr key={i}>
                            <TD className="text-primary max-w-[260px] truncate">{h.query}</TD>
                            <TD><EngineBadge engine={h.engine} /></TD>
                            <TD><RankBadge label={h.today_label} /></TD>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </SectionCard>

      <SectionCard title={L.secActions}>
        {data.actions.length === 0 ? (
          <div className="text-xs text-muted py-4 text-center">{L.emptyActions}</div>
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <TH>{L.colActionTime}</TH>
                <TH>{L.colActionTitle}</TH>
                <TH right>{L.colStatus}</TH>
              </tr>
            </thead>
            <tbody>
              {data.actions.map((a, i) => (
                <tr key={i}>
                  <TD className="text-muted tabular-nums whitespace-nowrap">{a.time}</TD>
                  <TD className="text-primary">{a.title}</TD>
                  <TD right>
                    <span className="px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                      {a.status}
                    </span>
                  </TD>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SectionCard>

    </div>
  );
}
