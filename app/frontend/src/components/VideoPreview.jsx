import { useState, useEffect, useRef, useCallback } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import ProgressBar from './ProgressBar';
import SubtitleStyleControls from './SubtitleStyleControls';

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

export default function VideoPreview({ task, subtitleStyle, onSubtitleStyleChange, showSource, showTarget, onShowSourceChange, onShowTargetChange, onPreviewLangChange }) {
  const showToast = useToast();
  const { t, lang: uiLang } = useI18n();
  const videoRef = useRef(null);
  const wrapperRef = useRef(null);
  const subtitleRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [sourceSegments, setSourceSegments] = useState([]);
  const [targetSegments, setTargetSegments] = useState([]);
  const [subSource, setSubSource] = useState('');
  const [subTarget, setSubTarget] = useState('');
  const [editingCell, setEditingCell] = useState(null); // { index: number, field: 'source' | 'target' }
  const [editValue, setEditValue] = useState('');
  const [previewLang, setPreviewLang] = useState('');
  const [subtitleCollapsed, setSubtitleCollapsed] = useState(false);
  const [isIOSFullscreen, setIsIOSFullscreen] = useState(false);
  const [systemFonts, setSystemFonts] = useState([]);

  const { offsetY: subtitleOffsetY, fontSize: subtitleFontSize, sourceColor, targetColor, bold, fontFamily } = subtitleStyle || {};

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
    setSourceSegments([]);
    setTargetSegments([]);
    setSubSource('');
    setSubTarget('');
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

  // Track fullscreen state to keep subtitles visible
  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
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

  // Load segments when task or previewLang changes
  useEffect(() => {
    if (!task) return;
    async function load() {
      if (task.transcription_path) {
        try {
          const res = await fetch(task.transcription_path, { cache: 'no-store' });
          const data = await res.json();
          setSourceSegments(data.segments || []);
        } catch (e) {
          console.error('Failed to load source segments:', e);
        }
      }
      const transPath = previewLang && task.translations?.[previewLang]
        ? task.translations[previewLang]
        : task.translation_path;
      if (transPath) {
        try {
          const res = await fetch(transPath, { cache: 'no-store' });
          const data = await res.json();
          setTargetSegments(data.segments || []);
        } catch (e) {
          console.error('Failed to load target segments:', e);
          setTargetSegments([]);
        }
      } else {
        setTargetSegments([]);
      }
    }
    load();
  }, [task, previewLang]);

  const handleTimeUpdate = () => {
    const time = videoRef.current?.currentTime || 0;
    const src = sourceSegments.find(s => time >= s.start && time <= s.end);
    const tgt = targetSegments.find(s => time >= s.start && time <= s.end);
    setSubSource(src ? src.text : '');
    setSubTarget(tgt ? tgt.text : '');
  };

  const seekTo = (time) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.play();
    }
  };

  const startEdit = (index, field) => {
    const text = field === 'source'
      ? sourceSegments[index]?.text || ''
      : targetSegments[index]?.text || '';
    setEditingCell({ index, field });
    setEditValue(text);
  };

  const saveEdit = useCallback(async () => {
    if (!task || !editingCell) return;
    const { index, field } = editingCell;
    const payload = {};
    if (field === 'source' && sourceSegments[index]?.text !== editValue) {
      payload.source_text = editValue;
    }
    if (field === 'target' && targetSegments[index]?.text !== editValue) {
      payload.target_text = editValue;
    }
    if (Object.keys(payload).length === 0) {
      setEditingCell(null);
      return;
    }

    const res = await fetch(`/api/tasks/${task.task_id}/segments/${index}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      if (payload.source_text !== undefined) {
        setSourceSegments(prev => {
          const next = [...prev];
          next[index] = { ...next[index], text: editValue };
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
      setEditingCell(null);
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(t('save') + ' ' + (err.detail || t('unknownError')), 'error');
    }
  }, [task, editingCell, editValue, sourceSegments, targetSegments, t, showToast]);

  const cancelEdit = () => setEditingCell(null);

  if (!task) {
    return (
      <section className="app-panel app-preview-empty"><span className="app-step">02</span><div className="app-preview-glyph">▶</div><h2>{t('videoPreview')}</h2><p>{t('selectTaskToPreview')}</p></section>
    );
  }

  const maxLen = Math.max(sourceSegments.length, targetSegments.length);
  const isProcessing = !['transcribed', 'completed', 'failed', 'translation_failed', 'cancelled'].includes(task.status);

  return (
    <section className="app-panel p-4">
      <div className="app-panel-heading"><span className="app-step">02</span><div><h2>{t('videoPreview')}</h2><p>{t('previewPanelHint')}</p></div></div>
      <div ref={wrapperRef} className={`bg-black overflow-hidden ${isIOSFullscreen ? 'fixed inset-0 z-50 flex items-center justify-center rounded-none' : 'relative rounded-lg group'}`}>
        <video ref={videoRef} className={`block ${isIOSFullscreen ? 'w-full h-full object-contain' : 'w-full'}`} controls controlsList="nofullscreen"
          playsInline
          webkit-playsinline="true"
          preload="metadata"
          src={task.video_url || `/video/${encodeURIComponent(task.filename)}`}
          onTimeUpdate={handleTimeUpdate}></video>
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
          ref={subtitleRef}
          className={`text-center pointer-events-none space-y-1 px-4 ${
            isFullscreen || isIOSFullscreen
              ? 'fixed left-0 right-0 z-[9999]'
              : 'absolute left-0 right-0'
          }`}
          style={{ bottom: (isFullscreen || isIOSFullscreen) ? `${80 + (subtitleOffsetY || 0)}px` : `${64 + (subtitleOffsetY || 0)}px` }}
        >
          {showSource && <div className={`drop-shadow-[0_1px_1px_rgba(0,0,0,0.5)] max-h-24 overflow-hidden break-words leading-snug ${bold ? 'font-bold' : 'font-semibold'}`} style={{ fontSize: `${subtitleFontSize || 18}px`, color: sourceColor || '#FFFFFF', fontFamily: fontFamily || 'Arial' }}>{subSource}</div>}
          {showTarget && <div className={`drop-shadow-[0_1px_1px_rgba(0,0,0,0.5)] max-h-20 overflow-hidden break-words leading-snug ${bold ? 'font-bold' : 'font-medium'}`} style={{ fontSize: `${Math.max(12, (subtitleFontSize || 18) - 2)}px`, color: targetColor || '#FDE047', fontFamily: fontFamily || 'Arial' }}>{subTarget}</div>}
        </div>
      </div>

      <SubtitleStyleControls style={subtitleStyle} onChange={onSubtitleStyleChange} fonts={systemFonts} fontLabels={FONT_LABEL_MAP} />

      <div className="mt-2 flex items-center gap-4 flex-wrap">
        <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showSource}
            onChange={e => onShowSourceChange(e.target.checked)}
            className="w-4 h-4 rounded border-gray-300 dark:border-slate-600 bg-gray-100 dark:bg-slate-700 text-blue-600 focus:ring-blue-500"
          />
          {t('showSource')}
        </label>
        <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showTarget}
            onChange={e => onShowTargetChange(e.target.checked)}
            className="w-4 h-4 rounded border-gray-300 dark:border-slate-600 bg-gray-100 dark:bg-slate-700 text-blue-600 focus:ring-blue-500"
          />
          {t('showTarget')}
        </label>
        {availableTranslations.length > 1 && (
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
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{t('subtitleDisplayHint')}</p>

      {/* Subtitle Timeline Table */}
      <div className="mt-5">
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
                const textareaRows = Math.max(3, editValue.split('\n').length);
                return (
                  <tr key={i} role="row"
                    className={`${isEditingSource || isEditingTarget ? 'bg-gray-100 dark:bg-slate-700' : 'hover:bg-gray-100/50 dark:hover:bg-slate-700/50'} transition`}>
                    <td role="cell" data-label={t('time')} className="app-subtitle-time px-3 py-2 text-slate-500 dark:text-slate-400 whitespace-nowrap">
                      {src || tgt ? (
                        <span
                          onClick={() => seekTo((src || tgt).start)}
                          className="cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 underline decoration-dotted"
                          title={t('jumpToTime')}
                        >
                          {formatTime((src || tgt).start)} - {formatTime((src || tgt).end)}
                        </span>
                      ) : '-'}
                    </td>
                    <td role="cell" data-label={t('source')} className="px-3 py-2">
                      {isEditingSource ? (
                        <div className="flex items-start gap-2">
                          <textarea
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); }
                              if (e.key === 'Escape') cancelEdit();
                            }}
                            rows={textareaRows}
                            className="flex-1 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500 text-slate-900 dark:text-white resize-none"
                          />
                          <div className="flex flex-col gap-1">
                            <button onClick={saveEdit}
                              className="px-2 py-1 bg-green-600 hover:bg-green-500 text-white rounded text-xs">{t('save')}</button>
                            <button onClick={cancelEdit}
                              className="px-2 py-1 bg-gray-400 hover:bg-gray-500 dark:bg-slate-600 dark:hover:bg-slate-500 text-white rounded text-xs">{t('cancel')}</button>
                          </div>
                        </div>
                      ) : (
                        <span onClick={() => startEdit(i, 'source')} className="text-slate-800 dark:text-slate-200 cursor-pointer">{src?.text || ''}</span>
                      )}
                    </td>
                    <td role="cell" data-label={t('target')} className="px-3 py-2">
                      {isEditingTarget ? (
                        <div className="flex items-start gap-2">
                          <textarea
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); }
                              if (e.key === 'Escape') cancelEdit();
                            }}
                            rows={textareaRows}
                            className="flex-1 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500 text-slate-900 dark:text-white resize-none"
                          />
                          <div className="flex flex-col gap-1">
                            <button onClick={saveEdit}
                              className="px-2 py-1 bg-green-600 hover:bg-green-500 text-white rounded text-xs">{t('save')}</button>
                            <button onClick={cancelEdit}
                              className="px-2 py-1 bg-gray-400 hover:bg-gray-500 dark:bg-slate-600 dark:hover:bg-slate-500 text-white rounded text-xs">{t('cancel')}</button>
                          </div>
                        </div>
                      ) : (
                        <span onClick={() => startEdit(i, 'target')} className="text-yellow-600 dark:text-yellow-300 cursor-pointer">{tgt?.text || ''}</span>
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
