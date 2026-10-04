export const ACTIVE_TASK_STATUSES = new Set([
  'pending', 'extracting_audio', 'waiting_for_gpu', 'switching_model', 'transcribing',
  'translating', 'pipeline_transcribing', 'pipeline_translating', 'pipeline_translated',
  'pipeline_dubbing', 'burning', 'dubbing',
]);

export const STANDALONE_TRANSCRIPTION_STATUSES = new Set([
  'pending', 'extracting_audio', 'waiting_for_gpu', 'switching_model', 'transcribing',
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

export function subtitleTranslationProgress(percent, previous = 40) {
  const numeric = Number(percent);
  if (!Number.isFinite(numeric)) return previous;
  return Math.max(previous, 40 + Math.round(Math.max(0, Math.min(95, numeric) - 5) / 90 * 30));
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
  pipeline_transcribing: 'pipeline_transcribing',
  pipeline_translating: 'pipeline_translating',
  pipeline_translated: 'pipeline_translated',
  pipeline_dubbing: 'pipeline_dubbing',
  pipeline_rendering: 'pipeline_rendering',
  burning: 'burning',
  burning_completed: 'burnComplete',
  dubbing_completed: 'dubbingCompleted',
};

function formatMessage(template, values) {
  return Object.entries(values).reduce(
    (message, [key, value]) => message.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

export function localizedTaskMessage(task, t = value => value) {
  const status = String(task?.status || '');
  const rawMessage = String(task?.message || '');
  const pipelineKey = STAGE_MESSAGES[task?.pipeline_stage] || STAGE_MESSAGES[status];
  if (status === 'dubbing') {
    const speech = rawMessage.match(/正在合成语音\s*(\d+)\s*\/\s*(\d+)/);
    if (speech) return formatMessage(t('synthesizingSpeechChunk'), { current: speech[1], total: speech[2] });
    if (/正在准备配音/.test(rawMessage)) return t('preparingDubbing');
    if (/正在对齐时间轴/.test(rawMessage)) return t('aligningDubbing');
    if (/正在合并音视频/.test(rawMessage)) return t('muxingDubbedVideo');
  }
  if (status === 'translating') {
    const batch = rawMessage.match(/^(\d+)\s*\/\s*(\d+)$/);
    if (batch) return formatMessage(t('translatingBatch'), { current: batch[1], total: batch[2] });
    return t('translating');
  }
  if (pipelineKey && status !== 'transcribing' && status !== 'extracting_audio') return t(pipelineKey);
  if (status === 'extracting_audio') return t('extractingAudio');
  if (status !== 'transcribing') return rawMessage;

  const chunk = rawMessage.match(/(?:正在转录第|transcribing\s+(?:chunk|segment))\s*(\d+)\s*\/\s*(\d+)\s*(?:段)?/i);
  if (chunk) {
    return formatMessage(t('transcribingChunk'), { current: chunk[1], total: chunk[2] });
  }
  const duration = rawMessage.match(/(?:音频时长|audio duration)\s*([\d.]+)\s*(?:秒|seconds?)/i);
  if (duration) {
    return formatMessage(t('splittingAudio'), { duration: duration[1] });
  }
  if (/正在运行语音识别|running speech recognition/i.test(rawMessage)) {
    return t('runningSpeechRecognition');
  }
  if (/识别完成.*整理结果|finishing (?:the )?transcription/i.test(rawMessage)) {
    return t('finishingTranscription');
  }
  if (/转录完成.*保存结果|saving (?:the )?transcription/i.test(rawMessage)) {
    return t('savingTranscription');
  }
  if (/正在转录音频|transcribing audio/i.test(rawMessage)) return t('transcribing');
  return rawMessage || t('transcribing');
}

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
  const persistedStage = task?.pipeline_stage || task?.gpu_stage || persistedStatus;
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
    const currentStage = stageId(persistedStage)
      || (STANDALONE_TRANSCRIPTION_STATUSES.has(persistedStatus) ? 'transcribe' : '');
    const standaloneTranscription = currentStage === 'transcribe'
      && STANDALONE_TRANSCRIPTION_STATUSES.has(persistedStatus);
    const standaloneMessage = standaloneTranscription && persistedStatus === 'switching_model'
      ? t('preparingTranscriptionModel')
      : standaloneTranscription && persistedStatus === 'waiting_for_gpu'
        ? t('waitingForTranscriptionModel')
        : standaloneTranscription && persistedStatus === 'pending'
          ? t('transcriptionQueued')
          : '';
    return {
      running: true,
      status: 'processing',
      progress,
      currentStage,
      message: standaloneMessage || localizedTaskMessage(task, t) || t('processing'),
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
