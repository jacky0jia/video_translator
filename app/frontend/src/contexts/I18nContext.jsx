import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { translations } from '../i18n/translations';

const I18nContext = createContext(null);
const validLanguage = value => value === 'en' || value === 'zh';
const cacheLanguage = value => {
  try { localStorage.setItem('app_lang', value); } catch { /* Browser storage can be disabled. */ }
};

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(() => {
    try {
      const cached = localStorage.getItem('app_lang');
      return validLanguage(cached) ? cached : 'en';
    } catch { return 'en'; }
  });
  const userChanged = useRef(false);

  useEffect(() => {
    let active = true;
    fetch('/api/config').then(response => response.ok ? response.json() : null).then(data => {
      const saved = data?.config?.UI_LANGUAGE;
      // Older configurations retain their browser preference until Settings is saved.
      if (active && !userChanged.current && data?.is_local && validLanguage(saved)) {
        setLang(saved);
        cacheLanguage(saved);
      }
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  const t = useCallback(
    (key) => translations[lang]?.[key] ?? key,
    [lang]
  );

  const setLanguage = useCallback((next) => {
    if (!validLanguage(next)) return;
    userChanged.current = true;
    setLang(next);
    cacheLanguage(next);
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
