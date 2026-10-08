import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type AdminAccount } from '../../services/aiTelemetryApi';
import { authApi } from '../../services/authApi';
import { Pagination } from '../../components/Pagination';
import { PageHead } from '../../components/PageHead';
import { useOverlayDismiss } from '../../components/ui/useOverlayDismiss';
// Unified UI component layer; see docs/03-系统设计/前端UI规范.md for the conventions
import { Button, Input, Field, SkeletonRows, EmptyRow, UiSelect } from '../../components/ui';

// Client-side pagination: the backend returns everything at once ordered by id desc, and the frontend slices in memory.
// Current volume is tens to hundreds, so pure frontend pagination suffices; move to backend limit/offset past a thousand.

export function AdminAccounts() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [kw, setKw] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [topicFilter, setTopicFilter] = useState('');
  const [sort, setSort] = useState('newest');

  // ── New client modal state ──
  const [modalOpen, setModalOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    aiTelemetryApi.adminListAccounts(token)
      .then(setAccounts)
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  const filteredAccounts = useMemo(() => {
    const q = kw.trim().toLowerCase().replace(/^#/, '');
    const out = accounts.filter(account => {
      if (q) {
        const haystack = [
          String(account.id), account.email, account.name, account.brand_target,
        ].filter(Boolean).join(' ').toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (statusFilter === 'active' && !account.is_active) return false;
      if (statusFilter === 'inactive' && account.is_active) return false;
      if (kindFilter === 'customer' && account.is_admin) return false;
      if (kindFilter === 'admin' && !account.is_admin) return false;
      if (topicFilter === 'with' && account.topic_count === 0) return false;
      if (topicFilter === 'without' && account.topic_count > 0) return false;
      return true;
    });
    return [...out].sort((a, b) => {
      if (sort === 'oldest') return a.id - b.id;
      if (sort === 'name') {
        const an = a.brand_target || a.name || a.email;
        const bn = b.brand_target || b.name || b.email;
        return an.localeCompare(bn, 'zh-Hans-CN');
      }
      if (sort === 'topics') return (b.topic_count - a.topic_count) || (b.id - a.id);
      return b.id - a.id;
    });
  }, [accounts, kindFilter, kw, sort, statusFilter, topicFilter]);

  const filtering = !!kw.trim() || !!statusFilter || !!kindFilter || !!topicFilter || sort !== 'newest';
  const clearFilters = () => {
    setKw('');
    setStatusFilter('');
    setKindFilter('');
    setTopicFilter('');
    setSort('newest');
  };

  const totalPages = Math.max(1, Math.ceil(filteredAccounts.length / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  useEffect(() => { setPage(1); }, [kw, statusFilter, kindFilter, topicFilter, sort, pageSize]);
  const pagedAccounts = useMemo(
    () => filteredAccounts.slice((page - 1) * pageSize, page * pageSize),
    [filteredAccounts, page, pageSize],
  );

  // Lock scrolling while the modal is open + Esc closes
  useEffect(() => {
    if (!modalOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) closeModal();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modalOpen, submitting]);

  const openModal = () => {
    setEmail(''); setPassword(''); setName(''); setFormErr(null);
    setModalOpen(true);
  };
  const closeModal = () => {
    if (submitting) return;
    setModalOpen(false);
  };
  // Overlay close checks both mousedown and mouseup, so selecting text in an input and releasing outside the box doesn't close it by mistake
  const dismissModal = useOverlayDismiss(closeModal);

  const handleCreate = async () => {
    setFormErr(null);
    const e = email.trim().toLowerCase();
    const p = password;
    const n = name.trim();
    if (!e) { setFormErr(t('workbench.adminAccounts.createForm.emailRequired')); return; }
    if (p.length < 6) { setFormErr(t('workbench.adminAccounts.createForm.passwordTooShort')); return; }
    setSubmitting(true);
    try {
      const u = await authApi.register(e, p, n || undefined, token);
      // Registered successfully -> jump straight to this new client's "New topic" page
      setModalOpen(false);
      navigate(`/ops/accounts/${u.id}/topics?new=1`);
    } catch (ex: unknown) {
      setFormErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="wb-page">
      <PageHead titleKey="workbench.adminAccounts.title" titleFallback="画像" />
      <header className="wb-page-header">
        <div>
          <div className="wb-eyebrow">{t('workbench.adminAccounts.eyebrow')}</div>
          <h1 className="text-xl font-semibold text-primary">{t('workbench.adminAccounts.heading')}</h1>
          <p className="wb-subtitle">{t('workbench.adminAccounts.subtitle')}</p>
        </div>
        <button type="button" className="wb-button wb-button-primary" onClick={openModal}>
          <span aria-hidden>＋</span>{t('workbench.adminAccounts.newCustomer')}
        </button>
      </header>

      {err ? (
        <div className="text-xs" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</div>
      ) : (
        <div className="wb-panel overflow-hidden">
          <div className="wb-toolbar">
            <label className="wb-field flex-1 min-w-[15rem] max-w-[25rem]">
              <span className="wb-field-label">{t('workbench.adminAccounts.searchLabel')}</span>
              <span className="relative block">
                <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted"
                     fill="none" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.5" aria-hidden>
                  <circle cx="7" cy="7" r="4.25" />
                  <path d="m10.25 10.25 3 3" strokeLinecap="round" />
                </svg>
                <input type="search" value={kw} onChange={e => setKw(e.target.value)}
                       placeholder={t('workbench.adminAccounts.searchPlaceholder')}
                       className="wb-input w-full !pl-8" />
              </span>
            </label>
            <AccountFilter label={t('workbench.adminAccounts.filterStatus')} value={statusFilter}
              onChange={setStatusFilter} options={[
                ['', t('workbench.adminAccounts.filterStatusAll')],
                ['active', t('workbench.adminAccounts.active')],
                ['inactive', t('workbench.adminAccounts.inactive')],
              ]} />
            <AccountFilter label={t('workbench.adminAccounts.filterKind')} value={kindFilter}
              onChange={setKindFilter} options={[
                ['', t('workbench.adminAccounts.filterKindAll')],
                ['customer', t('workbench.adminAccounts.customer')],
                ['admin', t('workbench.adminAccounts.admin')],
              ]} />
            <AccountFilter label={t('workbench.adminAccounts.filterTopics')} value={topicFilter}
              onChange={setTopicFilter} options={[
                ['', t('workbench.adminAccounts.filterTopicsAll')],
                ['with', t('workbench.adminAccounts.withTopics')],
                ['without', t('workbench.adminAccounts.withoutTopics')],
              ]} />
            <AccountFilter label={t('workbench.adminAccounts.sortLabel')} value={sort}
              onChange={setSort} options={[
                ['newest', t('workbench.adminAccounts.sortNewest')],
                ['oldest', t('workbench.adminAccounts.sortOldest')],
                ['name', t('workbench.adminAccounts.sortName')],
                ['topics', t('workbench.adminAccounts.sortTopics')],
              ]} />
            {filtering && (
              <button type="button" className="wb-button wb-button-quiet" onClick={clearFilters}>
                {t('workbench.adminAccounts.clearFilters')}
              </button>
            )}
            <span className="wb-result-count">
              {t('workbench.adminAccounts.resultCount', { shown: filteredAccounts.length, total: accounts.length })}
            </span>
          </div>
          <div className="wb-table-scroll">
            <table className="wb-table wb-account-table">
              <thead>
                <tr>
                  <th className="text-left">{t('workbench.adminAccounts.colId')}</th>
                  <th className="text-left">{t('workbench.adminAccounts.colEmail')}</th>
                  <th className="text-left">{t('workbench.adminAccounts.colName')}</th>
                  <th className="text-left">{t('workbench.adminAccounts.colStatus')}</th>
                  <th className="text-left">{t('workbench.adminAccounts.colKind')}</th>
                  <th className="text-right">{t('workbench.adminAccounts.colTopicCount')}</th>
                  <th />
                </tr>
              </thead>
              {loading ? <SkeletonRows rows={8} cols={7} /> : accounts.length === 0 ? (
                <EmptyRow
                  colSpan={7}
                  title={t('workbench.adminAccounts.empty', { defaultValue: '还没有客户账号' })}
                  hint={t('workbench.adminAccounts.emptyHint', { defaultValue: '点右上角「注册新客户」开第一个账号。' })}
                  action={<Button size="sm" variant="solid" onClick={openModal}>
                    + {t('workbench.adminAccounts.newCustomer')}
                  </Button>}
                />
              ) : filteredAccounts.length === 0 ? (
                <EmptyRow colSpan={7}
                  title={t('workbench.adminAccounts.noMatch')}
                  hint={t('workbench.adminAccounts.noMatchHint')}
                  action={<Button size="sm" onClick={clearFilters}>{t('workbench.adminAccounts.clearFilters')}</Button>}
                />
              ) : (
              <tbody>
                {pagedAccounts.map(a => (
                  <tr key={a.id}>
                    <td className="text-muted tabular-nums">#{a.id}</td>
                    <td className="text-primary">{a.email}</td>
                    <td className="text-secondary">{a.brand_target || a.name || '—'}</td>
                    <td><span className="wb-account-state" data-muted={!a.is_active}>
                      {a.is_active ? t('workbench.adminAccounts.active') : t('workbench.adminAccounts.inactive')}
                    </span></td>
                    <td><span className="wb-account-state" data-strong={a.is_admin}>
                      {a.is_admin ? t('workbench.adminAccounts.admin') : t('workbench.adminAccounts.customer')}
                    </span></td>
                    <td className="text-right tabular-nums">{a.topic_count}</td>
                    <td>
                      <Link to={`/ops/accounts/${a.id}/topics`} className="text-accent hover:underline">
                        {t('workbench.adminAccounts.configureTopics')} →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
              )}
            </table>
          </div>
          <div className="px-3 py-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
            <Pagination page={page} pageSize={pageSize} total={filteredAccounts.length}
              onPageChange={setPage} onPageSizeChange={setPageSize} />
          </div>
        </div>
      )}

      {/* ── Register new client modal ── */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
             style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)' }}
             {...dismissModal}>
          <div className="w-full max-w-md rounded-xl p-5 shadow-2xl"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3 mb-4">
              <h2 className="text-base font-semibold text-primary">
                {t('workbench.adminAccounts.createForm.title')}
              </h2>
              <button type="button" onClick={closeModal} disabled={submitting}
                      className="text-muted hover:text-primary text-lg leading-none"
                      aria-label="close">×</button>
            </div>
            <div className="space-y-3">
              <Field label={t('workbench.adminAccounts.createForm.emailLabel')} required>
                <Input type="email" autoFocus autoComplete="off" size="sm" value={email}
                       onChange={setEmail}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       invalid={!!formErr}
                       placeholder={t('workbench.adminAccounts.createForm.emailPlaceholder')} />
              </Field>
              <Field label={t('workbench.adminAccounts.createForm.passwordLabel')} required>
                <Input type="text" autoComplete="off" size="sm" value={password}
                       onChange={setPassword}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       placeholder={t('workbench.adminAccounts.createForm.passwordPlaceholder')} />
              </Field>
              <Field label={t('workbench.adminAccounts.createForm.nameLabel')}>
                <Input type="text" autoComplete="off" size="sm" value={name}
                       onChange={setName}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       placeholder={t('workbench.adminAccounts.createForm.namePlaceholder')} />
              </Field>
            </div>
            {formErr && (
              <div className="mt-3 text-xs" role="alert" style={{ color: 'var(--danger-fg)' }}>{formErr}</div>
            )}
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button size="sm" disabled={submitting} onClick={closeModal}>
                {t('workbench.adminAccounts.createForm.cancel')}
              </Button>
              <Button size="sm" variant="solid" loading={submitting} onClick={handleCreate}>
                {submitting
                  ? t('workbench.adminAccounts.createForm.submitting')
                  : t('workbench.adminAccounts.createForm.submit')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AccountFilter({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
}) {
  return (
    <label className="wb-field">
      <span className="wb-field-label">{label}</span>
      <UiSelect value={value} onChange={onChange} className="wb-select w-[8.5rem]"
        options={options.map(([optionValue, optionLabel]) => ({
          value: optionValue,
          label: optionLabel,
        }))} />
    </label>
  );
}
