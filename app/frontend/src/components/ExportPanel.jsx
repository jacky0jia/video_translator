import { useEffect, useState } from 'react';
import { useI18n } from '../contexts/I18nContext';

const SUBTITLE_PREVIEW_BYTES = 256 * 1024;

function outputUrl(path) {
  if (typeof path !== 'string' || !path.startsWith('/static/output/') || path.includes('\\')) return null;
  const url = new URL(path, window.location.origin);
  return url.origin === window.location.origin && url.pathname.startsWith('/static/output/') ? url.pathname : null;
}

function PreviewIcon() {
  return <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
}

function SubtitlePreview({ path, t }) {
  const [state, setState] = useState({ status: 'loading', text: '', truncated: false });

  useEffect(() => {
    const controller = new AbortController();
    const url = outputUrl(path);
    setState({ status: 'loading', text: '', truncated: false });
    if (!url) {
      setState({ status: 'error', text: '', truncated: false });
      return () => controller.abort();
    }
    const load = async () => {
      try {
        const response = await fetch(url, { headers: { Range: `bytes=0-${SUBTITLE_PREVIEW_BYTES}` }, signal: controller.signal, cache: 'no-store' });
        if (!response.ok || !response.body) throw new Error('Subtitle unavailable');
        const reader = response.body.getReader();
        const chunks = [];
        let size = 0;
        let truncated = false;
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const remaining = SUBTITLE_PREVIEW_BYTES - size;
            if (value.length > remaining) truncated = true;
            if (remaining > 0) {
              chunks.push(value.subarray(0, remaining));
              size += Math.min(value.length, remaining);
            }
            if (size >= SUBTITLE_PREVIEW_BYTES) {
              truncated = truncated || response.status === 206 || value.length > remaining;
              await reader.cancel();
              break;
            }
          }
        } finally {
          reader.releaseLock();
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
        const text = new TextDecoder('utf-8').decode(bytes);
        if (!controller.signal.aborted) setState({ status: 'ready', text, truncated });
      } catch (error) {
        if (!controller.signal.aborted) setState({ status: 'error', text: '', truncated: false });
      }
    };
    load();
    return () => controller.abort();
  }, [path]);

  if (state.status === 'loading') return <p role="status" className="text-sm text-slate-500">{t('previewLoading')}</p>;
  if (state.status === 'error') return <p role="alert" className="text-sm text-rose-600">{t('previewUnavailable')}</p>;
  return <>
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs text-slate-800 dark:bg-slate-950 dark:text-slate-100">{state.text || t('previewEmpty')}</pre>
    {state.truncated && <p className="mt-1 text-xs text-slate-500">{t('previewTruncated')}</p>}
  </>;
}

function ExportItem({ item, t }) {
  const [open, setOpen] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const url = outputUrl(item.path);

  return <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-2.5 dark:border-slate-700 dark:bg-slate-900/40">
    <div className="flex min-w-0 items-center gap-3">
      <span className="text-indigo-600 dark:text-indigo-300"><FileIcon type={item.type} /></span>
      <div className="min-w-0 flex-1">
        <strong className="block truncate text-sm font-medium">{item.label}</strong>
        <span className="text-xs text-slate-500 dark:text-slate-400">{item.meta}</span>
      </div>
      <button type="button" className="app-icon-button" aria-label={`${t('preview')} ${item.label}`} aria-expanded={open} onClick={() => { setOpen(value => !value); setMediaError(false); }}><PreviewIcon /></button>
      {url && <a href={url} download className="app-icon-button" aria-label={`${t('download')} ${item.label}`}><DownloadIcon /></a>}
    </div>
    {open && <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-700">
      {!url ? <p role="alert" className="text-sm text-rose-600">{t('previewUnavailable')}</p> :
        item.type === 'subtitle' ? <SubtitlePreview path={url} t={t} /> :
          mediaError ? <p role="alert" className="text-sm text-rose-600">{t('previewUnavailable')}</p> :
            item.type === 'audio' ? <audio controls preload="metadata" src={url} onError={() => setMediaError(true)} className="w-full" aria-label={`${t('preview')} ${item.label}`} /> :
              <video controls preload="metadata" src={url} onError={() => setMediaError(true)} className="max-h-80 w-full rounded-lg bg-black" aria-label={`${t('preview')} ${item.label}`} />}
    </div>}
  </div>;
}

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
        <div className="app-panel-heading"><span className="app-step">04</span><div><h2>{t('exportPanel')}</h2><p>{t('exportPanelHint')}</p></div></div>
        <span>{items.length} {t(items.length === 1 ? 'file' : 'files')}</span>
      </div>
      <div className="space-y-2 p-3">
        {items.length === 0 ? (
          <p className="px-2 py-5 text-center text-sm text-slate-500 dark:text-slate-400">{t('processedFilesHint')}</p>
        ) : items.map(item => (
          <ExportItem key={`${item.type}-${item.path}`} item={item} t={t} />
        ))}
      </div>
    </section>
  );
}
