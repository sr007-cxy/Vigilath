import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface LanguageSwitcherProps {
  className?: string;
}

export function LanguageSwitcher({ className = '' }: LanguageSwitcherProps) {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  const languages = [
    { code: 'en', name: 'English' },
    { code: 'zh', name: '中文' },
    { code: 'ja', name: '日本語' },
    { code: 'ko', name: '한국어' },
    { code: 'de', name: 'Deutsch' },
    { code: 'fr', name: 'Français' },
    { code: 'es', name: 'Español' },
  ];

  const handleLanguageChange = (lang: string) => {
    i18n.changeLanguage(lang);
    setIsOpen(false);
  };

  const getLanguageName = (code: string) => {
    return languages.find(lang => lang.code === code)?.name || code;
  };

  return (
    <div className={`relative ${className}`}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 16px',
          backgroundColor: 'rgba(255, 255, 255, 0.9)',
          border: '1px solid #e2e8f0',
          borderRadius: '9999px',
          color: '#1e293b',
          fontSize: '0.875rem',
          fontWeight: '500',
          cursor: 'pointer',
          transition: 'all 0.3s',
          boxShadow: '0 2px 8px rgba(59, 130, 246, 0.1)',
        }}
        onMouseOver={(e) => {
          e.currentTarget.style.backgroundColor = 'white';
          e.currentTarget.style.borderColor = '#3b82f6';
          e.currentTarget.style.transform = 'translateY(-1px)';
        }}
        onMouseOut={(e) => {
          e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
          e.currentTarget.style.borderColor = '#e2e8f0';
          e.currentTarget.style.transform = 'translateY(0)';
        }}
      >
        {getLanguageName(i18n.language)}
        <svg
          xmlns="http://www.w3.org/2000/svg"
          style={{ height: '16px', width: '16px' }}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {isOpen && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '8px',
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '12px',
            boxShadow: '0 4px 16px rgba(59, 130, 246, 0.15)',
            zIndex: 1000,
            minWidth: '120px',
          }}
        >
          {languages.map((lang) => (
            <button
              key={lang.code}
              onClick={() => handleLanguageChange(lang.code)}
              style={{
                display: 'block',
                width: '100%',
                padding: '10px 16px',
                textAlign: 'left' as const,
                backgroundColor: lang.code === i18n.language ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                color: lang.code === i18n.language ? '#3b82f6' : '#1e293b',
                border: 'none',
                borderRadius: lang.code === i18n.language ? '8px' : '0',
                fontSize: '0.875rem',
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'all 0.2s',
              }}
              onMouseOver={(e) => {
                if (lang.code !== i18n.language) {
                  e.currentTarget.style.backgroundColor = 'rgba(59, 130, 246, 0.05)';
                }
              }}
              onMouseOut={(e) => {
                if (lang.code !== i18n.language) {
                  e.currentTarget.style.backgroundColor = 'transparent';
                }
              }}
            >
              {lang.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
