import { useState } from 'react';
import { useI18n } from '../contexts/I18nContext';

export const DEFAULT_VIDEO_OUTPUT = { policy: 'balanced', codec: 'hevc', resolution: '1440', bitrate_mbps: 10 };
const POLICIES = ['balanced', 'resolution', 'codec', 'custom'];
const codecName = value => ({ h264: 'H.264', hevc: 'HEVC', av1: 'AV1' }[value] || String(value || '—').toUpperCase());
export function frameRate(value) {
  const [numerator, denominator = '1'] = String(value || '').split('/').map(Number);
  const fps = numerator / Number(denominator);
  return Number.isFinite(fps) && fps > 0 ? Number(fps.toFixed(3)) : '—';
}

export default function VideoOutputOptions({ processing }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const { videoOutput, setVideoOutput, videoPlan, videoPlanLoading, videoPlanError, burn, endNoteEnabled, running } = processing;
  const source = videoPlan?.source;
  const plan = videoPlan?.plan;
  const copy = !burn && !endNoteEnabled;
  const update = (key, value) => setVideoOutput(current => ({ ...current, [key]: value }));
  const bitrate = value => value ? `${Number((value / 1e6).toFixed(2))} Mbps` : '—';
  const duration = source?.duration > 0 ? `${Math.floor(source.duration / 60)}:${String(Math.floor(source.duration % 60)).padStart(2, '0')}` : '—';
  return <details className="group rounded-xl border border-slate-200 dark:border-slate-700" open={open} onToggle={event => setOpen(event.currentTarget.open)} data-video-output>
    <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 [&::-webkit-details-marker]:hidden">
      <span className="min-w-0"><strong className="block text-sm font-medium">{t('outputVideo')}</strong><span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{copy ? t('videoStreamCopy') : t(`videoPolicy_${videoOutput.policy}`)}{plan && !videoPlanLoading && ` · ${codecName(plan.codec)} · ${plan.height}p`}</span></span>
      <span aria-hidden="true" className="text-slate-500 transition-transform group-open:rotate-180">⌄</span>
    </summary>
    <div className="space-y-3 border-t border-slate-200 p-3 dark:border-slate-700">
      {source && <div className="text-xs text-slate-500 dark:text-slate-400" data-source-profile><p>{t('sourceVideo')} · {codecName(source.codec)} · {source.width}×{source.height} · {frameRate(source.frame_rate)} fps</p><p>{duration} · {source.bitrate_source === 'container_estimate' ? t('estimated') : ''} {bitrate(source.bitrate)}</p></div>}
      <label className="block"><span className="app-field-label">{t('videoPolicy')}</span><select className="app-input w-full" value={videoOutput.policy} onChange={event => update('policy', event.target.value)} disabled={running || copy}>{POLICIES.map(policy => <option key={policy} value={policy}>{t(`videoPolicy_${policy}`)}</option>)}</select></label>
      <p className="text-xs text-slate-500 dark:text-slate-400">{t(copy ? 'videoCopyHint' : `videoPolicyHint_${videoOutput.policy}`)}</p>
      {videoOutput.policy === 'custom' && !copy && <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-custom-video>
        <label><span className="app-field-label">{t('videoCodec')}</span><select className="app-input w-full" value={videoOutput.codec} onChange={event => update('codec', event.target.value)} disabled={running}>{['hevc', 'h264', 'av1'].map(codec => <option key={codec} value={codec}>{codecName(codec)}</option>)}</select></label>
        <label><span className="app-field-label">{t('videoResolution')}</span><select className="app-input w-full" value={videoOutput.resolution} onChange={event => update('resolution', event.target.value)} disabled={running}><option value="source">{t('sourceResolution')}</option><option value="1440">1440p ({t('maximum')})</option><option value="1080">1080p ({t('maximum')})</option></select></label>
        <label><span className="app-field-label">{t('videoBitrate')}</span><input className="app-input w-full" type="number" min="0.1" max="200" step="0.1" value={videoOutput.bitrate_mbps} onChange={event => update('bitrate_mbps', event.target.value === '' ? '' : Number(event.target.value))} disabled={running} /></label>
        <div><span className="app-field-label">{t('videoFrameRate')}</span><span className="text-xs text-slate-500 dark:text-slate-400">{t('followSource')} · {frameRate(source?.frame_rate)} fps</span></div>
      </div>}
      {videoPlanLoading ? <p role="status" className="text-xs text-slate-500 dark:text-slate-400">{t('checkingVideoPlan')}</p> : videoPlanError ? <p role="alert" className="app-note break-words">{t('videoPlanUnavailable')}: {videoPlanError}</p> : plan && <div className="space-y-2 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900/50" aria-live="polite" data-output-plan>
        <div className="flex flex-wrap justify-between gap-2 text-slate-500 dark:text-slate-400"><span>{t('estimatedOutput')}</span><span>{t(plan.encoder === 'copy' ? 'videoStreamCopy' : plan.software ? 'videoSoftwareEncoding' : 'videoHardwareEncoding')}</span></div>
        <p className="text-sm font-medium">{codecName(plan.codec)} · {plan.width}×{plan.height} · {frameRate(plan.frame_rate)} fps</p>
        <div className="flex flex-wrap justify-between gap-2 text-slate-500 dark:text-slate-400"><span>{bitrate(plan.bitrate)}</span><span>{t('estimated')} {plan.estimated_bytes ? `${(plan.estimated_bytes / 1e9).toFixed(2)} GB` : '—'}</span></div>
      </div>}
      {!videoPlanLoading && plan && <p className="app-note">{t(plan.encoder === 'copy' ? 'videoCopyHint' : plan.reason.startsWith('av1_') ? 'videoAv1FallbackHint' : plan.software ? 'videoSoftwareHint' : 'videoEstimateHint')}{plan.software && plan.reason.startsWith('av1_') ? ` ${t('videoSoftwareHint')}` : ''}</p>}
    </div>
  </details>;
}
