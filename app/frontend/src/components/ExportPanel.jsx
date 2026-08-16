import { useI18n } from '../contexts/I18nContext';

function DownloadIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14" />
    </svg>
  );
}

function FileIcon({ type }) {
  const path = type === 'audio'
    ? 'M9 18V5l10-2v13M9 9l10-2M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm10-2a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'
    : type === 'video'
      ? 'M4 6h11v12H4zM15 10l5-3v10l-5-3'
      : 'M6 3h8l4 4v14H6zM14 3v5h5M9 13h6M9 17h6';
  return <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d={path} /></svg>;
}

export default function ExportPanel({ task }) {
  const { t } = useI18n();
  const format = (task?.last_subtitle_format || 'srt').toLowerCase();
  const subtitlePath = task?.subtitle_outputs?.[format];
  const route = task?.last_process_route || (task?.dubbing_video_path ? 'dubbing' : 'subtitles');
  const items = [];

  if (subtitlePath) {
    items.push({ type: 'subtitle', label: task?.target_lang ? `${task.target_lang} ${t('subtitles')}` : t('targetSubtitles'), meta: format.toUpperCase(), path: subtitlePath });
  }
  if (route === 'dubbing') {
    if (task?.dubbing_audio_path) items.push({ type: 'audio', label: task?.target_lang ? `${task.target_lang} ${t('dubbingLabel')}` : t('targetDubbing'), meta: 'WAV', path: task.dubbing_audio_path });
    if (task?.dubbing_video_path) items.push({ type: 'video', label: t('dubbedVideo'), meta: 'MP4', path: task.dubbing_video_path });
  } else if (task?.last_burn_enabled && task?.burn_path) {
    items.push({ type: 'video', label: t('hardSubtitleVideo'), meta: 'MP4', path: task.burn_path });
  }

  return (
    <section className="app-panel overflow-hidden">
      <div className="app-panel-header">
        <h2>{t('exportPanel')}</h2>
        <span>{items.length} {t(items.length === 1 ? 'file' : 'files')}</span>
      </div>
      <div className="space-y-2 p-3">
        {items.length === 0 ? (
          <p className="px-2 py-5 text-center text-sm text-slate-500 dark:text-slate-400">{t('processedFilesHint')}</p>
        ) : items.map(item => (
          <div key={`${item.type}-${item.path}`} className="flex min-w-0 items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-2.5 dark:border-slate-700 dark:bg-slate-900/40">
            <span className="text-indigo-600 dark:text-indigo-300"><FileIcon type={item.type} /></span>
            <div className="min-w-0 flex-1">
              <strong className="block truncate text-sm font-medium">{item.label}</strong>
              <span className="text-xs text-slate-500 dark:text-slate-400">{item.meta}</span>
            </div>
            <a href={item.path} download className="app-icon-button" aria-label={`Download ${item.label}`}><DownloadIcon /></a>
          </div>
        ))}
      </div>
    </section>
  );
}
