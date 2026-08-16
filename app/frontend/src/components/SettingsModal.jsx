import { useState, useEffect, useRef, useCallback } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import { useTheme } from '../contexts/ThemeContext';
import { AboutSection, InterfaceSettingsSection, ServicesSection } from './SettingsSections';

const FIELDS = [
  { key: 'DEVICE_PREFERENCE', labelKey: 'devicePreference', type: 'select', options: ['auto', 'gpu', 'cpu'] },
  { key: 'COMPUTE_TYPE', labelKey: 'computeType', type: 'select', options: ['auto', 'float16', 'int8'] },
  { key: 'LLM_PROVIDER', labelKey: 'llmProvider', type: 'select', options: ['lm_studio', 'ollama', 'openai_compatible'], span: 2 },
  { key: 'LM_STUDIO_BASE_URL', labelKey: 'lmStudioBaseUrl', type: 'text', span: 2 },
  { key: 'LM_STUDIO_MODEL', labelKey: 'lmStudioModel', type: 'text', span: 2 },
  { key: 'LM_STUDIO_CLI_PATH', labelKey: 'lmStudioCliPath', type: 'text', isPath: true, span: 2 },
  { key: 'LM_STUDIO_PORT', labelKey: 'lmStudioPort', type: 'number' },
  { key: 'LM_STUDIO_TTL_SECONDS', labelKey: 'lmStudioTtl', type: 'number' },
  { key: 'OLLAMA_BASE_URL', labelKey: 'ollamaBaseUrl', type: 'text', span: 2 },
  { key: 'OLLAMA_MODEL', labelKey: 'ollamaModel', type: 'text', span: 2 },
  { key: 'OLLAMA_KEEP_ALIVE', labelKey: 'ollamaKeepAlive', type: 'text', span: 2, hintKey: 'ollamaKeepAliveHint' },
  { key: 'LLM_API_BASE_URL', labelKey: 'llmApiBaseUrl', type: 'text', span: 2 },
  { key: 'LLM_API_KEY', labelKey: 'llmApiKey', type: 'password', span: 2 },
  { key: 'LLM_MODEL_NAME', labelKey: 'llmModelName', type: 'text', span: 2 },
  { key: 'LLM_TEMPERATURE', labelKey: 'llmTemperature', type: 'number', step: 0.1 },
  { key: 'TRANSLATION_BATCH_SIZE', labelKey: 'translationBatchSize', type: 'number' },
  { key: 'CHUNKING_THRESHOLD_MINUTES', labelKey: 'chunkingThreshold', type: 'number' },
  { key: 'FFMPEG_PATH', labelKey: 'ffmpegPath', type: 'text', isPath: true, span: 2 },
  { key: 'TTS_MODE', labelKey: 'ttsMode', type: 'select', options: ['kokoro', 'edge', 'speaches'], hintKey: 'ttsModeHint', span: 2 },
  { key: 'TTS_API_URL', labelKey: 'ttsApiUrl', type: 'text', span: 2 },
  { key: 'TTS_API_KEY', labelKey: 'ttsApiKey', type: 'password', span: 2 },
  { key: 'TTS_MODEL', labelKey: 'ttsModel', type: 'text', span: 2 },
  { key: 'TTS_DEFAULT_VOICE', labelKey: 'ttsDefaultVoice', type: 'text' },
  { key: 'TTS_SPEED', labelKey: 'ttsSpeed', type: 'number', step: 0.05 },
  { key: 'DUB_SAMPLE_RATE', labelKey: 'dubSampleRate', type: 'number' },
  { key: 'KOKORO_MODEL_PATH', labelKey: 'kokoroModelPath', type: 'text', isPath: true, span: 2, hintKey: 'kokoroPathHint' },
  { key: 'KOKORO_VOICES_PATH', labelKey: 'kokoroVoicesPath', type: 'text', isPath: true, span: 2 },
];

// ASR fields rendered separately with a local/remote mode toggle
const ASR_FIELDS = {
  modelPath: { key: 'ASR_MODEL_PATH', labelKey: 'asrModelPath', type: 'text', isPath: true, span: 2 },
  apiUrl: { key: 'ASR_API_URL', labelKey: 'asrApiUrl', type: 'text', span: 2 },
  remoteModel: { key: 'ASR_REMOTE_MODEL', labelKey: 'asrRemoteModel', type: 'text', span: 2 },
};

const DEFAULTS = {
  CHUNKING_THRESHOLD_MINUTES: 15,
  CHUNK_OVERLAP_SECONDS: 3,
  TRANSLATION_BATCH_SIZE: 10,
  LLM_TEMPERATURE: 0.3,
  LLM_PROVIDER: 'lm_studio',
  LM_STUDIO_BASE_URL: 'http://127.0.0.1:1234/v1',
  LM_STUDIO_CLI_PATH: 'lms',
  LM_STUDIO_PORT: 1234,
  LM_STUDIO_TTL_SECONDS: 300,
  OLLAMA_BASE_URL: 'http://127.0.0.1:11434',
  OLLAMA_KEEP_ALIVE: '5m',
};

function fieldMatchesProvider(field, provider) {
  if (field.key.startsWith('LM_STUDIO_')) return provider === 'lm_studio';
  if (field.key.startsWith('OLLAMA_')) return provider === 'ollama';
  if (['LLM_API_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL_NAME'].includes(field.key)) {
    return provider === 'openai_compatible';
  }
  return true;
}

function FieldRow({ f, config, handleChange, textMain, inputBg, inputBorder, models, fetchingModels, t, asrModelInfo, onBrowse }) {
  return (
    <div className={f.span === 2 ? 'md:col-span-2' : ''}>
      <label className={`block text-sm font-medium mb-1 ${textMain}`}>{t(f.labelKey)}</label>
      {f.hintKey && (
        <p className="text-xs text-slate-400 dark:text-slate-500 mb-1">{t(f.hintKey)}</p>
      )}
      {f.type === 'select' ? (
        <select value={config[f.key] || ''} onChange={e => handleChange(f.key, e.target.value)}
          className={`w-full p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}>
          {f.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : f.isPath ? (
        <div>
          <div className="flex gap-2">
            <input
              type="text"
              value={config[f.key] || ''}
              onChange={e => handleChange(f.key, e.target.value)}
              placeholder={f.key === 'ASR_MODEL_PATH' ? t('asrModelPathPlaceholder') : t('ffmpegPathPlaceholder')}
              className={`flex-1 p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}
            />
            <button
              type="button"
              onClick={() => onBrowse && onBrowse(f.key)}
              className={`px-3 py-2 rounded text-sm border ${inputBorder} ${inputBg} hover:bg-gray-200 dark:hover:bg-slate-600 transition ${textMain}`}
            >
              {t('browse')}
            </button>
          </div>
          {f.key === 'ASR_MODEL_PATH' && asrModelInfo && (
            <p className={`text-xs mt-1 ${asrModelInfo.isValid ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
              {asrModelInfo.message}
            </p>
          )}
        </div>
      ) : (f.key === 'LLM_MODEL_NAME' || f.key === 'OLLAMA_MODEL' || f.key === 'LM_STUDIO_MODEL') && models.length > 0 ? (
        <div className="flex gap-2">
          <select
            value={config[f.key] || ''}
            onChange={e => handleChange(f.key, e.target.value)}
            className={`flex-1 p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}
          >
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          {fetchingModels && <span className="text-xs text-slate-400 self-center">...</span>}
        </div>
      ) : (
        <input
          type={f.type}
          step={f.step || undefined}
          value={config[f.key] ?? ''}
          onChange={e => handleChange(f.key, e.target.value)}
          placeholder={t(f.labelKey + 'Placeholder') || ''}
          className={`w-full p-2 ${inputBg} rounded border ${inputBorder} text-sm ${textMain}`}
        />
      )}
    </div>
  );
}

export default function SettingsModal({ onClose }) {
  const showToast = useToast();
  const { t, lang, setLanguage } = useI18n();
  const { theme, setTheme } = useTheme();
  const [config, setConfig] = useState({});
  const [hardware, setHardware] = useState(null);
  const [models, setModels] = useState([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [lmStudioRuntimeReady, setLmStudioRuntimeReady] = useState(null);
  const modelFetchTimerRef = useRef(null);
  const [asrModelInfo, setAsrModelInfo] = useState(null);
  const [isLocal, setIsLocal] = useState(true);
  const [asrMode, setAsrMode] = useState('local'); // 'local' | 'remote'

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(d => {
      setIsLocal(d.is_local !== false);
      const merged = { ...d.config };
      Object.entries(DEFAULTS).forEach(([key, val]) => {
        if (merged[key] === undefined || merged[key] === null || merged[key] === '') {
          merged[key] = val;
        }
      });
      setConfig(merged);
      // Determine ASR mode: remote if ASR_API_URL is set, else local
      const mode = merged.ASR_API_URL ? 'remote' : 'local';
      setAsrMode(mode);
      // Validate ASR configuration on load
      if (mode === 'remote') {
        setAsrModelInfo({
          isValid: true,
          message: `${t('usingRemoteAsr')}: ${merged.ASR_API_URL}`,
        });
      } else if (merged.ASR_MODEL_PATH) {
        validateAsrPath(merged.ASR_MODEL_PATH);
      } else {
        // Scan default models directory when ASR_MODEL_PATH is not set
        fetch('/api/models/asr').then(r => r.json()).then(d => {
          const list = d.models || [];
          if (list.length > 0) {
            setAsrModelInfo({
              isValid: true,
              message: `${t('detectedModels')}: ${list.join(', ')}`,
            });
          } else {
            setAsrModelInfo({
              isValid: false,
              message: t('noModelsFoundUseBrowse'),
            });
          }
        }).catch(() => {
          setAsrModelInfo(null);
        });
      }
    });
    fetch('/api/hardware').then(r => r.json()).then(d => setHardware(d.recommendation));
  }, []);

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  // Auto-fetch models when base URL or key changes
  useEffect(() => {
    if (modelFetchTimerRef.current) {
      clearTimeout(modelFetchTimerRef.current);
    }
    if (config.LLM_PROVIDER === 'ollama') {
      setFetchingModels(true);
      fetch('/api/ollama/status', { signal: AbortSignal.timeout(8000) })
        .then(async r => { if (!r.ok) throw new Error('Ollama unavailable'); return r.json(); })
        .then(data => {
          const list = data.models || [];
          setModels(list);
          if (!config.OLLAMA_MODEL && list.length) {
            setConfig(prev => ({ ...prev, OLLAMA_MODEL: list[0] }));
          }
        })
        .catch(() => setModels([]))
        .finally(() => setFetchingModels(false));
      return;
    }
    if (config.LLM_PROVIDER === 'lm_studio') {
      setFetchingModels(true);
      setLmStudioRuntimeReady(null);
      fetch('/api/lm-studio/status', { signal: AbortSignal.timeout(35000) })
        .then(async r => { if (!r.ok) throw new Error('LM Studio unavailable'); return r.json(); })
        .then(data => {
          const list = data.models || [];
          setModels(list);
          setLmStudioRuntimeReady(data.runtime_ready !== false);
          if (!config.LM_STUDIO_MODEL && list.length) {
            setConfig(prev => ({ ...prev, LM_STUDIO_MODEL: list[0] }));
          }
        })
        .catch(() => {
          setModels([]);
          setLmStudioRuntimeReady(false);
        })
        .finally(() => setFetchingModels(false));
      return;
    }
    const baseUrl = config.LLM_API_BASE_URL;
    const apiKey = config.LLM_API_KEY;
    if (!baseUrl) {
      setModels([]);
      return;
    }
    modelFetchTimerRef.current = setTimeout(async () => {
      await fetchModels(baseUrl, apiKey);
    }, 1000);
    return () => {
      if (modelFetchTimerRef.current) clearTimeout(modelFetchTimerRef.current);
    };
  }, [config.LLM_PROVIDER, config.LLM_API_BASE_URL, config.LLM_API_KEY, config.OLLAMA_BASE_URL, config.LM_STUDIO_BASE_URL]);

  const validateAsrPath = useCallback(async (path) => {
    if (!path) {
      setAsrModelInfo(null);
      return;
    }
    try {
      const scanRes = await fetch('/api/models/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const scanData = await scanRes.json();
      if (scanData.is_direct_model) {
        const size = scanData.models?.[0] || '';
        setAsrModelInfo({
          isValid: true,
          message: `${t('detectedModel')}: whisper-${size}`,
        });
      } else if (scanData.models && scanData.models.length > 0) {
        setAsrModelInfo({
          isValid: false,
          message: `${t('detectedModels')}: ${scanData.models.join(', ')} — ${t('selectDirectModelFolder')}`,
        });
      } else {
        setAsrModelInfo({
          isValid: false,
          message: t('noModelsFound'),
        });
      }
    } catch (e) {
      setAsrModelInfo(null);
    }
  }, [t]);

  const fetchModels = useCallback(async (baseUrl, apiKey) => {
    if (!baseUrl) return;
    setFetchingModels(true);
    const urlsToTry = [
      baseUrl.replace(/\/$/, '') + '/models',
      baseUrl.replace(/\/$/, '') + '/v1/models',
    ];
    for (const url of urlsToTry) {
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
        const res = await fetch(url, { headers, signal: AbortSignal.timeout(8000) });
        if (res.ok) {
          const data = await res.json();
          let list = (data.data || []).map(m => m.id || m.name || m.model).filter(Boolean);
          // Strip paths from model names to avoid exposing directory structure
          list = list.map(name => {
            if (name.includes('/') || name.includes('\\')) {
              return name.split(/[\\/]/).pop();
            }
            return name;
          });
          if (list.length > 0) {
            setModels(list);
            setConfig(prev => {
              const current = prev.LLM_MODEL_NAME;
              if (!current || !list.includes(current)) {
                return { ...prev, LLM_MODEL_NAME: list[0] };
              }
              return prev;
            });
            setFetchingModels(false);
            return;
          }
        }
      } catch (e) {
        // Try next URL
      }
    }
    setModels([]);
    setFetchingModels(false);
  }, []);

  const handleChange = (key, value) => {
    setConfig(prev => ({ ...prev, [key]: value }));
    if (key === 'ASR_API_URL' && asrMode === 'remote') {
      if (value) {
        setAsrModelInfo({ isValid: true, message: `${t('usingRemoteAsr')}: ${value}` });
      } else {
        setAsrModelInfo(null);
      }
    }
    if (key === 'ASR_MODEL_PATH' && asrMode === 'local') {
      validateAsrPath(value);
    }
  };

  const handleAsrModeChange = (mode) => {
    setAsrMode(mode);
    if (mode === 'local') {
      // Clear remote ASR config so local takes effect
      setConfig(prev => ({ ...prev, ASR_API_URL: '', ASR_REMOTE_MODEL: '' }));
      // Re-validate local model
      if (config.ASR_MODEL_PATH) {
        validateAsrPath(config.ASR_MODEL_PATH);
      } else {
        fetch('/api/models/asr').then(r => r.json()).then(d => {
          const list = d.models || [];
          if (list.length > 0) {
            setAsrModelInfo({ isValid: true, message: `${t('detectedModels')}: ${list.join(', ')}` });
          } else {
            setAsrModelInfo({ isValid: false, message: t('noModelsFoundUseBrowse') });
          }
        }).catch(() => setAsrModelInfo(null));
      }
    } else {
      // Clear local model path so remote takes effect
      setConfig(prev => ({ ...prev, ASR_MODEL_PATH: '' }));
      if (config.ASR_API_URL) {
        setAsrModelInfo({ isValid: true, message: `${t('usingRemoteAsr')}: ${config.ASR_API_URL}` });
      } else {
        setAsrModelInfo(null);
      }
    }
  };

  const handleBrowse = async (key) => {
    const endpoint = key === 'ASR_MODEL_PATH' ? '/api/fs/select-file' : '/api/fs/select-file';
    try {
      const res = await fetch(endpoint, { method: 'POST' });
      const data = await res.json();
      if (data.path) {
        let finalPath = data.path;
        if (key === 'ASR_MODEL_PATH') {
          // User selected a file (e.g. model.bin); use its parent directory
          const lastSep = Math.max(data.path.lastIndexOf('\\'), data.path.lastIndexOf('/'));
          finalPath = lastSep > 0 ? data.path.substring(0, lastSep) : data.path;
        }
        setConfig(prev => ({ ...prev, [key]: finalPath }));
        if (key === 'ASR_MODEL_PATH' && asrMode === 'local') {
          validateAsrPath(finalPath);
        }
      }
    } catch (e) {
      console.error('Browse failed:', e);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    // Strip ASR_MODEL_SIZE from config since it's no longer used
    const { ASR_MODEL_SIZE, ...payload } = config;
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(t('settingsSaved'), 'success');
      window.dispatchEvent(new CustomEvent('settings-changed'));
      onClose();
    } else {
      showToast(t('settingsSaveError') + ': ' + (data.detail || t('settingsSaveFailed')), 'error');
    }
  };

  const inputBg = 'bg-gray-100 dark:bg-slate-700';
  const inputBorder = 'border-gray-300 dark:border-slate-600';
  const textMuted = 'text-slate-500 dark:text-slate-400';
  const textMain = 'text-slate-900 dark:text-white';

  const fieldProps = {
    config, handleChange, textMain, inputBg, inputBorder, models, fetchingModels, t, asrModelInfo, onBrowse: handleBrowse
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-2xl border border-gray-200 dark:border-slate-700 w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6">
        <div className="flex justify-between items-center mb-6 border-b border-gray-200 dark:border-slate-700 pb-3">
          <h2 className={`text-xl font-semibold ${textMain}`}>{t('settings')}</h2>
          <button onClick={onClose} className={`${textMuted} hover:text-slate-900 dark:hover:text-white text-2xl leading-none`}>&times;</button>
        </div>

        {hardware && (
          <div className="mb-4 p-3 bg-gray-100/50 dark:bg-slate-700/50 rounded border border-gray-200 dark:border-slate-600 text-sm text-slate-600 dark:text-slate-300">
            {t('detected')}: {hardware[2]}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          {!isLocal && (
            <div className="mb-4 p-3 bg-amber-50 dark:bg-amber-900/20 rounded border border-amber-200 dark:border-amber-800 text-sm text-amber-700 dark:text-amber-300">
              {t('remoteSettingsHint')}
            </div>
          )}
          <InterfaceSettingsSection lang={lang} setLanguage={setLanguage} theme={theme} setTheme={setTheme} inputBg={inputBg} inputBorder={inputBorder} textMain={textMain} />
          <ServicesSection>
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* ASR Mode Section */}
            <div className="md:col-span-2 p-4 bg-gray-50 dark:bg-slate-700/40 rounded-lg border border-gray-200 dark:border-slate-600">
              <label className={`block text-sm font-medium mb-2 ${textMain}`}>{t('asrMode')}</label>
              <div className="flex gap-6 mb-4">
                {isLocal && (
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="asrMode" checked={asrMode === 'local'} onChange={() => handleAsrModeChange('local')}
                      className="w-4 h-4 accent-blue-600" />
                    <span className={`text-sm ${textMain}`}>{t('asrLocalModel')}</span>
                  </label>
                )}
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="radio" name="asrMode" checked={asrMode === 'remote'} onChange={() => handleAsrModeChange('remote')}
                    className="w-4 h-4 accent-blue-600" disabled={!isLocal && asrMode === 'local'} />
                  <span className={`text-sm ${textMain}`}>{t('asrRemoteApi')}</span>
                </label>
              </div>
              {asrMode === 'local' && isLocal && (
                <FieldRow f={ASR_FIELDS.modelPath} {...fieldProps} />
              )}
              {asrMode === 'remote' && (
                <div className="space-y-4">
                  <FieldRow f={ASR_FIELDS.apiUrl} {...fieldProps} />
                  <FieldRow f={ASR_FIELDS.remoteModel} {...fieldProps} />
                  {asrModelInfo && (
                    <p className={`text-xs ${asrModelInfo.isValid ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
                      {asrModelInfo.message}
                    </p>
                  )}
                </div>
              )}
            </div>

            {config.LLM_PROVIDER === 'lm_studio' && lmStudioRuntimeReady === false && (
              <div className="md:col-span-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                {t('lmStudioRuntimeMissing')}
              </div>
            )}

            {FIELDS.filter(f => (isLocal || !f.isPath) && fieldMatchesProvider(f, config.LLM_PROVIDER)).map(f => (
              <FieldRow key={f.key} f={f} {...fieldProps} />
            ))}
          </div>
          </ServicesSection>

          <AboutSection />

          <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-gray-200 dark:border-slate-700">
            <button type="button" onClick={onClose}
              className={`px-4 py-2 ${inputBg} hover:bg-gray-200 dark:hover:bg-slate-600 rounded text-sm transition ${textMain}`}>{t('cancel')}</button>
            <button type="submit"
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded text-sm transition">{t('saveSettings')}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
