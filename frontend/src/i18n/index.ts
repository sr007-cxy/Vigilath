import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import en from './en';
import zh from './zh';

if (!i18n.isInitialized) {
  i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources: {
        en: { translation: en },
        zh: { translation: zh }
      },
      supportedLngs: ['en', 'zh'],
      load: 'languageOnly',
      fallbackLng: 'zh',
      defaultNS: 'translation',
      detection: {
        order: ['localStorage', 'navigator'],
        lookupLocalStorage: 'i18nextLng',
        caches: ['localStorage']
      },
      interpolation: {
        escapeValue: false
      }
    });
}

const loaded: Record<string, boolean> = {};

export async function loadNamespace(ns: 'result' | 'knowledge') {
  if (loaded[ns]) return;

  if (ns === 'result') {
    const [enM, zhM] = await Promise.all([
      import('./en-result'),
      import('./zh-result'),
    ]);
    i18n.addResourceBundle('en', 'translation', enM.default, true, true);
    i18n.addResourceBundle('zh', 'translation', zhM.default, true, true);
  } else if (ns === 'knowledge') {
    const [enM, zhM] = await Promise.all([
      import('./en-knowledge'),
      import('./zh-knowledge'),
    ]);
    i18n.addResourceBundle('en', 'translation', enM.default, true, true);
    i18n.addResourceBundle('zh', 'translation', zhM.default, true, true);
  }

  loaded[ns] = true;
}

export default i18n;
