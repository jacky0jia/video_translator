export function InterfaceSettingsSection({ lang, setLanguage, theme, setTheme, inputBg, inputBorder, textMain, t = key => key }) {
  return (
    <div className="mb-4 grid gap-4 rounded-xl border border-gray-200 p-4 sm:grid-cols-2 dark:border-slate-600">
      <label>
        <span className={`block text-sm font-medium mb-1 ${textMain}`}>{t('interfaceLanguage')}</span>
        <select value={lang} onChange={event => setLanguage(event.target.value)} className={`w-full p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}>
          <option value="en">English</option><option value="zh">简体中文</option>
        </select>
      </label>
      <label>
        <span className={`block text-sm font-medium mb-1 ${textMain}`}>{t('theme')}</span>
        <select value={theme} onChange={event => setTheme(event.target.value)} className={`w-full p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}>
          <option value="light">{t('light')}</option><option value="dark">{t('dark')}</option>
        </select>
      </label>
    </div>
  );
}

export function ServicesSection({ children }) {
  return (
    <details className="rounded-xl border border-gray-200 p-3 dark:border-slate-600">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800 dark:text-slate-100">Services &amp; diagnostics</summary>
      {children}
    </details>
  );
}

export function AboutSection({ edition = 'standard', t }) {
  return (
    <div className="mt-4 rounded-xl border border-gray-200 p-4 dark:border-slate-600">
      <h3 className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">{t('about')}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-slate-500 dark:text-slate-400">{t('product')}</dt><dd>Video Translator</dd>
        <dt className="text-slate-500 dark:text-slate-400">{t('edition')}</dt><dd>{edition}</dd>
      </dl>
    </div>
  );
}
