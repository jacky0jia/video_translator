import { useI18n } from '../contexts/I18nContext';

export default function AppLogo({ compact = false }) {
  const { t } = useI18n();
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <img
        className="h-10 w-10 shrink-0 drop-shadow-lg"
        src="/subtitle-companion.svg"
        alt="Video Translator logo"
        width="40"
        height="40"
      />
      {!compact && <span className="truncate text-base font-semibold tracking-tight">{t('appName')}</span>}
    </div>
  );
}
