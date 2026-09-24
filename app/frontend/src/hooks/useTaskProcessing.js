import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTaskSSE } from './useTaskSSE';
import {
  completedStageIds, deriveTaskProcessingState, retryPlanForFailure, stageId,
  stageIdForEvent, STAGE_MESSAGES,
  standaloneTranscriptionCompletion, subtitleVisibility,
} from './taskProcessingState';
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

export function useTaskProcessing({ task, onTaskRefresh, subtitleStyle, showSource, showTarget }) {
  const showToast = useToast();
  const { t } = useI18n();
  const [route, setRoute] = useState(task?.last_process_route || 'subtitles');
  const [format, setFormat] = useState(task?.last_subtitle_format || 'srt');
  const [burn, setBurn] = useState(false);
  const [subtitleContent, setSubtitleContent] = useState('translated');
  const [voices, setVoices] = useState([]);
  const [voice, setVoice] = useState('');
  const [ttsMode, setTtsMode] = useState('');
  const [onlineLanguages, setOnlineLanguages] = useState([]);
  const [voiceError, setVoiceError] = useState('');
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
  const completedStages = useMemo(
    () => completedStageIds(task, targetLang),
    [task, targetLang],
  );

  useEffect(() => {
    let active = true;
    let controller;
    const loadVoices = () => {
      controller?.abort();
      controller = new AbortController();
      setVoices([]);
      setVoice('');
      setTtsMode('');
      return fetch('/api/dubbing/voices', { cache: 'no-store', signal: controller.signal })
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(data => {
        if (!active) return;
        setVoices(data.voices || []);
        setTtsMode(data.tts_mode || '');
        setOnlineLanguages(data.online_languages || []);
        setVoiceError(data.error || data.edge_error || '');
      })
      .catch(error => {
        if (error?.name === 'AbortError') return;
        if (!active) return;
        setVoices([]);
        setVoiceError(t('voiceCatalogUnavailable'));
      });
    };
    loadVoices();
    window.addEventListener('settings-changed', loadVoices);
    return () => {
      active = false;
      controller?.abort();
      window.removeEventListener('settings-changed', loadVoices);
    };
  }, [t]);

  useEffect(() => {
    if (ttsMode === 'qwen') setSpeed(1);
  }, [ttsMode]);

  useEffect(() => {
    const preferred = matchingVoices.find(item => item.id === task?.dubbing_voice) || matchingVoices[0];
    if (languageCode(targetLang) === 'ko') setVoice(preferred?.id || (onlineLanguages.includes('ko') ? 'ko-KR-SunHiNeural' : ''));
    else setVoice(preferred?.id || '');
  }, [matchingVoices, onlineLanguages, targetLang, task?.task_id, task?.dubbing_voice]);

  useEffect(() => {
    const restored = deriveTaskProcessingState(task, t);
    setRoute(task?.last_process_route || 'subtitles');
    setFormat(task?.last_subtitle_format || 'srt');
    setProgress(restored.progress);
    setCurrentStage(restored.currentStage);
    setMessage(restored.message);
    setStatus(restored.status);
    setRunning(restored.running);
    setLastFailedStage(task?.failed_stage || task?.interrupted_status || '');
    completionHandledRef.current = false;
  }, [task?.task_id, t]);

  useEffect(() => {
    const completion = standaloneTranscriptionCompletion({ status: task?.status }, t);
    if (!completion) return;
    setProgress(completion.progress);
    setStatus(completion.status);
    setCurrentStage(completion.currentStage);
    setMessage(completion.message);
    setRunning(completion.running);
  }, [task?.status, t]);

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

  useTaskSSE(running && route === 'dubbing' ? task?.task_id : null, event => {
    const nextProgress = Number(event.progress_percent);
    if (Number.isFinite(nextProgress)) setProgress(nextProgress);
    const eventStage = event.pipeline_stage || event.status;
    setCurrentStage(value => stageIdForEvent(event, value));
    if (event.message) setMessage(STAGE_MESSAGES[eventStage] || event.message);
    const transcriptionCompletion = standaloneTranscriptionCompletion(event, t);
    if (transcriptionCompletion) {
      setProgress(transcriptionCompletion.progress);
      setStatus(transcriptionCompletion.status);
      setCurrentStage(transcriptionCompletion.currentStage);
      setMessage(transcriptionCompletion.message);
      setRunning(transcriptionCompletion.running);
      refresh().catch(() => {});
      return;
    }
    // Translation artifacts are ready before synthesis starts, even if dubbing
    // later fails. Refresh here as well as on terminal events.
    if (route === 'dubbing' && ['pipeline_translated', 'pipeline_dubbing'].includes(event.status)) {
      refresh().catch(() => {});
    }
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
      refresh().catch(() => {});
    }
    if (event.pipeline_status === 'cancelled' || event.status === 'cancelled') {
      setRunning(false);
      setStatus('cancelled');
      setMessage('Processing cancelled');
      refresh().catch(() => {});
    }
  });

  const ensureTranslation = async (force = false) => {
    const profile = task.translation_profiles?.[targetLang];
    if (!force && task.translations?.[targetLang] && profile !== 'dubbing') return;
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
      const visibility = subtitleVisibility(subtitleContent, showSource, showTarget);
      const response = await fetch('/api/burn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: task.task_id,
          ...visibility,
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
    const visibility = subtitleVisibility(subtitleContent, showSource, showTarget, burn);
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
        ...visibility,
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
    const failureMessage = message || task.message || task.dubbing_error || '';
    return run(retryPlanForFailure(failedStage, failureMessage, route));
  };

  const cancel = async () => {
    if (route !== 'dubbing') return;
    const response = await fetch(`/api/pipeline/${task.task_id}/cancel`, { method: 'POST' });
    if (!response.ok) showToast('Unable to cancel processing', 'error');
  };

  return {
    burn, cancel, completedStages, currentStage, format, matchingVoices, message, progress,
    requiredStages, retryFailedStage, route, run, running, setBurn, setFormat,
    setRoute, setSpeed, setSubtitleContent, setVoice, speed, status,
    subtitleContent, targetLang, ttsMode, onlineLanguages, voice, voiceError,
  };
}
