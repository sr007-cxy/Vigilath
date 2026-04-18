import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en';
import zh from './zh';

if (!i18n.isInitialized) {
  i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      zh: { translation: zh }
    },
    lng: localStorage.getItem('i18nextLng') || (navigator.language?.startsWith('zh') ? 'zh' : 'en'),
    fallbackLng: 'en',
    defaultNS: 'translation',
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
