export const ACTIVE_TASK_STATUSES = new Set([
  'pending', 'extracting_audio', 'waiting_for_gpu', 'switching_model', 'transcribing',
  'translating', 'pipeline_transcribing', 'pipeline_translating', 'pipeline_translated',
  'pipeline_dubbing', 'burning', 'dubbing',
]);

export function stageId(status) {
  const value = String(status).toLowerCase();
  if (value.includes('transcrib') || value === 'asr') return 'transcribe';
  if (value.includes('translat')) return 'translate';
  if (value.includes('dubb') || value === 'tts' || value.includes('synth')) return 'dub';
  if (value.includes('burn') || value.includes('render')) return 'render';
  return '';
}

export function stageIdForEvent(event, currentStage = '') {
  return stageId(event?.pipeline_stage)
    || stageId(event?.gpu_stage)
    || stageId(event?.status)
    || currentStage;
}

export function completedStageIds(task, targetLang) {
  const completed = [];
  if (task?.transcription_path) completed.push('transcribe');
  const translated = task?.translations?.[targetLang]
    || (task?.target_lang === targetLang && task?.translation_path);
  if (translated) completed.push('translate');
  const dubbed = task?.dubbing_status === 'completed'
    && (!task?.dubbing_target_lang || task.dubbing_target_lang === targetLang)
    && (task?.dubbing_audio_path || task?.dubbing_video_path);
  if (dubbed) completed.push('dub');
  const rendered = (task?.burn_status === 'completed' && task?.burn_path)
    || (dubbed && task?.dubbing_burn_subtitles === true && task?.dubbing_video_path);
  if (rendered) completed.push('render');
  return completed;
}

export const STAGE_MESSAGES = {
  pipeline_transcribing: 'Transcribing',
  pipeline_translating: 'Translating',
  pipeline_translated: 'Translation complete',
  pipeline_dubbing: 'Creating dubbing',
  burning: 'Rendering subtitles',
};

const DUBBING_RETRANSLATION_PATTERNS = [
  /自然配音内容过长/,
  /dubbing content is too long/i,
  /timing limit reached/i,
];

export function retryPlanForFailure(failedStage, failureMessage, route) {
  const stage = String(failedStage || '');
  if (stage.includes('translat')) {
    return { forceTranslate: true, forceDub: route === 'dubbing', resume: true };
  }
  if (stage.includes('dubb')) {
    const forceTranslate = DUBBING_RETRANSLATION_PATTERNS.some(pattern => (
      pattern.test(String(failureMessage || ''))
    ));
    return { forceTranslate, forceDub: true, resume: true };
  }
  return { resume: true };
}

export function subtitleVisibility(subtitleContent, showSource, showTarget, burn = true) {
  return {
    show_source: Boolean(burn && subtitleContent === 'bilingual' && showSource),
    show_target: Boolean(burn && showTarget),
  };
}

export function standaloneTranscriptionCompletion(event, t = value => value) {
  if (event?.status !== 'transcribed') return null;
  return {
    running: false,
    status: 'ready',
    progress: 0,
    currentStage: '',
    message: t('readyToProcess'),
  };
}

export function deriveTaskProcessingState(task, t = value => value) {
  const persistedStatus = task?.status || '';
  const persistedStage = task?.pipeline_stage || persistedStatus;
  const running = task?.pipeline_status === 'processing'
    || task?.dubbing_status === 'processing'
    || task?.burn_status === 'processing'
    || ACTIVE_TASK_STATUSES.has(persistedStatus);
  const hasPersistedProgress = task?.progress_percent !== null && task?.progress_percent !== undefined;
  const numericProgress = Number(task?.progress_percent);
  const progress = hasPersistedProgress && Number.isFinite(numericProgress)
    ? numericProgress
    : (running ? 4 : 0);

  if (running) {
    return {
      running: true,
      status: 'processing',
      progress,
      currentStage: stageId(persistedStage),
      message: STAGE_MESSAGES[persistedStage] || task?.message || t('processing'),
    };
  }
  if (task?.pipeline_status === 'failed' || ['failed', 'translation_failed'].includes(persistedStatus)) {
    return {
      running: false,
      status: 'failed',
      progress,
      currentStage: stageId(task?.failed_stage || persistedStage),
      message: task?.message || t('statusFailed'),
    };
  }
  if (task?.pipeline_status === 'cancelled' || persistedStatus === 'cancelled') {
    return {
      running: false,
      status: 'cancelled',
      progress,
      currentStage: '',
      message: task?.message || t('pipelineCancelled'),
    };
  }
  if (task?.pipeline_status === 'completed' || persistedStatus === 'completed') {
    return {
      running: false,
      status: 'completed',
      progress: 100,
      currentStage: '',
      message: t('allOutputsReady'),
    };
  }
  return {
    running: false,
    status: 'ready',
    progress: 0,
    currentStage: '',
    message: t('readyToProcess'),
  };
}
