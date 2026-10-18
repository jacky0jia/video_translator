import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTaskSSE } from './useTaskSSE';
import { useTaskScope, useTaskScopedState } from './useTaskScope';
import {
  completedStageIds, deriveTaskProcessingState, selectedWorkflowState, retryPlanForFailure, stageId,
  stageIdForEvent, localizedTaskMessage, isStandaloneTranscriptionEvent,
  standaloneTranscriptionCompletion, stageProgressForEvent, subtitleVisibility,
} from './taskProcessingState';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import { useWorkflowOptions } from './useWorkflowOptions';

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

export function useTaskProcessing({ task, onTaskRefresh, subtitleStyle: providedSubtitleStyle, showSource, showTarget }) {
  const notify = useToast();
  const scope = useTaskScope(task?.task_id);
  const showToast = useCallback((...args) => {
    if (scope.active) notify(...args);
  }, [scope, notify]);
  const { t } = useI18n();
  const taskStateIsStale = Number.isSafeInteger(scope.latestRevision)
    && (!Number.isSafeInteger(task?.state_revision) || task.state_revision < scope.latestRevision);
  const { options, setters, setOption } = useWorkflowOptions(scope, task, taskStateIsStale);
  const subtitleStyle = providedSubtitleStyle ?? options.subtitle_style;
  const { route, subtitle_format: format, burn_subtitles: burn, end_note_enabled: endNoteEnabled,
    output_options: videoOutput, subtitle_content: subtitleContent, voice, speed } = options;
  const { route: setRoute, subtitle_format: setFormat, burn_subtitles: setBurn, end_note_enabled: setEndNoteEnabled,
    output_options: setVideoOutput, subtitle_content: setSubtitleContent, voice: setVoice, speed: setSpeed } = setters;
  const [videoPlan, setVideoPlan] = useTaskScopedState(scope, null);
  const [videoPlanLoading, setVideoPlanLoading] = useTaskScopedState(scope, false);
  const [videoPlanError, setVideoPlanError] = useTaskScopedState(scope, '');
  // The voice catalog belongs to system settings, not the selected task. Its
  // existing request cleanup stays responsible for stale catalog responses.
  const [voices, setVoices] = useState([]);
  const [ttsMode, setTtsMode] = useState('');
  const [onlineLanguages, setOnlineLanguages] = useState([]);
  const [voiceError, setVoiceError] = useState('');
  const [running, setRunning] = useTaskScopedState(scope, false);
  const [startPending, setStartPending] = useTaskScopedState(scope, false);
  const [cancelPending, setCancelPending] = useTaskScopedState(scope, false);
  const [progress, setProgress] = useTaskScopedState(scope, 0);
  const [currentStage, setCurrentStage] = useTaskScopedState(scope, '');
  const [message, setMessage] = useTaskScopedState(scope, () => t('readyToProcess'));
  const [status, setStatus] = useTaskScopedState(scope, 'ready');
  const [lastFailedStage, setLastFailedStage] = useTaskScopedState(scope, '');
  const { completionHandledRef, startRequestRef } = scope;

  const targetLang = task?.target_lang || 'Chinese';
  const requiredStages = useMemo(() => route === 'dubbing'
    ? (burn || endNoteEnabled ? PROCESS_STAGES : ['transcribe', 'translate', 'dub'])
    : burn ? ['transcribe', 'translate', 'render'] : ['transcribe', 'translate'], [route, burn, endNoteEnabled]);
  const matchingVoices = useMemo(
    () => voices.filter(item => voiceMatches(item, targetLang)),
    [voices, targetLang],
  );
  const completedStages = useMemo(
    () => completedStageIds(task, targetLang),
    [task, targetLang],
  );

  useEffect(() => {
    setVideoPlan(null);
    setVideoPlanLoading(false);
    setVideoPlanError('');
  }, [task?.task_id]);

  useEffect(() => {
    if (!task?.task_id || route !== 'dubbing' || running) return;
    const controller = new AbortController();
    setVideoPlanLoading(true);
    setVideoPlanError('');
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/tasks/${task.task_id}/video-plan`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ output_options: videoOutput, burn_subtitles: burn, end_note_enabled: endNoteEnabled }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('videoInvalidOptions'));
        if (!controller.signal.aborted) setVideoPlan(data);
      } catch (error) {
        if (!controller.signal.aborted) { setVideoPlan(null); setVideoPlanError(error.message); }
      } finally {
        if (!controller.signal.aborted) setVideoPlanLoading(false);
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [task?.task_id, route, running, videoOutput, burn, endNoteEnabled, t]);

  useEffect(() => {
    let active = true;
    let controller;
    let requestId = 0;
    const loadVoices = () => {
      const currentRequestId = ++requestId;
      controller?.abort();
      controller = new AbortController();
      setVoices([]);
      setTtsMode('');
      return fetch('/api/dubbing/voices', { cache: 'no-store', signal: controller.signal })
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(data => {
        if (!active || currentRequestId !== requestId) return;
        setVoices(data.voices || []);
        setTtsMode(data.tts_mode || '');
        setOnlineLanguages(data.online_languages || []);
        setVoiceError(data.error || data.edge_error || '');
      })
      .catch(error => {
        if (error?.name === 'AbortError') return;
        if (!active || currentRequestId !== requestId) return;
        setVoices([]);
        setVoiceError(t('voiceCatalogUnavailable'));
      });
    };
    loadVoices();
    window.addEventListener('settings-changed', loadVoices);
    return () => {
      active = false;
      requestId += 1;
      controller?.abort();
      window.removeEventListener('settings-changed', loadVoices);
    };
  }, [t]);

  useEffect(() => {
    if (ttsMode === 'qwen') setOption('speed', 1, false);
  }, [ttsMode, scope, speed]);

  useEffect(() => {
    // A catalog reload must not erase a saved or manually selected voice while
    // loading. Once loaded, keep it if compatible with this task's language.
    if (!voices.length) return;
    const preferred = matchingVoices.find(item => item.id === voice) || matchingVoices[0];
    setOption('voice', preferred?.id || (languageCode(targetLang) === 'ko' && onlineLanguages.includes('ko') ? 'ko-KR-SunHiNeural' : ''), false);
  }, [matchingVoices, onlineLanguages, targetLang, scope, voice]);

  useEffect(() => {
    // Language changes rerun this effect too. Do not restore an old persisted
    // snapshot over an in-flight local start; a different task has its own lock.
    if (startRequestRef.current || taskStateIsStale) return;
    const restored = deriveTaskProcessingState(task, t);
    setProgress(restored.progress);
    setCurrentStage(restored.currentStage);
    setMessage(restored.message);
    setStatus(restored.status);
    setRunning(restored.running);
    setStartPending(false);
    setCancelPending(false);
    setLastFailedStage(task?.failed_stage || task?.interrupted_status || '');
    completionHandledRef.current = false;
  }, [task?.task_id, t]);

  useEffect(() => {
    if (taskStateIsStale) return;
    if (task?.pipeline_status !== 'awaiting_compression') return;
    const restored = deriveTaskProcessingState(task, t);
    setProgress(restored.progress);
    setCurrentStage(restored.currentStage);
    setMessage(restored.message);
    setStatus(restored.status);
    setRunning(false);
  }, [task?.pipeline_status, task?.compression_request, t]);

  useEffect(() => {
    if (taskStateIsStale) return;
    if (!task?.pipeline_cleanup_pending && task?.pipeline_status !== 'cancelling'
        && !(status === 'cancelling' && task?.pipeline_cleanup_pending === false)) return;
    const restored = deriveTaskProcessingState(task, t);
    setProgress(restored.progress); setCurrentStage(restored.currentStage);
    setMessage(restored.message); setStatus(restored.status); setRunning(restored.running);
  }, [task, t]);

  useEffect(() => {
    if (taskStateIsStale) return;
    const persistedRunning = task?.pipeline_status === 'processing'
      || task?.pipeline_cleanup_pending || task?.pipeline_status === 'cancelling'
      || task?.dubbing_status === 'processing'
      || task?.burn_status === 'processing';
    if (!persistedRunning || running || startRequestRef.current) return;
    const restored = deriveTaskProcessingState(task, t);
    setProgress(restored.progress);
    setCurrentStage(restored.currentStage);
    setMessage(restored.message);
    setStatus(restored.status);
    setRunning(true);
  }, [task, t]);

  useEffect(() => {
    if (taskStateIsStale) return;
    if (!isStandaloneTranscriptionEvent(task)) return;
    if (task?.pipeline_status === 'processing' || task?.dubbing_status === 'processing' || task?.burn_status === 'processing') return;
    const live = deriveTaskProcessingState(task, t);
    setProgress(live.progress);
    setCurrentStage(live.currentStage);
    setMessage(live.message);
    setStatus(live.status);
    setRunning(live.running);
  }, [task?.status, task?.progress_percent, task?.message, task?.gpu_stage, task?.pipeline_stage, task?.pipeline_status, task?.dubbing_status, task?.burn_status, t]);

  useEffect(() => {
    if (startRequestRef.current || taskStateIsStale) return;
    const completion = standaloneTranscriptionCompletion({ status: task?.status }, t);
    if (!completion || task?.pipeline_status === 'processing' || task?.pipeline_status === 'cancelling' || task?.pipeline_cleanup_pending) return;
    setProgress(completion.progress);
    setStatus(completion.status);
    setCurrentStage(completion.currentStage);
    setMessage(completion.message);
    setRunning(completion.running);
  }, [task?.status, t]);

  const refresh = useCallback(async () => {
    await onTaskRefresh?.(task.task_id);
  }, [onTaskRefresh, task?.task_id]);

  const pauseForReview = data => {
    scope.reviewId = data?.review?.review_id || data?.translation_reviews?.[targetLang]?.review_id || scope.reviewId;
    scope.reviewRevision = data?.state_revision ?? scope.reviewRevision;
    setRunning(false); setStatus('awaiting_review'); setCurrentStage('');
    setProgress(0); setMessage(t('translationReviewRequired'));
  };

  useEffect(() => {
    if (taskStateIsStale || startRequestRef.current) return;
    if (task?.pipeline_status === 'awaiting_review' || task?.status === 'awaiting_review') pauseForReview(task);
    if (task?.pipeline_status === 'awaiting_dubbing_edit' || task?.status === 'awaiting_dubbing_edit') {
      const restored = deriveTaskProcessingState(task, t);
      setRunning(false); setStatus(restored.status); setCurrentStage(restored.currentStage);
      setProgress(restored.progress); setMessage(restored.message);
    }
  }, [task?.state_revision, task?.pipeline_status, task?.translation_reviews, t]);

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

  // During preflight/POST, a snapshot still belongs to the previous run.
  // Subscribe only after the server has acknowledged this start.
  useTaskSSE(startPending ? null : (running ? task?.task_id : null), event => {
    if (Number.isSafeInteger(event.state_revision)) scope.latestRevision = Math.max(scope.latestRevision ?? -1, event.state_revision);
    if (event.pipeline_status === 'awaiting_dubbing_edit' || event.status === 'awaiting_dubbing_edit') {
      const restored = deriveTaskProcessingState(event, t);
      setRunning(false); setStatus(restored.status); setCurrentStage(restored.currentStage);
      setProgress(restored.progress); setMessage(restored.message);
      refresh().catch(() => {}); return;
    }
    if (task?.dubbing_review && event.dubbing_review === null) refresh().catch(() => {});
    if (event.pipeline_status === 'awaiting_review' || event.status === 'awaiting_review') {
      pauseForReview(event); refresh().catch(() => {}); return;
    }
    if (event.pipeline_cleanup_pending || event.pipeline_status === 'cancelling') {
      const restored = deriveTaskProcessingState(event, t);
      setProgress(restored.progress); setCurrentStage(restored.currentStage);
      setMessage(restored.message); setStatus(restored.status); setRunning(true);
      refresh().catch(() => {});
      return;
    }
    if (event.pipeline_status === 'processing' && status === 'cancelling') {
      setStatus('processing'); setRunning(true); refresh().catch(() => {});
    }
    const transcriptionCompletion = standaloneTranscriptionCompletion(event, t);
    // The video-plan preflight subscribes while this task is still persisted as
    // transcribed. Its initial SSE snapshot is not a new transcription finish.
    if (transcriptionCompletion) {
      if (task?.transcription_path || event.pipeline_status === 'processing') return;
      setProgress(transcriptionCompletion.progress);
      setStatus(transcriptionCompletion.status);
      setCurrentStage(transcriptionCompletion.currentStage);
      setMessage(transcriptionCompletion.message);
      setRunning(transcriptionCompletion.running);
      refresh().catch(() => {});
      return;
    }
    if (isStandaloneTranscriptionEvent(event)) {
      setProgress(value => stageProgressForEvent(event, value));
      setCurrentStage('transcribe');
      setMessage(localizedTaskMessage(event, t));
      return;
    }
    if (route === 'subtitles') {
      if (!['translating', 'burning'].includes(event.status)) return;
      setProgress(value => stageProgressForEvent(event, value));
      setCurrentStage(event.status === 'burning' ? 'render' : 'translate');
      setMessage(localizedTaskMessage(event, t));
      return;
    }
    setProgress(value => stageProgressForEvent(event, value));
    setCurrentStage(value => stageIdForEvent(event, value));
    if (event.message) setMessage(localizedTaskMessage(event, t));
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
    if (event.pipeline_status === 'awaiting_compression') {
      setRunning(false);
      setStatus('awaiting_compression');
      setCurrentStage('dub');
      setMessage(t('compressionApprovalRequired'));
      refresh().catch(() => {});
      return;
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
      setCurrentStage('');
      setMessage(t('pipelineCancelled'));
      refresh().catch(() => {});
    }
  }, task?.state_revision);

  const ensureTranslation = async (force = false) => {
    if (!force && (task.translations?.[targetLang] || (task.target_lang === targetLang && task.translation_path))) return false;
    setCurrentStage('translate');
    setProgress(0);
    setMessage(t('translating'));
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
    pauseForReview(data);
    return true;
  };

  const waitForBurn = async () => {
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 900));
      const response = await fetch(`/api/burn/${task.task_id}`);
      let data = await response.json().catch(() => ({}));
      if (!response.ok) {
        // A failed status request is not proof that the background render failed.
        const persisted = await fetch(`/api/tasks/${task.task_id}`, { cache: 'no-store' });
        const latest = await persisted.json().catch(() => ({}));
        if (!persisted.ok || !latest.task) throw new Error(data.detail || 'Unable to read video rendering status');
        data = latest.task;
      }
      if (data.burn_status === 'completed') return;
      if (data.burn_status === 'failed') throw new Error(data.burn_error || 'Video rendering failed');
    }
  };

  const startSubtitles = async ({ forceTranslate = false, reviewId = null } = {}) => {
    if (await ensureTranslation(forceTranslate)) {
      await refresh();
      return false;
    }
    const reviewResponse = await fetch(`/api/tasks/${task.task_id}/translation-review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_lang: targetLang, review_id: reviewId }),
    });
    const review = await reviewResponse.json().catch(() => ({}));
    if (!reviewResponse.ok) throw new Error(review.detail || 'Subtitle review failed');
    if (!review.approved) {
      pauseForReview(review); await refresh(); return false;
    }
    await refresh();
    setProgress(100);
    setMessage(t('creatingSubtitleFile'));
    await createSubtitleFile();
    if (burn) {
      setCurrentStage('render');
      setProgress(0);
      setMessage(t('burningSubtitlesIntoVideo'));
      const visibility = subtitleVisibility(subtitleContent, showSource, showTarget);
      const response = await fetch('/api/burn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: task.task_id,
          ...visibility,
          style: subtitleStyle,
          target_lang: targetLang,
          end_note_enabled: endNoteEnabled,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Unable to start video rendering');
      refresh().catch(() => {});
      await waitForBurn().catch(error => { error.failedStage = 'burning'; throw error; });
    }
    return true;
  };

  const startDubbing = async ({ forceTranslate = false, forceDub = false, reviewId = null } = {}) => {
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
        end_note_enabled: endNoteEnabled,
        ...visibility,
        subtitle_style: subtitleStyle || {},
        force_translate: forceTranslate,
        force_dub: forceDub,
        review_id: reviewId,
        output_options: videoOutput,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || 'Unable to start dubbing');
      error.httpStatus = response.status;
      throw error;
    }
    setMessage(t('pipelineStarted'));
  };

  const run = async ({ forceTranslate = false, forceDub = false, resume = false } = {}) => {
    if (!scope.active || startRequestRef.current || running || task?.pipeline_cleanup_pending || task?.pipeline_status === 'cancelling' || !task?.transcription_path) return;
    startRequestRef.current = true;
    setStartPending(route === 'dubbing');
    completionHandledRef.current = false;
    setRunning(true);
    setStatus('processing');
    setMessage(t('processing'));
    const hasTranslation = Boolean(task.translations?.[targetLang] || (task.target_lang === targetLang && task.translation_path));
    const firstStage = forceTranslate || !hasTranslation ? 'translate' : route === 'dubbing' || forceDub ? 'dub' : burn ? 'render' : 'translate';
    const latestReviewId = scope.reviewRevision > (task?.state_revision ?? -1)
      ? scope.reviewId : task?.translation_reviews?.[targetLang]?.review_id || scope.reviewId;
    const reviewId = !forceTranslate && (status === 'awaiting_review' || task?.pipeline_status === 'awaiting_review')
      ? latestReviewId || null : null;
    setProgress(0);
    setCurrentStage(firstStage);
    try {
      // Save the actual request before translation can pause. Subtitle-only
      // workflows do not reach export (the old save point) until confirmation.
      const saved = await fetch(`/api/tasks/${task.task_id}/workflow-options`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...options, subtitle_style: subtitleStyle || options.subtitle_style,
          show_source: showSource ?? options.show_source, show_target: showTarget ?? options.show_target }),
      });
      const savedData = await saved.json().catch(() => ({}));
      if (!saved.ok) {
        const error = new Error(savedData.detail || 'Unable to save workflow options');
        error.httpStatus = saved.status;
        throw error;
      }
      if (route === 'dubbing') {
        // Validate and probe before expensive translation/synthesis, even if the
        // folded preview has not finished loading yet.
        const response = await fetch(`/api/tasks/${task.task_id}/video-plan`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ output_options: videoOutput, burn_subtitles: burn, end_note_enabled: endNoteEnabled }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('videoInvalidOptions'));
        setVideoPlan(data);
        setVideoPlanLoading(false);
        await startDubbing({ forceTranslate, forceDub, reviewId });
        refresh().catch(() => {});
        return;
      }
      if (!await startSubtitles({ forceTranslate, reviewId })) return;
      setProgress(100);
      setStatus('completed');
      setCurrentStage('');
      setMessage(t('allOutputsReady'));
      setRunning(false);
      await refresh();
      showToast(t('allOutputsReady'), 'success');
    } catch (error) {
      if (route === 'dubbing' && error.httpStatus === 409 && /pipeline already running/i.test(error.message)) {
        setRunning(true);
        setStatus('processing');
        setMessage(t('processing'));
        refresh().catch(() => {});
        return;
      }
      setRunning(false);
      setStatus('failed');
      setLastFailedStage(error.failedStage || firstStage);
      setMessage(error.message);
      showToast(error.message, 'error');
      refresh().catch(() => {});
    } finally {
      startRequestRef.current = false;
      setStartPending(false);
    }
  };

  const retryFailedStage = () => {
    const failedStage = String(lastFailedStage || task.failed_stage || task.interrupted_status || task.status || '');
    const failureMessage = message || task.message || task.dubbing_error || '';
    return run(retryPlanForFailure(failedStage, failureMessage, route));
  };

  const cancel = async () => {
    if (!scope.active || route !== 'dubbing' || scope.cancelRequestPending || status === 'cancelling' || task?.pipeline_cleanup_pending) return;
    scope.cancelRequestPending = true;
    setCancelPending(true);
    try {
      const response = await fetch(`/api/pipeline/${task.task_id}/cancel`, { method: 'POST' });
      if (!response.ok) throw new Error(t('pipelineCancelFailed'));
      refresh().catch(() => {});
    } catch (error) {
      showToast(error.message || t('pipelineCancelFailed'), 'error');
    } finally {
      scope.cancelRequestPending = false;
      setCancelPending(false);
    }
  };

  const decideCompression = async approve => {
    if (!scope.active) return;
    const response = await fetch(`/api/pipeline/${task.task_id}/compression`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approve }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.detail || t('compressionDecisionFailed'), 'error');
      return;
    }
    if (approve) {
      setRunning(true);
      setStatus('processing');
      setCurrentStage('translate');
      setProgress(0);
      setMessage(t('compressingTranslation'));
    } else {
      setRunning(false);
      setStatus('failed');
      setMessage(t('compressionDeclined'));
    }
    await refresh();
  };

  const displayState = selectedWorkflowState(
    { status, progress, currentStage, message, running }, completedStages, requiredStages, t,
  );
  if (route === 'dubbing' && !displayState.running && ['ready', 'completed'].includes(displayState.status)
      && task?.dubbing_review?.status === 'required' && task.dubbing_review.target_lang === targetLang) {
    Object.assign(displayState, { status: 'awaiting_dubbing_edit', currentStage: 'dub', progress: 0,
      message: t('dubbingManualEditRequired') });
  }
  return {
    burn, cancel, completedStages, currentStage, decideCompression, endNoteEnabled, format, matchingVoices, message, progress,
    requiredStages, retryFailedStage, route, run, running, startPending, cancelPending,
    cleanupWaiting: status === 'cancelling' || Boolean(task?.pipeline_cleanup_pending), setBurn, setEndNoteEnabled, setFormat,
    setRoute, setSpeed, setSubtitleContent, setVoice, speed, status,
    subtitleContent, targetLang, ttsMode, onlineLanguages, voice, voiceError,
    videoOutput, setVideoOutput, videoPlan, videoPlanLoading, videoPlanError,
    ...displayState,
  };
}
