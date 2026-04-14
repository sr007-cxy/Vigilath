import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../components/LanguageSwitcher';

// Simple sanitization function to prevent XSS
const sanitizeInput = (input: string): string => {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

export function Contact() {
  const { t } = useTranslation();
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    website: '',
    message: ''
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const navigate = useNavigate();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const validateEmail = (email: string): boolean => {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
  };

  const validateUrl = (url: string): boolean => {
    try {
      new URL(url);
      return true;
    } catch {
      return false;
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate form
    if (!formData.name.trim()) {
      setError(t('contact.form.errors.name'));
      return;
    }

    if (!formData.email.trim() || !validateEmail(formData.email)) {
      setError(t('contact.form.errors.email'));
      return;
    }

    if (!formData.website.trim() || !validateUrl(formData.website)) {
      setError(t('contact.form.errors.website'));
      return;
    }

    if (!formData.message.trim()) {
      setError(t('contact.form.errors.message'));
      return;
    }

    setIsLoading(true);
    setError('');
    setSuccess('');

    try {
      // Sanitize input to prevent XSS
      const sanitizedData = {
        name: sanitizeInput(formData.name),
        email: sanitizeInput(formData.email),
        website: sanitizeInput(formData.website),
        message: sanitizeInput(formData.message)
      };

      // Send data to backend API
      const response = await fetch('/api/contact', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(sanitizedData)
      });

      if (!response.ok) {
        throw new Error('Failed to submit contact form');
      }

      setSuccess(t('contact.form.success'));
      setFormData({ name: '', email: '', website: '', message: '' });
    } catch {
      setError(t('contact.form.error'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid-background">
      {/* 背景发光效果 */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-3xl mx-auto animate-fade-in">
          <div className="bg-card border border-border rounded-2xl p-8">
            <div style={{ position: 'absolute', top: '24px', right: '24px', zIndex: 20 }}>
              <LanguageSwitcher />
            </div>
            <div className="text-center mb-8">
              <h1 className="text-3xl font-bold text-primary mb-4">{t('contact.title')}</h1>
              <p className="text-secondary">
                {t('contact.subtitle')}
              </p>
            </div>

            {success && (
              <div className="bg-green-900 border border-green-800 text-green-300 px-4 py-2 rounded-md mb-6">
                {success}
              </div>
            )}

            {error && (
              <div className="bg-red-900 border border-red-800 text-red-300 px-4 py-2 rounded-md mb-6">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-6">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-secondary mb-1">
                  {t('contact.form.name')}
                </label>
                <input
                  type="text"
                  id="name"
                  name="name"
                  value={formData.name}
                  onChange={handleChange}
                  placeholder={t('contact.form.placeholders.name')}
                  className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                  disabled={isLoading}
                />
              </div>

              <div>
                <label htmlFor="email" className="block text-sm font-medium text-secondary mb-1">
                  {t('contact.form.email')}
                </label>
                <input
                  type="email"
                  id="email"
                  name="email"
                  value={formData.email}
                  onChange={handleChange}
                  placeholder={t('contact.form.placeholders.email')}
                  className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                  disabled={isLoading}
                />
              </div>

              <div>
                <label htmlFor="website" className="block text-sm font-medium text-secondary mb-1">
                  {t('contact.form.website')}
                </label>
                <input
                  type="text"
                  id="website"
                  name="website"
                  value={formData.website}
                  onChange={handleChange}
                  placeholder={t('contact.form.placeholders.website')}
                  className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                  disabled={isLoading}
                />
              </div>

              <div>
                <label htmlFor="message" className="block text-sm font-medium text-secondary mb-1">
                  {t('contact.form.message')}
                </label>
                <textarea
                  id="message"
                  name="message"
                  value={formData.message}
                  onChange={handleChange}
                  placeholder={t('contact.form.placeholders.message')}
                  rows={5}
                  className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200 resize-none"
                  disabled={isLoading}
                />
              </div>

              <button
                type="submit"
                disabled={isLoading}
                className="w-full bg-accent-primary hover:bg-accent-primary/80 text-white font-semibold py-3 rounded-lg transition-colors duration-300 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoading ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    {t('contact.form.sending')}
                  </>
                ) : (
                  t('contact.form.submit')
                )}
              </button>
            </form>

            <div className="mt-8">
              <h2 className="text-xl font-semibold mb-4">{t('contact.otherWays')}</h2>
              <div className="space-y-2">
                <p className="flex items-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5 text-accent-primary">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                  </svg>
                  <span className="text-secondary">email@example.com</span>
                </p>
                <p className="flex items-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5 text-accent-primary">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 002.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 01-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 00-1.091-.852H4.5A2.25 2.25 0 002.25 4.5v2.25z" />
                  </svg>
                  <span className="text-secondary">+1 (123) 456-7890</span>
                </p>
              </div>
            </div>

            <div className="mt-8 text-center">
              <button
                onClick={() => navigate('/')}
                className="text-accent-primary hover:text-primary transition-colors duration-300"
              >
                {t('contact.backToHome')}
              </button>
            </div>

            {/* FAQ Section */}
            <div className="mt-12">
              <h2 className="text-2xl font-bold text-primary mb-6">{t('contact.faq.title')}</h2>
              <div className="space-y-4">
                <div className="border border-border rounded-lg p-4">
                  <h3 className="text-lg font-semibold text-primary mb-2">{t('contact.faq.questions.question1')}</h3>
                  <p className="text-secondary">{t('contact.faq.answers.answer1')}</p>
                </div>
                <div className="border border-border rounded-lg p-4">
                  <h3 className="text-lg font-semibold text-primary mb-2">{t('contact.faq.questions.question2')}</h3>
                  <p className="text-secondary">{t('contact.faq.answers.answer2')}</p>
                </div>
                <div className="border border-border rounded-lg p-4">
                  <h3 className="text-lg font-semibold text-primary mb-2">{t('contact.faq.questions.question3')}</h3>
                  <p className="text-secondary">{t('contact.faq.answers.answer3')}</p>
                </div>
                <div className="border border-border rounded-lg p-4">
                  <h3 className="text-lg font-semibold text-primary mb-2">{t('contact.faq.questions.question4')}</h3>
                  <p className="text-secondary">{t('contact.faq.answers.answer4')}</p>
                </div>
                <div className="border border-border rounded-lg p-4">
                  <h3 className="text-lg font-semibold text-primary mb-2">{t('contact.faq.questions.question5')}</h3>
                  <p className="text-secondary">{t('contact.faq.answers.answer5')}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
