import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type AdminAccount } from '../../services/aiTelemetryApi';
import { authApi } from '../../services/authApi';

export function AdminAccounts() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // ── 新建客户表单 state ──
  const [formOpen, setFormOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    aiTelemetryApi.adminListAccounts(token)
      .then(setAccounts)
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  const resetForm = () => {
    setEmail(''); setPassword(''); setName('');
    setFormErr(null);
  };

  const handleCreate = async () => {
    setFormErr(null);
    const e = email.trim().toLowerCase();
    const p = password;
    const n = name.trim();
    if (!e) { setFormErr(t('workbench.adminAccounts.createForm.emailRequired')); return; }
    if (p.length < 6) { setFormErr(t('workbench.adminAccounts.createForm.passwordTooShort')); return; }
    setSubmitting(true);
    try {
      const u = await authApi.register(e, p, n || undefined);
      setOkMsg(t('workbench.adminAccounts.createForm.successHint', { id: u.id }));
      resetForm();
      setFormOpen(false);
      reload();
      // 3s 后清成功提示
      setTimeout(() => setOkMsg(null), 4000);
    } catch (ex: unknown) {
      setFormErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div>
          <h1 className="text-xl font-semibold text-primary">
            {t('workbench.adminAccounts.heading')}
          </h1>
          <p className="text-xs text-muted mt-1">
            {t('workbench.adminAccounts.subtitle')}
          </p>
        </div>
        {!formOpen && (
          <button type="button"
                  onClick={() => { setFormOpen(true); setOkMsg(null); }}
                  className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap"
                  style={{ background: 'var(--accent-primary)' }}>
            + {t('workbench.adminAccounts.newCustomer')}
          </button>
        )}
      </div>

      {okMsg && (
        <div className="mb-3 text-xs px-3 py-2 rounded-md"
             style={{ background: 'rgba(16,185,129,0.10)', color: '#10b981', border: '1px solid rgba(16,185,129,0.30)' }}>
          {okMsg}
        </div>
      )}

      {formOpen && (
        <div className="mb-4 rounded-lg p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <h2 className="text-sm font-semibold text-primary mb-3">
            {t('workbench.adminAccounts.createForm.title')}
          </h2>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="text-xs text-secondary">
              <span className="block mb-1">{t('workbench.adminAccounts.createForm.emailLabel')} *</span>
              <input type="email" autoComplete="off" value={email}
                     onChange={e => setEmail(e.target.value)}
                     placeholder={t('workbench.adminAccounts.createForm.emailPlaceholder')}
                     className="w-full px-2 py-1.5 rounded-md text-xs text-primary"
                     style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }} />
            </label>
            <label className="text-xs text-secondary">
              <span className="block mb-1">{t('workbench.adminAccounts.createForm.passwordLabel')} *</span>
              <input type="text" autoComplete="off" value={password}
                     onChange={e => setPassword(e.target.value)}
                     placeholder={t('workbench.adminAccounts.createForm.passwordPlaceholder')}
                     className="w-full px-2 py-1.5 rounded-md text-xs text-primary"
                     style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }} />
            </label>
            <label className="text-xs text-secondary">
              <span className="block mb-1">{t('workbench.adminAccounts.createForm.nameLabel')}</span>
              <input type="text" autoComplete="off" value={name}
                     onChange={e => setName(e.target.value)}
                     placeholder={t('workbench.adminAccounts.createForm.namePlaceholder')}
                     className="w-full px-2 py-1.5 rounded-md text-xs text-primary"
                     style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }} />
            </label>
          </div>
          {formErr && (
            <div className="mt-3 text-xs text-red-500">{formErr}</div>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button type="button" disabled={submitting} onClick={handleCreate}
                    className="text-xs px-3 py-1.5 rounded-md text-white"
                    style={{ background: 'var(--accent-primary)', opacity: submitting ? 0.5 : 1 }}>
              {submitting
                ? t('workbench.adminAccounts.createForm.submitting')
                : t('workbench.adminAccounts.createForm.submit')}
            </button>
            <button type="button" disabled={submitting}
                    onClick={() => { setFormOpen(false); resetForm(); }}
                    className="text-xs px-3 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
              {t('workbench.adminAccounts.createForm.cancel')}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-xs text-muted text-center py-10">
          {t('common.loading')}
        </div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : (
        <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted">
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colId')}</th>
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colEmail')}</th>
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colName')}</th>
                <th className="text-right px-3 py-2">{t('workbench.adminAccounts.colTopicCount')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {accounts.map(a => (
                <tr key={a.id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="px-3 py-2 text-muted tabular-nums">{a.id}</td>
                  <td className="px-3 py-2 text-primary">
                    {a.email}
                    {a.is_admin && (
                      <span className="ml-1 text-[10px] text-amber-500">
                        {t('workbench.adminAccounts.adminBadge')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-secondary">{a.name || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.topic_count}</td>
                  <td className="px-3 py-2">
                    <Link to={`/workbench/accounts/${a.id}/topics`} className="text-accent hover:underline">
                      {t('workbench.adminAccounts.configureTopics')} →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
