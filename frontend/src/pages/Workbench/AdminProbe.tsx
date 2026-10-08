// Engine live test (admin + ops) — enter a question, pick an engine, run it once via real browser automation,
// returning the answer + an execution recording. Uses the workbench-dedicated account pool, not the sentiment monitoring accounts.
// Takes 30 seconds to 5 minutes: after submitting, poll every 3 seconds until done/failed.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  workbenchProbeApi,
  type ProbeEngine,
  type ProbeRun,
} from '../../services/workbenchProbeApi';
import { UiSelect } from '../../components/ui/Select';

const POLL_MS = 3000;

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function StatusBadge({ status }: { status: ProbeRun['status'] }) {
  const { t } = useTranslation();
  const cls =
    status === 'done'
      ? 'bg-green-500/10 text-green-600'
      : status === 'failed'
        ? 'bg-red-500/10 text-red-600'
        : 'bg-amber-500/10 text-amber-600';
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded ${cls}`}>
      {t(`workbench.adminProbe.status.${status}`)}
    </span>
  );
}

/** Recording player: fetch the Blob with auth -> objectURL -> <video> (the <video> tag can't carry request headers) */
function RunVideo({ runId, token }: { runId: number; token: string }) {
  const { t } = useTranslation();
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    setObjectUrl(null);
    setError(null);
    setLoading(true);
    workbenchProbeApi
      .fetchVideoBlob(token, runId)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setObjectUrl(url);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [runId, token]);

  if (loading) {
    return <div className="text-sm text-secondary">{t('workbench.adminProbe.videoLoading')}</div>;
  }
  if (error) {
    return <div className="text-sm text-red-600">{error}</div>;
  }
  if (!objectUrl) return null;
  return (
    <div className="space-y-2">
      <video src={objectUrl} controls className="w-full max-h-[480px] rounded bg-black" />
      <a
        href={objectUrl}
        download={`probe-${runId}.webm`}
        className="inline-block text-xs text-secondary hover:text-primary underline"
      >
        {t('workbench.adminProbe.videoDownload')}
      </a>
    </div>
  );
}

export function AdminProbe() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';

  const [engines, setEngines] = useState<ProbeEngine[]>([]);
  const [engine, setEngine] = useState('');
  const [query, setQuery] = useState('');
  const [run, setRun] = useState<ProbeRun | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [history, setHistory] = useState<ProbeRun[]>([]);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const running = run != null && (run.status === 'queued' || run.status === 'running');

  const stopTimers = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    if (elapsedTimer.current) {
      clearInterval(elapsedTimer.current);
      elapsedTimer.current = null;
    }
  }, []);

  const loadHistory = useCallback(() => {
    workbenchProbeApi
      .listRuns(token)
      .then(setHistory)
      .catch(() => undefined);
  }, [token]);

  useEffect(() => {
    workbenchProbeApi
      .listEngines(token)
      .then((list) => {
        setEngines(list);
        setEngine((cur) => cur || list[0]?.key || '');
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    loadHistory();
    return stopTimers;
  }, [token, loadHistory, stopTimers]);

  const startPolling = useCallback(
    (runId: number) => {
      stopTimers();
      setElapsed(0);
      elapsedTimer.current = setInterval(() => setElapsed((s) => s + 1), 1000);
      pollTimer.current = setInterval(() => {
        workbenchProbeApi
          .getRun(token, runId)
          .then((r) => {
            setRun(r);
            if (r.status === 'done' || r.status === 'failed') {
              stopTimers();
              loadHistory();
            }
          })
          .catch(() => undefined); // A single polling failure doesn't stop it; the next round continues
      }, POLL_MS);
    },
    [token, stopTimers, loadHistory],
  );

  const handleSubmit = useCallback(() => {
    const q = query.trim();
    if (!q || !engine || submitting || running) return;
    setSubmitting(true);
    setError(null);
    setRun(null);
    workbenchProbeApi
      .createRun(token, engine, q)
      .then((r) => {
        setRun(r);
        if (r.status === 'queued' || r.status === 'running') startPolling(r.id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSubmitting(false));
  }, [token, engine, query, submitting, running, startPolling]);

  const showHistoryRun = useCallback(
    (r: ProbeRun) => {
      if (running) return;
      stopTimers();
      setError(null);
      setRun(r);
      if (r.status === 'queued' || r.status === 'running') startPolling(r.id);
    },
    [running, stopTimers, startPolling],
  );

  const engineName = (key: string) => engines.find((e) => e.key === key)?.name || key;

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.adminProbe.title" titleFallback="监测数据" />
      <header>
        <h1 className="text-xl font-semibold text-primary">{t('workbench.adminProbe.heading')}</h1>
        <p className="text-xs text-secondary mt-0.5">{t('workbench.adminProbe.subtitle')}</p>
      </header>

      {/* Submission form */}
      <div className="card p-4 space-y-3">
        <div className="flex flex-col md:flex-row gap-3">
          <div>
            <label className="block text-xs text-secondary mb-1">
              {t('workbench.adminProbe.engineLabel')}
            </label>
            <UiSelect
              value={engine}
              onChange={setEngine}
              disabled={running || submitting}
              className="text-sm px-2 py-1.5 rounded border border-[var(--border-color)] bg-transparent text-primary min-w-[10rem] disabled:opacity-50"
              options={engines.map((e) => ({ value: e.key, label: e.name }))}
            />
          </div>
          <div className="flex-1">
            <label className="block text-xs text-secondary mb-1">
              {t('workbench.adminProbe.queryLabel')}
            </label>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={running || submitting}
              rows={3}
              maxLength={2000}
              placeholder={t('workbench.adminProbe.queryPlaceholder')}
              className="w-full text-sm px-2 py-1.5 rounded border border-[var(--border-color)] bg-transparent text-primary disabled:opacity-50"
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={running || submitting || !query.trim() || !engine}
            className="text-sm px-4 py-1.5 rounded bg-[var(--accent-primary)] text-white disabled:opacity-50"
          >
            {submitting
              ? t('workbench.adminProbe.submitting')
              : t('workbench.adminProbe.execute')}
          </button>
          <span className="text-xs text-secondary">{t('workbench.adminProbe.durationHint')}</span>
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-600 border border-red-500/30 rounded p-3">{error}</div>
      )}

      {/* Running */}
      {run && running && (
        <div className="card p-4 space-y-2">
          <div className="flex items-center gap-2">
            <StatusBadge status={run.status} />
            <span className="text-sm text-primary">
              {engineName(run.engine)} · {t('workbench.adminProbe.elapsed', { seconds: elapsed })}
            </span>
          </div>
          <p className="text-xs text-secondary">{t('workbench.adminProbe.runningHint')}</p>
        </div>
      )}

      {/* Result */}
      {run && !running && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge status={run.status} />
            <span className="text-sm font-medium text-primary">{engineName(run.engine)}</span>
            <span className="text-xs text-secondary">{fmtTime(run.finished_at)}</span>
          </div>
          <div className="text-sm text-secondary whitespace-pre-wrap break-words">
            {t('workbench.adminProbe.questionLabel')}: {run.query}
          </div>

          {run.error && (
            <div className="text-sm text-red-600 border border-red-500/30 rounded p-3 whitespace-pre-wrap">
              {run.error}
            </div>
          )}

          {run.answer && (
            <div>
              <div className="text-sm font-medium text-primary mb-1">
                {t('workbench.adminProbe.answerLabel')}
              </div>
              <div className="text-sm text-primary whitespace-pre-wrap break-words border border-[var(--border-color)] rounded p-3">
                {run.answer}
              </div>
            </div>
          )}

          {run.citations.length > 0 && (
            <div>
              <div className="text-sm font-medium text-primary mb-1">
                {t('workbench.adminProbe.citationsLabel')}
              </div>
              <ul className="space-y-1">
                {run.citations.map((c, i) => (
                  <li key={`${c.url}-${i}`} className="text-xs">
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary hover:underline break-all"
                    >
                      {c.title || c.url}
                    </a>
                    {c.domain && <span className="text-secondary ml-1">({c.domain})</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {run.video_url ? (
            <div>
              <div className="text-sm font-medium text-primary mb-1">
                {t('workbench.adminProbe.videoLabel')}
              </div>
              <RunVideo runId={run.id} token={token} />
            </div>
          ) : (
            run.status === 'done' && (
              <p className="text-xs text-secondary">{t('workbench.adminProbe.noVideo')}</p>
            )
          )}
        </div>
      )}

      {/* Recent runs */}
      {history.length > 0 && (
        <div className="card p-3 overflow-x-auto">
          <div className="text-sm font-medium text-primary mb-2">
            {t('workbench.adminProbe.historyTitle')}
          </div>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-xs text-secondary border-b border-[var(--border-color)]">
                <th className="py-2 pr-3 font-medium">{t('workbench.adminProbe.colEngine')}</th>
                <th className="py-2 pr-3 font-medium">{t('workbench.adminProbe.colQuery')}</th>
                <th className="py-2 pr-3 font-medium">{t('workbench.adminProbe.colStatus')}</th>
                <th className="py-2 pr-3 font-medium">{t('workbench.adminProbe.colTime')}</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => showHistoryRun(r)}
                  className="border-b border-[var(--border-color)] hover:bg-surface-hover cursor-pointer"
                >
                  <td className="py-2 pr-3 whitespace-nowrap">{engineName(r.engine)}</td>
                  <td className="py-2 pr-3 max-w-[24rem] truncate" title={r.query}>
                    {r.query}
                  </td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="py-2 pr-3 text-xs text-secondary whitespace-nowrap">
                    {fmtTime(r.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-secondary mt-2">{t('workbench.adminProbe.historyHint')}</p>
        </div>
      )}
    </div>
  );
}
