// One-click full diagnosis — form page + historical report list.
// Entry: the first card in the home page "Advanced audit capabilities"; after submitting, go to /diagnosis/:id for polling and rendering.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fmtTimeIn } from '../../utils/datetime';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { PageHeader } from '../../components/ui';
import { useAuth } from '../../contexts/AuthContext';
import { PhoneLogin } from '../../components/PhoneLogin';
import { validateUrl, normalizeUrl } from '../../utils/validateUrl';
import {
  fullDiagnosisApi,
  type FullDiagnosisSummary,
} from '../../services/fullDiagnosisApi';

const STATUS_COLOR: Record<string, string> = {
  generating: '#6366f1',
  ready: '#10b981',
  failed: '#ef4444',
};

// Tag input: Enter/blur turns it into a chip, up to 5
export function TagField({ label, placeholder, hint, values, onChange, normalize, labelAction }: {
  label: string;
  placeholder: string;
  hint?: string;
  values: string[];
  onChange: (v: string[]) => void;
  normalize?: (v: string) => string;
  labelAction?: React.ReactNode;   // Action on the right of the label row (e.g. "Extract industry terms"), placed nearby
}) {
  const [input, setInput] = useState('');
  const add = () => {
    let v = input.trim();
    if (normalize) v = normalize(v);
    if (!v || values.includes(v) || values.length >= 5) { setInput(''); return; }
    onChange([...values, v]);
    setInput('');
  };
  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <label className="block text-sm font-medium text-primary">{label}</label>
        {labelAction}
      </div>
      <div className="flex flex-wrap items-center gap-2 py-1.5 px-2 rounded-xl bg-surface border border-soft">
        {values.map((c) => (
          <span key={c}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ background: 'rgba(99,102,241,0.12)', color: 'var(--accent-primary)' }}>
            {c}
            <button type="button" className="font-bold"
                    onClick={() => onChange(values.filter((x) => x !== c))}>
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); add(); }
          }}
          onBlur={add}
          placeholder={values.length ? '' : placeholder}
          className="flex-1 min-w-[120px] py-1 px-2 text-sm bg-transparent focus:outline-none text-primary border-none"
        />
        {input.trim() && (
          // Mobile users don't know to press Enter, so provide an explicit "Add" button
          <button type="button" onClick={add}
                  className="shrink-0 px-3 py-1 rounded-full text-xs font-medium text-white"
                  style={{ background: 'var(--solid-btn-bg, #0a0a0a)' }}>
            添加
          </button>
        )}
      </div>
      {hint && <p className="text-xs text-muted mt-1">{hint}</p>}
    </div>
  );
}

export function DiagnosisHome() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { token, setToken } = useAuth();

  const initialUrl = (location.state as { initialUrl?: string } | null)?.initialUrl || '';
  const [url, setUrl] = useState(initialUrl);
  const [brand, setBrand] = useState('');
  const [industryTerms, setIndustryTerms] = useState<string[]>([]);
  const [brandTerms, setBrandTerms] = useState<string[]>([]);
  const [competitors, setCompetitors] = useState<string[]>([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [needPhone, setNeedPhone] = useState(false);   // Not logged in -> bind phone number inline
  const [suggesting, setSuggesting] = useState(false);          // Extracting industry terms
  const [suggestions, setSuggestions] = useState<string[]>([]);  // Extracted candidates, click to add
  const aliveRef = useRef(true);
  const [history, setHistory] = useState<FullDiagnosisSummary[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const PAGE_SIZE = 10;

  // Pulled out of the workbench, this is a standalone page: logged-out users are no longer kicked back home. Identifying users is left to the site-wide silent login
  // (WechatSilentAuth) + inline phone binding at submit time.
  useEffect(() => () => { aliveRef.current = false; }, []);

  const loadHistory = useCallback(async (p: number) => {
    try {
      const res = await fullDiagnosisApi.list(p, PAGE_SIZE);
      setHistory(res.items);
      setTotal(res.total || 0);
      // When deletion empties the page (or concurrent deletion), go back to the previous page
      if (res.items.length === 0 && p > 1) { setPage(p - 1); return; }
    } catch {
      // History list load failure doesn't block the form
    } finally {
      setHistoryLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (token) loadHistory(page);
  }, [token, page, loadHistory]);

  // Official account menu "My reports" -> /app/reports -> redirects here with #history: once the history list loads, scroll to it,
  // otherwise on mobile the first screen shows only the form and users think "there are no records". Scroll only once after the first load.
  const scrolledRef = useRef(false);
  useEffect(() => {
    if (scrolledRef.current || !historyLoaded || location.hash !== '#history') return;
    scrolledRef.current = true;
    document.getElementById('history')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [historyLoaded, location.hash]);

  const generate = useCallback(async () => {
    setBusy(true);
    try {
      const res = await fullDiagnosisApi.generate(
        url.trim() ? normalizeUrl(url) : '',
        brand.trim(), competitors, industryTerms, brandTerms);
      if (!aliveRef.current) return;
      navigate(`/diagnosis/${res.report_id}`);
    } catch (e2: unknown) {
      if (!aliveRef.current) return;
      setErr(e2 instanceof Error ? e2.message : String(e2));
      setBusy(false);
    }
  }, [url, brand, competitors, industryTerms, brandTerms, navigate]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    // Website is optional: brands without a website run only the four AI-side items; validate the format only if filled
    if (url.trim() && !validateUrl(url)) { setErr(t('diagnosis.form.urlInvalid')); return; }
    if (!brand.trim()) { setErr(t('diagnosis.form.brandRequired')); return; }
    if (industryTerms.length === 0) { setErr(t('diagnosis.form.industryRequired')); return; }
    if (token) { void generate(); return; }
    setNeedPhone(true);   // Not logged in -> bind phone number inline, then continue automatically after binding
  };

  const onAuthed = useCallback(async (accessToken: string) => {
    setToken(accessToken);
    setNeedPhone(false);
    // Form already filled (binding triggered by clicking audit) -> start right after binding; form still empty (bound on page entry) -> back to the form to fill it
    const ready = !!brand.trim() && industryTerms.length > 0 && (!url.trim() || validateUrl(url));
    if (ready) await generate();
  }, [setToken, generate, brand, industryTerms, url]);

  // After a new WeChat user's silent login (has openid, phone pending, no token yet): pop the phone binding as soon as this page opens,
  // consistent with QR-scan login. After binding, onAuthed decides between returning to the form or starting directly based on whether the form is filled.
  useEffect(() => {
    if (token) { setNeedPhone(false); return; }
    const check = () => {
      try { if (sessionStorage.getItem('wx_bind_token')) setNeedPhone(true); } catch { /* private mode */ }
    };
    check();                                        // Check on mount (may already be set)
    window.addEventListener('wx-need-phone', check); // Silent login sets it async a step later; the event covers that
    return () => window.removeEventListener('wx-need-phone', check);
  }, [token]);

  // One-click industry term extraction: send brand + website for the backend to infer category terms for the user to pick. Easier than typing and pressing Enter on mobile.
  const extractTerms = useCallback(async () => {
    if (!brand.trim()) { setErr(t('diagnosis.form.brandRequired')); return; }
    setErr(''); setSuggesting(true);
    try {
      const terms = await fullDiagnosisApi.suggestIndustryTerms(brand.trim(), url.trim());
      const fresh = terms.filter((x) => !industryTerms.includes(x));
      setSuggestions(fresh);
      if (fresh.length === 0) setErr('没提取到新的行业词,请手动填写');
    } catch (e2: unknown) {
      setErr(e2 instanceof Error ? e2.message : '行业词提取失败');
    } finally {
      setSuggesting(false);
    }
  }, [brand, url, industryTerms, t]);

  const addSuggestion = useCallback((term: string) => {
    setIndustryTerms((prev) => (prev.includes(term) ? prev : [...prev, term]));
    setSuggestions((prev) => prev.filter((x) => x !== term));
  }, []);

  return (
  // The full-page frame (min-h-screen + grid-background) and centered content column were removed: this page now lives at
  // /app/diagnosis, where height, background and padding belong to SidebarShell's main. Keeping max-w-3xl would shrink it
  // into the middle on wide screens while neighboring pages are full width -- exactly the source of "some centered, some left-aligned".
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <PageHead titleKey="diagnosis.pageTitle" titleFallback="一键全面诊断" />
      <main className="animate-fade-in max-w-2xl mx-auto px-4 py-6 space-y-4">
        <PageHeader title={t('diagnosis.form.title')} hint={t('diagnosis.form.subtitle')} />
        <div className="w-full space-y-4">
          <section className="card p-6 sm:p-8">
            {needPhone ? (
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm text-secondary m-0">绑定手机号即可查看报告,之后再进来无需再填。</p>
                  {/* Dismissible: click × to collapse binding and return to the audit form to keep viewing/filling, not forced */}
                  <button type="button" aria-label="关闭"
                          onClick={() => { setNeedPhone(false); setErr(''); }}
                          className="shrink-0 -mt-1 -mr-1 w-8 h-8 rounded-full text-lg leading-none text-muted hover:text-primary">
                    ×
                  </button>
                </div>
                <PhoneLogin compact onAuthenticated={onAuthed} />
                <button type="button" onClick={() => { setNeedPhone(false); setErr(''); }}
                        className="w-full rounded-full py-2 px-6 text-sm border border-soft text-secondary">
                  稍后再说
                </button>
              </div>
            ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-primary mb-1.5">
                  {t('diagnosis.form.urlLabel')}
                </label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={t('diagnosis.form.urlPlaceholder')}
                  className="w-full py-2.5 px-4 text-sm rounded-xl bg-surface border border-soft focus:outline-none text-primary"
                />
                <p className="text-xs text-muted mt-1.5 m-0">{t('diagnosis.form.urlHint')}</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-primary mb-1.5">
                  {t('diagnosis.form.brandLabel')}
                </label>
                <input
                  type="text"
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                  placeholder={t('diagnosis.form.brandPlaceholder')}
                  className="w-full py-2.5 px-4 text-sm rounded-xl bg-surface border border-soft focus:outline-none text-primary"
                />
              </div>
              <div>
                <TagField
                  label={t('diagnosis.form.industryLabel')}
                  placeholder={t('diagnosis.form.industryPlaceholder')}
                  hint={t('diagnosis.form.industryHint')}
                  values={industryTerms}
                  onChange={setIndustryTerms}
                  labelAction={(
                    <button type="button" onClick={extractTerms} disabled={suggesting}
                            className="shrink-0 text-xs font-medium px-2.5 py-1 rounded-full border border-soft"
                            style={{ color: 'var(--accent-primary)', opacity: suggesting ? 0.6 : 1 }}>
                      {suggesting ? '提取中…' : '✨ 帮我提取'}
                    </button>
                  )}
                />
                {suggestions.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs text-muted mb-1.5">点选加入:</p>
                    <div className="flex flex-wrap gap-2">
                      {suggestions.map((sg) => (
                        <button key={sg} type="button" onClick={() => addSuggestion(sg)}
                                className="px-2.5 py-1 rounded-full text-xs border border-soft hover:bg-surface-hover text-primary">
                          + {sg}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <TagField
                label={t('diagnosis.form.brandTermsLabel')}
                placeholder={t('diagnosis.form.brandTermsPlaceholder')}
                hint={t('diagnosis.form.brandTermsHint')}
                values={brandTerms}
                onChange={setBrandTerms}
              />
              <TagField
                label={t('diagnosis.form.competitorsLabel')}
                placeholder={t('diagnosis.form.competitorsPlaceholder')}
                hint={t('diagnosis.form.competitorsHint')}
                values={competitors}
                onChange={setCompetitors}
                normalize={(v) => v.replace(/^https?:\/\//, '').replace(/\/.*$/, '')}
              />

              {err && (
                <div className="rounded-md p-3 text-sm"
                     style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                              border: '1px solid rgba(239,68,68,0.3)' }}>
                  {err}
                </div>
              )}

              <button type="submit" disabled={busy}
                      className="btn-solid w-full sm:w-auto rounded-full py-2.5 px-8 text-sm font-semibold"
                      style={{ opacity: busy ? 0.6 : 1 }}>
                {busy ? t('diagnosis.form.submitting') : t('diagnosis.form.submit')}
              </button>
              <p className="text-xs text-muted">{t('diagnosis.form.estimate')}</p>
            </form>
            )}
          </section>

          {token && (
          <section id="history" className="card p-6 sm:p-8 scroll-mt-24">
            <h2 className="text-lg font-bold text-primary mb-4">{t('diagnosis.history.title')}</h2>
            {historyLoaded && history.length === 0 && (
              <p className="text-sm text-secondary">{t('diagnosis.history.empty')}</p>
            )}
            <div className="space-y-2">
              {history.map((h) => (
                <div key={h.id} role="button"
                     onClick={() => navigate(`/diagnosis/${h.id}`)}
                     className="flex items-center gap-3 p-3 rounded-xl border border-soft hover:-translate-y-0.5 transition-transform cursor-pointer">
                  <span className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                        style={{ background: STATUS_COLOR[h.status] || 'var(--text-muted)' }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-primary truncate">
                      {h.brand_name}{h.url ? ` · ${h.url}` : ` · ${t('diagnosis.form.noUrl')}`}
                    </div>
                    <div className="text-xs text-muted">
                      {fmtTimeIn(h.created_at, undefined, false)}
                      {' · '}
                      {t(`diagnosis.history.status.${h.status}`, { defaultValue: h.status })}
                      {h.status === 'ready' && h.total_score != null && ` · ${h.total_score}/100 ${h.grade || ''}`}
                    </div>
                  </div>
                  <span className="text-xs" style={{ color: 'var(--accent-primary)' }}>
                    {t('diagnosis.history.view')} →
                  </span>
                </div>
              ))}
            </div>
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between gap-3 mt-4">
                <span className="text-xs text-muted">
                  {t('diagnosis.history.pageInfo', {
                    page,
                    pages: Math.ceil(total / PAGE_SIZE),
                    total,
                  })}
                </span>
                <div className="flex items-center gap-2">
                  <button type="button"
                          disabled={page <= 1}
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          className="px-3 py-1.5 rounded-lg text-xs border border-soft text-primary disabled:opacity-40 disabled:cursor-not-allowed">
                    {t('diagnosis.history.prev')}
                  </button>
                  <button type="button"
                          disabled={page >= Math.ceil(total / PAGE_SIZE)}
                          onClick={() => setPage((p) => p + 1)}
                          className="px-3 py-1.5 rounded-lg text-xs border border-soft text-primary disabled:opacity-40 disabled:cursor-not-allowed">
                    {t('diagnosis.history.next')}
                  </button>
                </div>
              </div>
            )}
          </section>
          )}
        </div>
      </main>
    </div>
  );
}
