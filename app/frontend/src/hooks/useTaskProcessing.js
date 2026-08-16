import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTaskSSE } from './useTaskSSE';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';

export const PROCESS_STAGES = ['transcribe', 'translate', 'dub', 'render'];

export function languageCode(value) {
  const lang = String(value || '').toLowerCase();
  if (lang.includes('chinese') || lang.startsWith('zh')) return 'zh';
  if (lang.includes('japanese') || lang === 'ja' || lang === 'jp') return 'ja';
  if (lang.includes('korean') || lang === 'ko') return 'ko';
  return 'en';
}

function voiceMatches(voice, target) {
  const actual = String(voice?.language || '').toLowerCase();
  const expected = languageCode(target);
  if (expected === 'zh') return actual === 'zh' || actual === 'cmn';
  if (expected === 'en') return actual.startsWith('en');
  return actual === expected || actual.startsWith(expected);
}

function stageId(status) {
  if (String(status).includes('transcrib')) return 'transcribe';
  if (String(status).includes('translat')) return 'translate';
  if (String(status).includes('dubb')) return 'dub';
  if (String(status).includes('burn') || String(status).includes('render')) return 'render';
  return '';
}

const STAGE_MESSAGES = {
  pipeline_transcribing: 'Transcribing',
  pipeline_translating: 'Translating',
  pipeline_translated: 'Translation complete',
  pipeline_dubbing: 'Creating dubbing',
  burning: 'Rendering subtitles',
};

export function useTaskProcessing({ task, onTaskRefresh, subtitleStyle, showSource, showTarget }) {
  const showToast = useToast();
  const { t } = useI18n();
  const [route, setRoute] = useState(task?.last_process_route || 'subtitles');
  const [format, setFormat] = useState(task?.last_subtitle_format || 'srt');
  const [burn, setBurn] = useState(false);
  const [subtitleContent, setSubtitleContent] = useState('translated');
  const [voices, setVoices] = useState([]);
  const [voice, setVoice] = useState('');
  const [speed, setSpeed] = useState(1);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentStage, setCurrentStage] = useState('');
  const [message, setMessage] = useState(() => t('readyToProcess'));
  const [status, setStatus] = useState('ready');
  const [lastFailedStage, setLastFailedStage] = useState('');
  const completionHandledRef = useRef(false);

  const targetLang = task?.target_lang || 'Chinese';
  const requiredStages = useMemo(() => route === 'dubbing'
    ? (burn ? PROCESS_STAGES : ['transcribe', 'translate', 'dub'])
    : burn ? ['transcribe', 'translate', 'render'] : ['transcribe', 'translate'], [route, burn]);
  const matchingVoices = useMemo(
    () => voices.filter(item => voiceMatches(item, targetLang)),
    [voices, targetLang],
  );

  useEffect(() => {
    fetch('/api/dubbing/voices')
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(data => setVoices(data.voices || []))
      .catch(() => setVoices([]));
  }, []);

  useEffect(() => {
    const preferred = matchingVoices.find(item => item.id === task?.dubbing_voice) || matchingVoices[0];
    if (languageCode(targetLang) === 'ko') setVoice(preferred?.id || 'ko-KR-SunHiNeural');
    else setVoice(preferred?.id || '');
  }, [matchingVoices, targetLang, task?.task_id, task?.dubbing_voice]);

  useEffect(() => {
    setRoute(task?.last_process_route || 'subtitles');
    setFormat(task?.last_subtitle_format || 'srt');
    setProgress(0);
    setCurrentStage('');
    setMessage(t('readyToProcess'));
    setStatus('ready');
    setRunning(false);
    setLastFailedStage(task?.failed_stage || task?.interrupted_status || '');
    completionHandledRef.current = false;
  }, [task?.task_id, t]);

  const refresh = useCallback(async () => {
    await onTaskRefresh?.(task.task_id);
  }, [onTaskRefresh, task?.task_id]);

  const createSubtitleFile = useCallback(async () => {
    const body = new URLSearchParams();
    body.append('json_path', task.translation_path || task.transcription_path);
    body.append('format', format);
    body.append('task_id', task.task_id);
    body.append('mode', route === 'dubbing' ? 'translated' : subtitleContent);
    body.append('download', 'false');
    body.append('process_route', route);
    body.append('burn_enabled', String(burn));
    body.append('source_color', subtitleStyle?.sourceColor || '#FFFFFF');
    body.append('target_color', subtitleStyle?.targetColor || '#FDE047');
    body.append('font_size', String(subtitleStyle?.fontSize || 18));
    body.append('offset_y', String(subtitleStyle?.offsetY || 0));
    body.append('bold', String(Boolean(subtitleStyle?.bold)));
    body.append('font_family', subtitleStyle?.fontFamily || 'Arial');
    const response = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Subtitle export failed');
    return data;
  }, [burn, format, route, subtitleContent, subtitleStyle, task]);

  useTaskSSE(running ? task?.task_id : null, event => {
    const nextProgress = Number(event.progress_percent);
    if (Number.isFinite(nextProgress)) setProgress(nextProgress);
    const eventStage = event.pipeline_stage || event.status;
    setCurrentStage(stageId(eventStage));
    if (event.message) setMessage(STAGE_MESSAGES[eventStage] || event.message);
    if (route === 'dubbing' && event.pipeline_status === 'completed' && !completionHandledRef.current) {
      completionHandledRef.current = true;
      setProgress(100);
      setStatus('completed');
      setCurrentStage('');
      setMessage(t('allOutputsReady'));
      setRunning(false);
      refresh()
        .catch(() => {})
        .finally(() => showToast('Processing completed', 'success'));
    }
    if (event.pipeline_status === 'failed' || event.status === 'failed' || event.status === 'translation_failed') {
      setRunning(false);
      setStatus('failed');
      setLastFailedStage(event.failed_stage || event.pipeline_stage || event.status);
      setMessage(event.message || 'Processing failed');
    }
    if (event.pipeline_status === 'cancelled' || event.status === 'cancelled') {
      setRunning(false);
      setStatus('cancelled');
      setMessage('Processing cancelled');
    }
  });

  const ensureTranslation = async (force = false) => {
    if (!force && task.translations?.[targetLang]) return;
    setCurrentStage('translate');
    setProgress(value => Math.max(value, 30));
    setMessage(`Translating into ${targetLang}`);
    const body = new URLSearchParams();
    body.append('json_path', task.transcription_path);
    body.append('target_lang', targetLang);
    body.append('task_id', task.task_id);
    const response = await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Translation failed');
  };

  const waitForBurn = async () => {
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 900));
      const response = await fetch(`/api/burn/${task.task_id}`);
      const data = await response.json();
      if (data.burn_status === 'completed') return;
      if (data.burn_status === 'failed') throw new Error(data.burn_error || 'Video rendering failed');
    }
  };

  const startSubtitles = async ({ forceTranslate = false } = {}) => {
    await ensureTranslation(forceTranslate);
    await refresh();
    setProgress(burn ? 70 : 85);
    setMessage('Creating subtitle file');
    await createSubtitleFile();
    if (burn) {
      setCurrentStage('render');
      setProgress(85);
      setMessage('Burning subtitles into video');
      const response = await fetch('/api/burn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: task.task_id,
          show_source: subtitleContent === 'bilingual' && showSource,
          show_target: showTarget,
          style: subtitleStyle,
          target_lang: targetLang,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Unable to start video rendering');
      await waitForBurn();
    }
  };

  const startDubbing = async ({ forceTranslate = false, forceDub = false } = {}) => {
    if (!voice) throw new Error(`${t('noVoiceAvailable')}: ${targetLang}`);
    const response = await fetch('/api/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: task.task_id,
        target_lang: targetLang,
        voice,
        speed,
        subtitle_format: format,
        burn_subtitles: burn,
        show_source: burn && subtitleContent === 'bilingual' && showSource,
        show_target: burn && showTarget,
        subtitle_style: subtitleStyle || {},
        force_translate: forceTranslate,
        force_dub: forceDub,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Unable to start dubbing');
    setMessage('Complete workflow started');
  };

  const run = async ({ forceTranslate = false, forceDub = false, resume = false } = {}) => {
    if (running || !task?.transcription_path) return;
    completionHandledRef.current = false;
    setRunning(true);
    setStatus('processing');
    setProgress(forceTranslate ? 30 : (forceDub || resume) ? 60 : 30);
    setCurrentStage(forceTranslate ? 'translate' : forceDub ? 'dub' : resume && route === 'dubbing' ? 'dub' : resume && burn ? 'render' : 'translate');
    try {
      if (route === 'dubbing') {
        await startDubbing({ forceTranslate, forceDub });
        return;
      }
      await startSubtitles({ forceTranslate });
      setProgress(100);
      setStatus('completed');
      setCurrentStage('');
      setMessage(t('allOutputsReady'));
      setRunning(false);
      await refresh();
      showToast('Processing completed', 'success');
    } catch (error) {
      setRunning(false);
      setStatus('failed');
      setLastFailedStage(currentStage || 'translate');
      setMessage(error.message);
      showToast(error.message, 'error');
    }
  };

  const retryFailedStage = () => {
    const failedStage = String(lastFailedStage || task.failed_stage || task.interrupted_status || task.status || '');
    if (failedStage.includes('translat')) return run({ forceTranslate: true, forceDub: route === 'dubbing', resume: true });
    if (failedStage.includes('dubb')) return run({ forceDub: true, resume: true });
    return run({ resume: true });
  };

  const cancel = async () => {
    if (route !== 'dubbing') return;
    const response = await fetch(`/api/pipeline/${task.task_id}/cancel`, { method: 'POST' });
    if (!response.ok) showToast('Unable to cancel processing', 'error');
  };

  return {
    burn, cancel, currentStage, format, matchingVoices, message, progress,
    requiredStages, retryFailedStage, route, run, running, setBurn, setFormat,
    setRoute, setSpeed, setSubtitleContent, setVoice, speed, status,
    subtitleContent, targetLang, voice,
  };
}
