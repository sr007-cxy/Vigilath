import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useContactModal } from './ContactModalContext';

const sanitizeInput = (input: string): string =>
  input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

export function ContactModal() {
  const { t } = useTranslation();
  const { isOpen, closeContact } = useContactModal();
  const [formData, setFormData] = useState({ name: '', email: '', website: '', message: '' });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  if (!isOpen) return null;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) { setError(t('contact.form.errors.name')); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) { setError(t('contact.form.errors.email')); return; }
    if (!formData.message.trim()) { setError(t('contact.form.errors.message')); return; }

    setIsLoading(true);
    setError('');
    setSuccess('');
    try {
      const res = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: sanitizeInput(formData.name),
          email: sanitizeInput(formData.email),
          website: sanitizeInput(formData.website),
          message: sanitizeInput(formData.message),
        }),
      });
      if (!res.ok) throw new Error();
      setSuccess(t('contact.form.success'));
      setFormData({ name: '', email: '', website: '', message: '' });
    } catch {
      setError(t('contact.form.error'));
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    closeContact();
    setError('');
    setSuccess('');
  };

  const inputClass =
    'w-full px-4 py-2.5 rounded-lg text-sm bg-card text-primary placeholder-muted border border-border focus:outline-none focus:border-accent-primary transition-colors';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />

      {/* Modal */}
      <div
        className="relative w-full max-w-md rounded-2xl p-6 shadow-xl animate-fade-in overflow-y-auto max-h-[90vh]"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        {/* Close button */}
        <button
          onClick={handleClose}
          className="absolute top-4 right-4 text-secondary hover:text-primary transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <h2 className="text-xl font-bold text-primary mb-1">{t('contact.title')}</h2>
        <p className="text-sm text-secondary mb-5">{t('contact.subtitle')}</p>

        {success && (
          <div className="bg-green-900/50 border border-green-700 text-green-300 px-3 py-2 rounded-lg text-sm mb-4">
            {success}
          </div>
        )}
        {error && (
          <div className="bg-red-900/50 border border-red-700 text-red-300 px-3 py-2 rounded-lg text-sm mb-4">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-secondary mb-1">{t('contact.form.name')}</label>
            <input
              type="text" name="name" value={formData.name} onChange={handleChange}
              placeholder={t('contact.form.placeholders.name')} className={inputClass} disabled={isLoading}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-secondary mb-1">{t('contact.form.email')}</label>
            <input
              type="email" name="email" value={formData.email} onChange={handleChange}
              placeholder={t('contact.form.placeholders.email')} className={inputClass} disabled={isLoading}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-secondary mb-1">{t('contact.form.website')}</label>
            <input
              type="text" name="website" value={formData.website} onChange={handleChange}
              placeholder={t('contact.form.placeholders.website')} className={inputClass} disabled={isLoading}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-secondary mb-1">{t('contact.form.message')}</label>
            <textarea
              name="message" value={formData.message} onChange={handleChange}
              placeholder={t('contact.form.placeholders.message')} rows={3}
              className={`${inputClass} resize-none`} disabled={isLoading}
            />
          </div>
          <button
            type="submit" disabled={isLoading}
            className="w-full bg-accent-primary hover:bg-accent-primary/80 text-white font-semibold py-2.5 rounded-lg transition-colors text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? t('contact.form.sending') : t('contact.form.submit')}
          </button>
        </form>

        <div className="mt-4 pt-4 text-center text-xs text-secondary" style={{ borderTop: '1px solid var(--border-color)' }}>
          Email: <a href="mailto:support@zen7.com" className="text-accent-primary hover:underline">support@zen7.com</a>
        </div>
      </div>
    </div>
  );
}
