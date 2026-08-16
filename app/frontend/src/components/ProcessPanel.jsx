import { useI18n } from '../contexts/I18nContext';
import { PROCESS_STAGES, useTaskProcessing } from '../hooks/useTaskProcessing';

const FORMATS = ['srt', 'vtt', 'ass'];
const STAGE_LABELS = { transcribe: 'Transcribe', translate: 'Translate', dub: 'Dub', render: 'Render' };

function OutputOptions({ processing }) {
  const { t } = useI18n();
  const { burn, format, matchingVoices, route, running, setBurn, setSpeed, setSubtitleContent, setVoice, speed, subtitleContent, voice } = processing;
  if (route === 'subtitles') return <>
    <label className="block"><span className="app-field-label">{t('subtitleContent')}</span><select className="app-input w-full" value={subtitleContent} onChange={e => setSubtitleContent(e.target.value)} disabled={running}><option value="translated">{t('translationOnly')}</option><option value="bilingual">{t('sourceAndTranslation')}</option></select></label>
    <label className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700"><span><strong className="block text-sm font-medium">{t('burnIntoVideo')}</strong><span className="text-xs text-slate-500 dark:text-slate-400">{t('burnIntoVideoHint')}</span></span><input type="checkbox" className="app-switch" checked={burn} onChange={e => setBurn(e.target.checked)} disabled={running} /></label>
    <p className="app-note">{format === 'ass' ? t('assStyleNote') : `${format.toUpperCase()} ${t('timedTextStyleNote')}`}</p>
  </>;
  return <>
    <label><span className="app-field-label">{t('voice')}</span><select className="app-input w-full" value={voice} onChange={e => setVoice(e.target.value)} disabled={running}>{matchingVoices.map(item => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}</select></label>
    <label className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700"><span><strong className="block text-sm font-medium">{t('burnIntoVideo')}</strong><span className="text-xs text-slate-500 dark:text-slate-400">{t('burnIntoVideoHint')}</span></span><input type="checkbox" className="app-switch" checked={burn} onChange={e => setBurn(e.target.checked)} disabled={running} /></label>
    {burn && <label className="block"><span className="app-field-label">{t('subtitleContent')}</span><select className="app-input w-full" value={subtitleContent} onChange={e => setSubtitleContent(e.target.value)} disabled={running}><option value="translated">{t('translationOnly')}</option><option value="bilingual">{t('sourceAndTranslation')}</option></select></label>}
    <label><span className="mb-1 flex justify-between text-sm"><span>{t('speechSpeed')}</span><strong>{speed.toFixed(2)}x</strong></span><input className="w-full accent-indigo-600" type="range" min="0.8" max="1.2" step="0.05" value={speed} onChange={e => setSpeed(Number(e.target.value))} disabled={running} /></label>
  </>;
}

function ProcessingStatus({ processing }) {
  const { t } = useI18n();
  const { currentStage, message, progress, requiredStages, status } = processing;
  return <>
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-900/40">
      <div className="mb-2 flex items-start justify-between gap-3"><div className="min-w-0"><strong className="block truncate text-sm font-medium">{status === 'completed' ? t('statusCompleted') : message}</strong><span className="text-xs text-slate-500 dark:text-slate-400">{currentStage ? STAGE_LABELS[currentStage] : t('selectedWorkflowStatus')}</span></div><strong className="text-sm font-medium">{progress}%</strong></div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"><div className={`h-full rounded-full transition-all ${status === 'failed' ? 'bg-red-500' : status === 'completed' ? 'bg-emerald-500' : 'bg-indigo-600'}`} style={{ width: `${progress}%` }} /></div>
    </div>
    <div className="grid grid-cols-4 gap-1.5">{PROCESS_STAGES.map(value => { const required = requiredStages.includes(value); const active = currentStage === value; const index = requiredStages.indexOf(value); const activeIndex = requiredStages.indexOf(currentStage); const done = status === 'completed' || (activeIndex > index && index >= 0); const labelKey = value === 'transcribe' ? 'stageTranscribe' : value === 'translate' ? 'stageTranslate' : value === 'dub' ? 'stageDub' : 'stageRender'; return <div key={value} className={`app-stage ${!required ? 'app-stage-skipped' : active ? 'app-stage-active' : done ? 'app-stage-done' : ''}`}><span />{t(labelKey)}</div>; })}</div>
  </>;
}

export default function ProcessPanel(props) {
  const { t } = useI18n();
  const { task } = props;
  const processing = useTaskProcessing(props);
  const { burn, cancel, format, retryFailedStage, route, run, running, setFormat, setRoute, status, targetLang } = processing;
  if (!task) return <section className="app-panel p-6 text-center text-sm text-slate-500 dark:text-slate-400">{t('selectTaskProcess')}</section>;
  return <section className="app-panel overflow-hidden">
    <div className="app-panel-header"><h2>{t('processPanel')}</h2><span className={`app-status app-status-${status}`}>{status === 'processing' ? t('processing') : status === 'completed' ? t('statusCompleted') : status === 'failed' ? t('statusFailed') : t('readyToProcess')}</span></div>
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between rounded-xl bg-indigo-50 px-3 py-2.5 text-sm dark:bg-indigo-950/40"><span className="text-slate-500 dark:text-slate-400">{t('taskTargetLanguage')}</span><strong className="font-medium">{targetLang}</strong></div>
      <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Output type">{['subtitles', 'dubbing'].map(value => <label key={value} className={`app-choice ${route === value ? 'app-choice-active' : ''}`}><input type="radio" name="process-route" value={value} checked={route === value} onChange={() => setRoute(value)} disabled={running} /><span>{t(value === 'subtitles' ? 'subtitles' : 'dubbingLabel')}</span></label>)}</div>
      <fieldset><legend className="app-field-label">{t('subtitleFormat')}</legend><div className="flex flex-wrap gap-4">{FORMATS.map(value => <label key={value} className="app-check"><input type="radio" name="subtitle-format" checked={format === value} onChange={() => setFormat(value)} disabled={running} />{value.toUpperCase()}</label>)}</div></fieldset>
      <OutputOptions processing={processing} />
      <ProcessingStatus processing={processing} />
      <button type="button" className="app-primary-button" onClick={() => run()} disabled={running || !task.transcription_path}>{running ? t('processing') : route === 'dubbing' ? t('createDubbedVideo') : burn ? t('createHardSubtitleVideo') : t('createSubtitleFile')}</button>
      {!running && task.transcription_path && <div className="grid grid-cols-2 gap-2"><button type="button" className="app-secondary-button" onClick={() => run({ forceTranslate: true, forceDub: route === 'dubbing' })}>{t('retranslate')}</button>{(status === 'failed' || ['failed', 'translation_failed', 'cancelled'].includes(task.status)) ? <button type="button" className="app-secondary-button" onClick={retryFailedStage}>{t('retryFailedStage')}</button> : <span />}</div>}
      {running && route === 'dubbing' && <button type="button" className="app-secondary-button w-full" onClick={cancel}>{t('cancelProcessing')}</button>}
    </div>
  </section>;
}
