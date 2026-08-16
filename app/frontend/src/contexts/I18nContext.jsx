import { createContext, useContext, useState, useCallback } from 'react';
import { translations } from '../i18n/translations';

const I18nContext = createContext(null);

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(() => {
    return localStorage.getItem('app_lang') || 'en';
  });

  const t = useCallback(
    (key) => translations[lang]?.[key] ?? key,
    [lang]
  );

  const setLanguage = useCallback((next) => {
    setLang(next);
    localStorage.setItem('app_lang', next);
  }, []);

  return (
    <I18nContext.Provider value={{ lang, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used inside I18nProvider');
  return ctx;
}
