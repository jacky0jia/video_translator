import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import ProgressBar from './ProgressBar';
import SubtitleStyleControls from './SubtitleStyleControls';
import { parseSubtitleTime } from '../utils/subtitleTiming';
import { subtitlePreviewLayout } from '../utils/subtitlePreviewLayout';
import { buildSubtitleDisplay, placeSubtitleBlocks, subtitleBlocksAt } from '../utils/subtitleDisplay';
import { subtitleReviewRows } from '../utils/subtitleReview';
import { transcriptionReview } from '../utils/transcriptionReview';
import { dubbingReviewView, dubbingIssueMessage } from '../utils/dubbingReview';
import { useSubtitleDocument } from '../hooks/useSubtitleDocument';
import SubtitleTimingNotice from './SubtitleTimingNotice';

const FONT_LABEL_MAP = {
  'Arial': 'Arial',
  'Arial Black': 'Arial Black',
  'Calibri': 'Calibri',
  'Cambria': 'Cambria',
  'Comic Sans MS': 'Comic Sans MS',
  'Consolas': 'Consolas',
  'Courier New': 'Courier New',
  'Georgia': 'Georgia',
  'Impact': 'Impact',
  'Segoe UI': 'Segoe UI',
  'Tahoma': 'Tahoma',
  'Times New Roman': 'Times New Roman',
  'Trebuchet MS': 'Trebuchet MS',
  'Verdana': 'Verdana',
  'Microsoft YaHei': '微软雅黑 (Microsoft YaHei)',
  'SimSun': '宋体 (SimSun)',
  'SimHei': '黑体 (SimHei)',
  'KaiTi': '楷体 (KaiTi)',
  'FangSong': '仿宋 (FangSong)',
  'PMingLiU': '新細明體 (PMingLiU)',
  'Noto Sans SC': 'Noto Sans SC',
  'PingFang SC': '苹方 (PingFang SC)',
  'Hiragino Sans GB': '冬青黑体 (Hiragino Sans GB)',
  'Meiryo': 'メイリオ (Meiryo)',
  'MS Gothic': 'MS ゴシック (MS Gothic)',
  'Yu Gothic': '游ゴシック (Yu Gothic)',
  'Yu Mincho': '游明朝 (Yu Mincho)',
  'Malgun Gothic': '맑은 고딕 (Malgun Gothic)',
  'Nanum Gothic': '나눔고딕 (Nanum Gothic)',
};

function formatTime(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = Math.floor(seconds % 60).toString().padStart(2, '0');
  const ms = Math.floor((seconds % 1) * 100).toString().padStart(2, '0');
  return `${m}:${s}.${ms}`;
}

const VP_LANG_CODE_MAP = {
  chinese: { zh: '汉语', en: 'Chinese' },
  english: { zh: '英语', en: 'English' },
  japanese: { zh: '日语', en: 'Japanese' },
  korean: { zh: '韩语', en: 'Korean' },
};

function vpGetLanguageName(code, uiLang) {
  if (!code) return code;
  const lower = code.toLowerCase();
  if (VP_LANG_CODE_MAP[lower]) {
    return VP_LANG_CODE_MAP[lower][uiLang] || VP_LANG_CODE_MAP[lower].en;
  }
  return code;
}

export default function VideoPreview({ task, subtitleStyle, onSubtitleStyleChange, showSource, showTarget, onShowSourceChange, onShowTargetChange, onPreviewLangChange, onTaskRefresh }) {
  const showToast = useToast();
  const { t, lang: uiLang } = useI18n();
  const videoRef = useRef(null);
  const wrapperRef = useRef(null);
  const measurementRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [pendingReviewRow, setPendingReviewRow] = useState(null);
  const [displayTime, setDisplayTime] = useState(0);
  const [measurements, setMeasurements] = useState(null);
  const [fontRevision, setFontRevision] = useState(0);
  const [editingCell, setEditingCell] = useState(null); // { index: number, field: 'source' | 'target' | 'time' }
  const editSessionRef = useRef(null);
  const pendingSavesRef = useRef(new Map());
  const mountedRef = useRef(false);
  const [pendingSaves, setPendingSaves] = useState(() => new Map());
  const [editValue, setEditValue] = useState('');
  const [editStart, setEditStart] = useState('');
  const [editEnd, setEditEnd] = useState('');
  const [previewLang, setPreviewLang] = useState('');
  const [subtitleCollapsed, setSubtitleCollapsed] = useState(false);
  const [isIOSFullscreen, setIsIOSFullscreen] = useState(false);
  const [systemFonts, setSystemFonts] = useState([]);
  const [videoGeometry, setVideoGeometry] = useState({ containerWidth: 0, containerHeight: 0, videoWidth: 0, videoHeight: 0 });

  const { offsetY: subtitleOffsetY, fontSize: subtitleFontSize, sourceColor, targetColor, bold, fontFamily } = subtitleStyle || {};
  const sourceDocument = useSubtitleDocument(task?.task_id, task?.transcription_path, task);
  const targetPath = previewLang && task?.translations?.[previewLang] ? task.translations[previewLang] : task?.translation_path;
  const targetDocument = useSubtitleDocument(task?.task_id, targetPath, task);
  const { segments: sourceSegments, setSegments: setSourceSegments } = sourceDocument;
  const { segments: targetSegments, setSegments: setTargetSegments } = targetDocument;
  const targetData = targetDocument.data;
  const targetReview = Array.isArray(targetData?.alignment_review_rows) ? {
    rows: targetData.alignment_review_rows, check_incomplete: targetData.alignment_check_incomplete,
  } : null;
  // An editor belongs to one document; never carry its draft into another track.
  useLayoutEffect(() => {
    editSessionRef.current = null;
    setEditingCell(null);
  }, [task?.task_id, task?.transcription_path, targetPath]);
  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; editSessionRef.current = null; };
  }, []);
  const savePending = pendingSaves.has(task?.task_id);
  const savingCurrentEdit = savePending && pendingSaves.get(task?.task_id) === editingCell;
  const acquireTimingUndo = useCallback(taskId => {
    if (editSessionRef.current || pendingSavesRef.current.has(taskId)) return null;
    const token = { kind: 'timing' };
    pendingSavesRef.current.set(taskId, token);
    setPendingSaves(new Map(pendingSavesRef.current));
    return () => {
      if (pendingSavesRef.current.get(taskId) === token) {
        pendingSavesRef.current.delete(taskId);
        if (mountedRef.current) setPendingSaves(new Map(pendingSavesRef.current));
      }
    };
  }, []);

  useEffect(() => {
    fetch('/api/fonts')
      .then(r => r.json())
      .then(d => {
        const fonts = d.fonts || [];
        if (fonts.length > 0) {
          setSystemFonts(fonts);
        }
      })
      .catch(() => {});
  }, []);

  const availableTranslations = task?.translations ? Object.keys(task.translations) : [];

  // Reset preview language only when switching to a different task
  useEffect(() => {
    if (!task) {
      setPreviewLang('');
      return;
    }
    const transKeys = task.translations ? Object.keys(task.translations) : [];
    // Handle comma-separated target_lang (e.g. "Korean,Chinese") and pick the first valid translation
    let defaultLang = '';
    if (task.target_lang) {
      const langs = task.target_lang.split(',').map(l => l.trim()).filter(Boolean);
      for (const lang of langs) {
        if (task.translations?.[lang]) {
          defaultLang = lang;
          break;
        }
      }
      if (!defaultLang && task.translations?.[task.target_lang]) {
        defaultLang = task.target_lang;
      }
    }
    if (!defaultLang && transKeys.length > 0) {
      defaultLang = transKeys[0];
    }
    setPreviewLang(defaultLang);
    setDisplayTime(0);
    setEditingCell(null);
  }, [task?.task_id]);

  // When translations are added (e.g. after translation completes) and previewLang is empty/invalid, auto-correct it
  useEffect(() => {
    if (!task) return;
    const transKeys = task.translations ? Object.keys(task.translations) : [];
    if (transKeys.length === 0) return;
    if (!previewLang || !task.translations?.[previewLang]) {
      let defaultLang = '';
      if (task.target_lang) {
        const langs = task.target_lang.split(',').map(l => l.trim()).filter(Boolean);
        for (const lang of langs) {
          if (task.translations?.[lang]) {
            defaultLang = lang;
            break;
          }
        }
        if (!defaultLang && task.translations?.[task.target_lang]) {
          defaultLang = task.target_lang;
        }
      }
      if (!defaultLang) {
        defaultLang = transKeys[0];
      }
      if (defaultLang !== previewLang) {
        setPreviewLang(defaultLang);
      }
    }
  }, [task?.translations, task?.target_lang, previewLang]);

  // Reload video when task changes to ensure the player picks up the file
  useEffect(() => {
    if (videoRef.current && task?.filename) {
      videoRef.current.load();
    }
  }, [task?.task_id]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const video = videoRef.current;
    if (!wrapper || !video) return;
    const update = () => {
      const bounds = wrapper.getBoundingClientRect();
      setVideoGeometry({
        containerWidth: bounds.width, containerHeight: bounds.height,
        videoWidth: video.videoWidth || 1920, videoHeight: video.videoHeight || 1080,
        duration: video.duration,
      });
    };
    const observer = new ResizeObserver(update);
    observer.observe(wrapper);
    video.addEventListener('loadedmetadata', update);
    update();
    return () => { observer.disconnect(); video.removeEventListener('loadedmetadata', update); };
  }, [task?.task_id]);

  // Track fullscreen state to keep subtitles visible
  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(Boolean(wrapperRef.current && document.fullscreenElement === wrapperRef.current));
    };
    document.addEventListener('fullscreenchange', handleChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleChange);
    };
  }, []);

  // Auto-collapse subtitle table on mobile
  useEffect(() => {
    const checkMobile = () => setSubtitleCollapsed(window.innerWidth < 768);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const toggleFullscreen = useCallback(() => {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    if (isIOS) {
      setIsIOSFullscreen(v => !v);
      return;
    }
    if (!wrapperRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      wrapperRef.current.requestFullscreen();
    }
  }, []);

  // Notify parent when previewLang changes
  useEffect(() => {
    onPreviewLangChange?.(previewLang);
  }, [previewLang, onPreviewLangChange]);

  const jumpToSubtitleRow = row => {
    setSubtitleCollapsed(false);
    setPendingReviewRow(row);
  };

  useEffect(() => {
    if (subtitleCollapsed || pendingReviewRow === null) return;
    const row = document.getElementById(`subtitle-row-${pendingReviewRow}`);
    if (row) {
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
      row.focus({ preventScroll: true });
      setPendingReviewRow(null);
    }
  }, [subtitleCollapsed, pendingReviewRow, sourceSegments, targetSegments]);

  const handleTimeUpdate = () => {
    setDisplayTime(videoRef.current?.currentTime || 0);
  };

  const displayEvents = useMemo(() => buildSubtitleDisplay([
    ...(showSource ? [{ role: 'source', segments: sourceSegments }] : []),
    ...(showTarget ? [{ role: 'target', segments: targetSegments }] : []),
  ]), [sourceSegments, targetSegments, showSource, showTarget]);

  const seekTo = (time) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.play();
    }
  };

  const beginEdit = (index, field) => {
    const session = { index, field };
    editSessionRef.current = session;
    setEditingCell(session);
  };
  const cancelEdit = () => { editSessionRef.current = null; setEditingCell(null); };

  const startEdit = (index, field) => {
    if (pendingSavesRef.current.get(task?.task_id)?.kind === 'timing') return;
    if ((field !== 'target' && sourceDocument.status !== 'ready')
      || (field === 'target' && targetDocument.status !== 'ready')
      || (field === 'time' && !['ready', 'missing'].includes(targetDocument.status))) {
      showToast(t('subtitleLoadReadOnly'), 'error');
      return;
    }
    if (field === 'time') {
      const segment = sourceSegments[index];
      if (!segment) return;
      setEditStart(formatTime(segment.start));
      setEditEnd(formatTime(segment.end));
      beginEdit(index, field);
      return;
    }
    const text = field === 'source'
      ? sourceSegments[index]?.text || ''
      : targetSegments[index]?.text || '';
    beginEdit(index, field);
    setEditValue(text);
  };

  const saveEdit = useCallback(async () => {
    if (!task || !editingCell || editSessionRef.current !== editingCell || pendingSavesRef.current.has(task.task_id)) return;
    const { index, field } = editingCell;
    if ((field !== 'target' && sourceDocument.status !== 'ready')
      || (field === 'target' && targetDocument.status !== 'ready')
      || (field === 'time' && !['ready', 'missing'].includes(targetDocument.status))) {
      showToast(t('subtitleLoadReadOnly'), 'error');
      return;
    }
    const payload = {};
    if (field === 'source' && sourceSegments[index]?.text !== editValue) {
      payload.source_text = editValue;
    }
    if (field === 'target' && targetSegments[index]?.text !== editValue) {
      payload.target_text = editValue;
    }
    if (field === 'time') {
      const start = parseSubtitleTime(editStart);
      const end = parseSubtitleTime(editEnd);
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
        showToast(t('invalidSubtitleTime'), 'error');
        return;
      }
      if (sourceSegments[index]?.start !== start || sourceSegments[index]?.end !== end) {
        payload.start = start;
        payload.end = end;
      }
    }
    if (Object.keys(payload).length === 0) {
      cancelEdit();
      return;
    }

    const session = editingCell;
    const isCurrent = () => mountedRef.current && editSessionRef.current === session;
    // Serialize writes for a task, even if its editor is cancelled/reopened or
    // selection moves A -> B -> A before the old response finishes.
    pendingSavesRef.current.set(task.task_id, session);
    setPendingSaves(new Map(pendingSavesRef.current));
    try {
      const res = await fetch(`/api/tasks/${task.task_id}/segments/${index}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!mountedRef.current) return;

      if (res.ok) {
        // Document setters are scoped to their task/path independently of the
        // editor. Update the saved baseline even if a new draft is open, but
        // never replace its input or close it from the old response.
        if (payload.source_text !== undefined) {
          setSourceSegments(prev => {
            const next = [...prev];
            next[index] = { ...next[index], text: editValue, source_text_edited: true };
            return next;
          });
        }
        if (payload.target_text !== undefined) {
          setTargetSegments(prev => {
            const next = [...prev];
            next[index] = { ...next[index], text: editValue };
            return next;
          });
        }
        if (payload.start !== undefined) {
          setSourceSegments(prev => prev.map((segment, row) => row === index ? { ...segment, start: payload.start, end: payload.end } : segment));
          setTargetSegments(prev => prev.map((segment, row) => row === index ? { ...segment, start: payload.start, end: payload.end } : segment));
        }
        if (isCurrent()) cancelEdit();
        await onTaskRefresh?.(task.task_id);
      } else {
        const err = await res.json().catch(() => ({}));
        if (isCurrent()) showToast(t('save') + ' ' + (typeof err.detail === 'string' ? err.detail : t('unknownError')), 'error');
      }
    } catch (error) {
      if (isCurrent()) showToast(t('save') + ' ' + (error.message || t('unknownError')), 'error');
    } finally {
      if (pendingSavesRef.current.get(task.task_id) === session) {
        pendingSavesRef.current.delete(task.task_id);
        if (mountedRef.current) setPendingSaves(new Map(pendingSavesRef.current));
      }
    }
  }, [task, editingCell, editValue, editStart, editEnd, sourceSegments, targetSegments, sourceDocument.status, targetDocument.status, setSourceSegments, setTargetSegments, t, showToast, onTaskRefresh]);

  const previewLayout = subtitlePreviewLayout({ ...videoGeometry, fontSize: subtitleFontSize || 18, offsetY: subtitleOffsetY || 0 });
  const metricScope = useMemo(() => ({}), [displayEvents, previewLayout.imageWidth, previewLayout.fontSize, bold, fontFamily, fontRevision, task?.task_id]);
  useEffect(() => {
    let cancelled = false;
    const update = () => { if (!cancelled) setFontRevision(value => value + 1); };
    document.fonts?.ready.then(update);
    document.fonts?.addEventListener('loadingdone', update);
    return () => { cancelled = true; document.fonts?.removeEventListener('loadingdone', update); };
  }, []);
  useLayoutEffect(() => {
    const container = measurementRef.current;
    if (!container || !previewLayout.imageWidth) return;
    const measure = () => {
      const heights = Object.fromEntries([...container.children].map(node => [node.dataset.blockId, Math.max(1, Math.ceil(node.getBoundingClientRect().height) + 2)]));
      setMeasurements(previous => previous?.scope === metricScope && JSON.stringify(previous.heights) === JSON.stringify(heights) ? previous : { scope: metricScope, heights });
    };
    const observer = new ResizeObserver(measure);
    [...container.children].forEach(node => observer.observe(node));
    measure();
    return () => observer.disconnect();
  }, [metricScope]);
  const placedBlocks = useMemo(() => measurements?.scope === metricScope
    ? placeSubtitleBlocks(displayEvents.blocks, measurements.heights, 2 * previewLayout.imageWidth / 640) : [],
    [metricScope, measurements, displayEvents, previewLayout.imageWidth]);
  const displayBlocks = subtitleBlocksAt(placedBlocks, displayTime);
  const imageHeight = previewLayout.imageWidth * videoGeometry.videoHeight / videoGeometry.videoWidth;
  const availableHeight = imageHeight - 80 * imageHeight / videoGeometry.videoHeight - (subtitleOffsetY || 0) * previewLayout.imageWidth / 640;
  // Warn about genuinely undisplayable rows, not ordinary temporal overlap.
  const displayIssueRows = new Set([...displayEvents.invalidRows,
    ...placedBlocks.filter(block => block.bottom + block.height > availableHeight + 1).map(block => block.row)]);
  const renderParts = block => block.parts.map(line => <div key={line.track} data-subtitle-role={line.role} data-subtitle-row={block.row}
    className={`drop-shadow-[0_1px_1px_rgba(0,0,0,0.5)] ${bold ? 'font-bold' : 'font-normal'}`}
    style={{ fontSize: `${previewLayout.fontSize}px`, lineHeight: 1.15, color: line.role === 'source' ? sourceColor || '#FFFFFF' : targetColor || '#FDE047', fontFamily: fontFamily || 'Arial' }}>{line.text}</div>);

  if (!task) {
    return (
      <section className="app-panel app-preview-empty"><span className="app-step">02</span><div className="app-preview-glyph">▶</div><h2>{t('videoPreview')}</h2><p>{t('selectTaskToPreview')}</p></section>
    );
  }

  const maxLen = Math.max(sourceSegments.length, targetSegments.length);
  const activeReview = targetReview || task?.translation_alignment_review?.[previewLang || task?.target_lang] || {};
  const reviewRows = new Set(subtitleReviewRows(activeReview.rows, targetSegments.length));
  const dubbingReview = dubbingReviewView(task?.dubbing_review, previewLang || task?.target_lang, sourceSegments.length);
  const dubbingRows = new Set(dubbingReview.rows);
  const asrHints = transcriptionReview(sourceSegments, videoGeometry.duration);
  const asrRows = new Map(asrHints.map(hint => [hint.row, hint.reason]));
  const isProcessing = !['transcribed', 'completed', 'failed', 'translation_failed', 'cancelled'].includes(task.status);

  return (
    <section className="app-panel p-4">
      <div className="app-panel-heading"><span className="app-step">02</span><div><h2>{t('videoPreview')}</h2><p>{t('previewPanelHint')}</p></div></div>
      <div ref={wrapperRef} className={`bg-black overflow-hidden ${isIOSFullscreen ? 'fixed inset-0 z-50 flex items-center justify-center rounded-none' : 'relative rounded-lg group'}`}>
        <video ref={videoRef} className={`block ${isFullscreen || isIOSFullscreen ? 'w-full h-full object-contain' : 'w-full'}`} controls controlsList="nofullscreen"
          playsInline
          webkit-playsinline="true"
          preload="metadata"
          src={task.video_url || `/video/${encodeURIComponent(task.filename)}`}
          onTimeUpdate={handleTimeUpdate} onSeeked={handleTimeUpdate}></video>
        {isIOSFullscreen && (
          <button
            onClick={() => setIsIOSFullscreen(false)}
            className="absolute top-4 right-4 p-2 rounded-md bg-black/60 text-white z-50"
            title={t('exitFullscreen')}
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
        <button
          onClick={toggleFullscreen}
          className={`absolute top-3 right-3 p-2 rounded-md bg-black/50 text-white hover:bg-black/70 transition z-10 ${isIOSFullscreen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
          title={isFullscreen || isIOSFullscreen ? t('exitFullscreen') : t('enterFullscreen')}
        >
          {isFullscreen ? (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
            </svg>
          )}
        </button>
        <div
          className="absolute left-1/2 z-[9999] -translate-x-1/2 text-center pointer-events-none break-words"
          data-subtitle-overlay
          style={{ bottom: `${previewLayout.bottom}px`, width: `${previewLayout.imageWidth * 0.9}px`, height: 0 }}
        >
          {displayBlocks.map(block => <div key={block.id} data-subtitle-block={block.id} style={{ position: 'absolute', width: '100%', bottom: block.bottom }}>{renderParts(block)}</div>)}
        </div>
        <div ref={measurementRef} aria-hidden="true" className="absolute text-center pointer-events-none break-words"
          style={{ visibility: 'hidden', height: 0, overflow: 'hidden', width: `${previewLayout.imageWidth * 0.9}px` }}>
          {displayEvents.blocks.map(block => <div key={block.id} data-block-id={block.id}>{renderParts(block)}</div>)}
        </div>
      </div>

      <SubtitleStyleControls style={subtitleStyle} onChange={onSubtitleStyleChange} fonts={systemFonts} fontLabels={FONT_LABEL_MAP} showSource={showSource} showTarget={showTarget} onShowSourceChange={onShowSourceChange} onShowTargetChange={onShowTargetChange} />

      {availableTranslations.length > 1 && (
        <div className="mt-2 flex items-center justify-end">
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-500 dark:text-slate-400">{t('subtitle')}:</span>
            <select
              value={previewLang}
              onChange={e => {
                const val = e.target.value;
                setPreviewLang(val);
                onPreviewLangChange?.(val);
              }}
              className="px-2 py-1 bg-gray-100 dark:bg-slate-700 rounded text-sm border border-gray-300 dark:border-slate-600 text-slate-900 dark:text-white"
            >
              {availableTranslations.map(lang => (
                <option key={lang} value={lang}>{vpGetLanguageName(lang, uiLang)}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Subtitle Timeline Table */}
      <div className="mt-5">
        <SubtitleTimingNotice task={task} blocked={!!editingCell || savePending} acquireUndo={acquireTimingUndo} onTaskRefresh={onTaskRefresh} onJump={jumpToSubtitleRow} />
        {[['source', sourceDocument], ['target', targetDocument]].filter(([, document]) => document.status === 'error').map(([track, document]) => <div key={track} role="alert" data-subtitle-load-error={track} className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
          <p>{t(track === 'source' ? 'sourceSubtitleLoadFailed' : 'targetSubtitleLoadFailed')}</p>
          <p className="mt-1 text-xs">{t('subtitleLoadReadOnly')}</p>
          <button type="button" onClick={document.reload} className="mt-2 underline">{t('retrySubtitleLoad')}</button>
        </div>)}
        {dubbingReview.active && <div role="note" data-testid="dubbing-sync-notice" className="mb-3 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-950 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-100">
          <p>{t('dubbingSyncNotice')}</p>
          {task.dubbing_review.needs_recheck && <p className="mt-1">{t('dubbingSyncRecheck')}</p>}
          {!dubbingReview.issues.length && <p className="mt-1">{t('dubbingSyncNoMeasurements')}</p>}
          <div className="mt-2 flex flex-wrap gap-2">{dubbingReview.rows.map(row => <a key={row} href={`#subtitle-row-${row}`} onClick={event => { event.preventDefault(); jumpToSubtitleRow(row); }} className="rounded border border-rose-500 px-2 py-0.5 underline">#{row}</a>)}</div>
          {dubbingReview.issues.length > 0 && <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs">{dubbingReview.issues.map((issue, index) => <li key={index}>#{issue.rows.join(', #')}: {issue.measured ? dubbingIssueMessage(issue, t('dubbingSyncMeasurements')) : t('dubbingSyncNoMeasurements')}</li>)}</ul>}
        </div>}
        {asrHints.length > 0 && <div role="note" data-testid="asr-review-notice" className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
          <p>{t('asrReviewNotice')}</p>
          <div className="mt-2 flex flex-wrap gap-2">{asrHints.map(({ row, reason }) => <button key={row} type="button" title={t(reason)} className="rounded border border-amber-500 px-2 py-0.5 underline" onClick={() => jumpToSubtitleRow(row)}>#{row}</button>)}</div>
        </div>}
        {displayIssueRows.size > 0 && <div role="note" data-subtitle-overflow className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
          <p>{t('subtitleOverflowNotice')}</p>
          <div className="mt-2 flex flex-wrap gap-2">{[...displayIssueRows].sort((a, b) => a - b).map(row => <button key={row} type="button" className="rounded border border-amber-500 px-2 py-0.5 underline" onClick={() => jumpToSubtitleRow(row)}>#{row}</button>)}</div>
        </div>}
        {(reviewRows.size > 0 || activeReview.check_incomplete) && <div role="note" className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
          <p>{t('alignmentReviewNotice')}</p>
          {activeReview.check_incomplete && <p className="mt-1">{t('alignmentAuditUnavailable')}</p>}
          {reviewRows.size > 0 && <div className="mt-2 flex flex-wrap gap-2">{[...reviewRows].map(row => <button key={row} type="button" className="rounded border border-amber-500 px-2 py-0.5 underline" onClick={() => jumpToSubtitleRow(row)}>#{row}</button>)}</div>}
        </div>}
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">{t('subtitleTimeline')}</h3>
          <button
            onClick={() => setSubtitleCollapsed(v => !v)}
            title={subtitleCollapsed ? t('expandSubtitle') : t('collapseSubtitle')}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-slate-700 text-slate-500 dark:text-slate-400 transition"
          >
            {subtitleCollapsed ? (
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>
            )}
          </button>
        </div>
        {!subtitleCollapsed && (
        <div className="app-subtitle-table-wrap max-h-80 overflow-y-auto border border-gray-200 dark:border-slate-700 rounded-lg overflow-x-auto">
          <table role="table" className="app-subtitle-table w-full min-w-[28rem] text-left text-sm">
            <thead className="bg-gray-100 dark:bg-slate-700 sticky top-0">
              <tr>
                <th className="px-3 py-2 w-24">{t('time')}</th>
                <th className="px-3 py-2">{t('source')}</th>
                <th className="px-3 py-2">{t('target')}</th>
              </tr>
            </thead>
            <tbody role="rowgroup" className="divide-y divide-gray-200 dark:divide-slate-700">
              {Array.from({ length: maxLen }).map((_, i) => {
                const src = sourceSegments[i];
                const tgt = targetSegments[i];
                const isEditingSource = editingCell?.index === i && editingCell?.field === 'source';
                const isEditingTarget = editingCell?.index === i && editingCell?.field === 'target';
                const isEditingTime = editingCell?.index === i && editingCell?.field === 'time';
                const textareaRows = Math.max(3, editValue.split('\n').length);
                return (
                  <tr key={i} id={`subtitle-row-${i + 1}`} tabIndex={-1} role="row" title={[dubbingRows.has(i + 1) ? t('dubbingSyncRow') : '', asrRows.has(i + 1) ? t(asrRows.get(i + 1)) : '', displayIssueRows.has(i + 1) ? t('subtitleOverflowNotice') : '', reviewRows.has(i + 1) ? t('alignmentReviewRow') : ''].filter(Boolean).join(' · ') || undefined}
                    className={`${dubbingRows.has(i + 1) ? 'bg-rose-100/80 dark:bg-rose-900/30' : asrRows.has(i + 1) || displayIssueRows.has(i + 1) || reviewRows.has(i + 1) ? 'bg-amber-100/80 dark:bg-amber-900/30' : isEditingSource || isEditingTarget || isEditingTime ? 'bg-gray-100 dark:bg-slate-700' : 'hover:bg-gray-100/50 dark:hover:bg-slate-700/50'} transition`}>
                    <td role="cell" data-label={t('time')} className="app-subtitle-time px-3 py-2 text-slate-500 dark:text-slate-400 whitespace-nowrap">
                      <span className="text-xs text-slate-400">#{i + 1}</span>
                      {isEditingTime ? <div className="flex flex-col gap-1">
                        <input aria-label={t('startTime')} value={editStart} disabled={savingCurrentEdit} onChange={e => setEditStart(e.target.value)} className="w-24 rounded border px-1 py-0.5 text-slate-900" />
                        <input aria-label={t('endTime')} value={editEnd} disabled={savingCurrentEdit} onChange={e => setEditEnd(e.target.value)} className="w-24 rounded border px-1 py-0.5 text-slate-900" />
                        <div className="flex gap-1"><button onClick={saveEdit} disabled={savePending} className="rounded bg-green-600 px-2 py-0.5 text-white">{t('save')}</button><button onClick={cancelEdit} className="rounded bg-gray-500 px-2 py-0.5 text-white">{t('cancel')}</button></div>
                      </div> : src || tgt ? (
                        <div className="flex items-center gap-1"><span
                          onClick={() => seekTo((src || tgt).start)}
                          className="cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 underline decoration-dotted"
                          title={t('jumpToTime')}
                        >
                          {formatTime((src || tgt).start)} - {formatTime((src || tgt).end)}
                        </span>{src && <button type="button" onClick={() => startEdit(i, 'time')} aria-label={`${t('editTime')} #${i + 1}`} title={t('editTime')} className="rounded border border-slate-300 px-1 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-700">✎</button>}</div>
                      ) : '-'}
                    </td>
                    <td role="cell" data-label={t('source')} className="px-3 py-2">
                      {isEditingSource ? (
                        <div className="flex items-start gap-2">
                          <textarea
                            value={editValue}
                            disabled={savingCurrentEdit}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); }
                              if (e.key === 'Escape') cancelEdit();
                            }}
                            rows={textareaRows}
                            className="flex-1 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500 text-slate-900 dark:text-white resize-none"
                          />
                          <div className="flex flex-col gap-1">
                            <button onClick={saveEdit} disabled={savePending}
                              className="px-2 py-1 bg-green-600 hover:bg-green-500 text-white rounded text-xs">{t('save')}</button>
                            <button onClick={cancelEdit}
                              className="px-2 py-1 bg-gray-400 hover:bg-gray-500 dark:bg-slate-600 dark:hover:bg-slate-500 text-white rounded text-xs">{t('cancel')}</button>
                          </div>
                        </div>
                      ) : (
                        <span role={src ? 'button' : undefined} tabIndex={src ? 0 : undefined} aria-label={src ? `${t('source')} #${i + 1}` : undefined} onClick={src ? () => startEdit(i, 'source') : undefined}
                          onKeyDown={e => { if (src && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); startEdit(i, 'source'); } }}
                          className="block min-h-[1.5rem] text-slate-800 dark:text-slate-200 cursor-pointer">{src ? src.text || <span className="text-slate-400">{t('emptySubtitleEdit')}</span> : ''}</span>
                      )}
                    </td>
                    <td role="cell" data-label={t('target')} className="px-3 py-2">
                      {isEditingTarget ? (
                        <div className="flex items-start gap-2">
                          <textarea
                            value={editValue}
                            disabled={savingCurrentEdit}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); }
                              if (e.key === 'Escape') cancelEdit();
                            }}
                            rows={textareaRows}
                            className="flex-1 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500 text-slate-900 dark:text-white resize-none"
                          />
                          <div className="flex flex-col gap-1">
                            <button onClick={saveEdit} disabled={savePending}
                              className="px-2 py-1 bg-green-600 hover:bg-green-500 text-white rounded text-xs">{t('save')}</button>
                            <button onClick={cancelEdit}
                              className="px-2 py-1 bg-gray-400 hover:bg-gray-500 dark:bg-slate-600 dark:hover:bg-slate-500 text-white rounded text-xs">{t('cancel')}</button>
                          </div>
                        </div>
                      ) : (
                        <span role={tgt ? 'button' : undefined} tabIndex={tgt ? 0 : undefined} aria-label={tgt ? `${t('target')} #${i + 1}` : undefined} onClick={tgt ? () => startEdit(i, 'target') : undefined}
                          onKeyDown={e => { if (tgt && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); startEdit(i, 'target'); } }}
                          className="block min-h-[1.5rem] text-yellow-600 dark:text-yellow-300 cursor-pointer">{tgt ? tgt.text || <span className="text-slate-400">{t('emptySubtitleEdit')}</span> : ''}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>
    </section>
  );
}
