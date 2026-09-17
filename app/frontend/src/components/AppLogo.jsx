import { useI18n } from '../contexts/I18nContext';

export default function AppLogo({ compact = false }) {
  const { t } = useI18n();
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <svg className="h-10 w-10 shrink-0 drop-shadow-lg" viewBox="0 0 64 64" role="img" aria-label="Video Translator logo">
        <defs>
          <linearGradient id="app-logo-gradient" x1="8" y1="6" x2="57" y2="59" gradientUnits="userSpaceOnUse">
            <stop stopColor="#596FF2" />
            <stop offset="1" stopColor="#8A5CF5" />
          </linearGradient>
        </defs>
        <rect x="4" y="4" width="56" height="56" rx="17" fill="url(#app-logo-gradient)" />
        <path d="M23 18.5c0-2 2.2-3.2 3.9-2.1l13.2 8.2c1.6 1 1.6 3.3 0 4.3l-13.2 8.2c-1.7 1.1-3.9-.1-3.9-2.1V18.5Z" fill="#fff" />
        <path d="M17 43h30M22 49h20" fill="none" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
        <path d="M12 22v-4a6 6 0 0 1 6-6h4M52 22v-4a6 6 0 0 0-6-6h-4" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" opacity=".72" />
      </svg>
      {!compact && <span className="truncate text-base font-semibold tracking-tight">{t('appName')}</span>}
    </div>
  );
}
