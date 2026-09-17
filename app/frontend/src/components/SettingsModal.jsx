import { useState, useEffect, useRef, useCallback } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import { useTheme } from '../contexts/ThemeContext';
import { AboutSection, InterfaceSettingsSection, ServicesSection } from './SettingsSections';
import SettingsField from './settings/SettingsField';
import QwenRuntimeStatus from './settings/QwenRuntimeStatus';
import SettingsSection from './settings/SettingsSection';
import { fieldIsVisible } from '../settings/visibility';
import { pollRuntimeStatus } from '../settings/pollRuntimeStatus';

export default function SettingsModal({ onClose }) {
  const showToast = useToast();
  const { t, lang, setLanguage } = useI18n();
  const { theme, setTheme } = useTheme();
  const [config, setConfig] = useState({});
  const [hardware, setHardware] = useState(null);
  const [models, setModels] = useState([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [lmStudioRuntimeReady, setLmStudioRuntimeReady] = useState(null);
  const [lmStudioRefreshToken, setLmStudioRefreshToken] = useState(0);
  const [lmStudioStatusError, setLmStudioStatusError] = useState(false);
  const modelFetchTimerRef = useRef(null);
  const overlayPointerDownRef = useRef(false);
  const asrDraftsRef = useRef({ localPath: '', remoteUrl: '', remoteModel: '' });
  const [asrModelInfo, setAsrModelInfo] = useState(null);
  const [isLocal, setIsLocal] = useState(null);
  const [asrMode, setAsrMode] = useState('local'); // 'local' | 'remote'
  const [schema, setSchema] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [schemaError, setSchemaError] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [qwenInstall, setQwenInstall] = useState({ llama_archive: '', cuda_archive: '', model_directory: '', device: 'cuda' });
  const [qwenInstalling, setQwenInstalling] = useState(false);
  const [qwenInstallResult, setQwenInstallResult] = useState(null);
  const [qwenPreflight, setQwenPreflight] = useState(null);
  const [qwenPreflighting, setQwenPreflighting] = useState(false);
  const [qwenInstallStatus, setQwenInstallStatus] = useState(null);
  const [qwenRuntimeStatus, setQwenRuntimeStatus] = useState(null);
  const [qwenRepairOpen, setQwenRepairOpen] = useState(false);
  const [upstreamDependencies, setUpstreamDependencies] = useState(null);

  useEffect(() => {
    if (!isLocal) return;
    return pollRuntimeStatus(setQwenRuntimeStatus);
  }, [isLocal]);

  useEffect(() => {
    Promise.all([
      fetch('/api/config').then(r => r.ok ? r.json() : Promise.reject(new Error('config'))),
      fetch('/api/config/schema').then(r => r.ok ? r.json() : Promise.reject(new Error('schema'))),
      fetch('/api/capabilities').then(r => r.ok ? r.json() : null),
      fetch('/api/hardware').then(r => r.ok ? r.json() : null),
    ]).then(([d, schemaData, capabilityData, hardwareData]) => {
      setIsLocal(d.is_local !== false);
      setUpstreamDependencies(d.upstream_dependencies || null);
      const merged = { ...d.config };
      (schemaData.fields || []).forEach(field => {
        if (merged[field.key] === undefined || merged[field.key] === null) merged[field.key] = field.default;
      });
      setSchema(schemaData);
      setCapabilities(capabilityData);
      setHardware(hardwareData?.recommendation || null);
      setConfig(merged);
      if (d.is_local !== false) {
        fetch('/api/qwen-tts/install-status')
          .then(response => response.ok ? response.json() : Promise.reject(new Error('install status')))
          .then(data => {
            setQwenInstallStatus(data);
            if (data.upstream_sources) {
              setQwenInstall(current => {
                const defaults = data.upstream_sources[current.device === 'vulkan' ? 'vulkan' : 'cuda'];
                return { ...current,
                  llama_archive: current.llama_archive || defaults.llama_archive,
                  cuda_archive: current.cuda_archive || defaults.cuda_archive,
                  model_directory: current.model_directory || data.upstream_sources.model_directory,
                };
              });
            }
          })
          .catch(() => setQwenInstallStatus({ installed: false, managed: false }));
      }
      asrDraftsRef.current = {
        localPath: merged.ASR_MODEL_PATH || '',
        remoteUrl: merged.ASR_API_URL || '',
        remoteModel: merged.ASR_REMOTE_MODEL || '',
      };
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
    }).catch(() => {
      setSchemaError(true);
      fetch('/api/config').then(r => r.json()).then(d => {
        setIsLocal(d.is_local !== false);
        const fallbackConfig = d.config || {};
        setConfig(fallbackConfig);
        asrDraftsRef.current = {
          localPath: fallbackConfig.ASR_MODEL_PATH || '',
          remoteUrl: fallbackConfig.ASR_API_URL || '',
          remoteModel: fallbackConfig.ASR_REMOTE_MODEL || '',
        };
      }).catch(() => {});
    });
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
      setLmStudioStatusError(false);
      fetch('/api/lm-studio/status', { signal: AbortSignal.timeout(35000) })
        .then(async r => { if (!r.ok) throw new Error('LM Studio unavailable'); return r.json(); })
        .then(data => {
          const list = data.models || [];
          setModels(list);
          setLmStudioRuntimeReady(data.runtime_ready ?? null);
          setLmStudioStatusError(data.healthy === false);
          if (!config.LM_STUDIO_MODEL && list.length) {
            setConfig(prev => ({ ...prev, LM_STUDIO_MODEL: list[0] }));
          }
        })
        .catch(() => {
          setModels([]);
          setLmStudioRuntimeReady(null);
          setLmStudioStatusError(true);
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
  }, [config.LLM_PROVIDER, config.LLM_API_BASE_URL, config.LLM_API_KEY, config.OLLAMA_BASE_URL, config.LM_STUDIO_BASE_URL, lmStudioRefreshToken]);

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
    if (key === 'ASR_MODEL_PATH') asrDraftsRef.current.localPath = value;
    if (key === 'ASR_API_URL') asrDraftsRef.current.remoteUrl = value;
    if (key === 'ASR_REMOTE_MODEL') asrDraftsRef.current.remoteModel = value;
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
      asrDraftsRef.current.remoteUrl = config.ASR_API_URL || asrDraftsRef.current.remoteUrl;
      asrDraftsRef.current.remoteModel = config.ASR_REMOTE_MODEL || asrDraftsRef.current.remoteModel;
      const localPath = asrDraftsRef.current.localPath;
      // Empty the remote selector so local ASR is active, but retain its draft
      // for a later switch back within this settings session.
      setConfig(prev => ({ ...prev, ASR_MODEL_PATH: localPath, ASR_API_URL: '', ASR_REMOTE_MODEL: '' }));
      // Re-validate local model
      if (localPath) {
        validateAsrPath(localPath);
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
      asrDraftsRef.current.localPath = config.ASR_MODEL_PATH || asrDraftsRef.current.localPath;
      const remoteUrl = asrDraftsRef.current.remoteUrl;
      const remoteModel = asrDraftsRef.current.remoteModel;
      // Empty the local selector so remote ASR is active, but retain its draft
      // for a later switch back within this settings session.
      setConfig(prev => ({ ...prev, ASR_MODEL_PATH: '', ASR_API_URL: remoteUrl, ASR_REMOTE_MODEL: remoteModel }));
      if (remoteUrl) {
        setAsrModelInfo({ isValid: true, message: `${t('usingRemoteAsr')}: ${remoteUrl}` });
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
        if (key === 'ASR_MODEL_PATH') asrDraftsRef.current.localPath = finalPath;
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
    if (isLocal) payload.UI_LANGUAGE = lang;
    else delete payload.UI_LANGUAGE;
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

  const handleQwenInstall = async () => {
    setQwenInstalling(true);
    setQwenInstallResult(null);
    try {
      const response = await fetch('/api/qwen-tts/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(qwenInstall),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || t('qwenInstallFailed'));
      setQwenInstallResult(data);
      setQwenInstallStatus(data);
      showToast(t('qwenInstallSucceeded'), 'success');
    } catch (error) {
      showToast(`${t('qwenInstallFailed')}: ${error.message}`, 'error');
    } finally {
      setQwenInstalling(false);
    }
  };

  const handleQwenPreflight = async () => {
    setQwenPreflighting(true);
    setQwenPreflight(null);
    try {
      const response = await fetch('/api/qwen-tts/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(qwenInstall),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || t('qwenPreflightFailed'));
      setQwenPreflight(data);
    } catch (error) {
      showToast(`${t('qwenPreflightFailed')}: ${error.message}`, 'error');
    } finally {
      setQwenPreflighting(false);
    }
  };

  const updateQwenInstall = (key, value) => {
    setQwenInstall(current => {
      const next = { ...current, [key]: value };
      const sources = qwenInstallStatus?.upstream_sources;
      if (key === 'device' && sources) {
        const previous = sources[current.device === 'vulkan' ? 'vulkan' : 'cuda'];
        const defaults = sources[value === 'vulkan' ? 'vulkan' : 'cuda'];
        for (const field of ['llama_archive', 'cuda_archive']) {
          if (!current[field] || current[field] === previous[field]) next[field] = defaults[field];
        }
      }
      return next;
    });
    setQwenPreflight(null);
    setQwenInstallResult(null);
  };

  const gib = bytes => (Number(bytes || 0) / (1024 ** 3)).toFixed(2);

  const inputBg = 'bg-gray-100 dark:bg-slate-700';
  const inputBorder = 'border-gray-300 dark:border-slate-600';
  const textMuted = 'text-slate-500 dark:text-slate-400';
  const textMain = 'text-slate-900 dark:text-white';

  const fieldProps = {
    config, onChange: handleChange, textMain, inputBg, inputBorder, models, fetchingModels, t, asrModelInfo, onBrowse: handleBrowse
  };
  const fieldsByKey = Object.fromEntries((schema?.fields || []).map(field => [field.key, field]));
  const asrKeys = new Set(['ASR_MODEL_PATH', 'ASR_API_URL', 'ASR_REMOTE_MODEL', 'UI_LANGUAGE']);
  const visibleFields = (schema?.fields || []).filter(field =>
    !asrKeys.has(field.key)
    && (showAdvanced || field.level !== 'advanced')
    && fieldIsVisible(field, { ...config, ASR_MODE: asrMode })
  );
  const advancedFieldCount = (schema?.fields || []).filter(field =>
    !asrKeys.has(field.key)
    && field.level === 'advanced'
    && fieldIsVisible(field, { ...config, ASR_MODE: asrMode })
  ).length;
  const qwenNeedsCudaArchive = qwenInstall.device !== 'vulkan';
  const qwenSourcesReady = Boolean(
    qwenInstall.llama_archive
    && qwenInstall.model_directory
    && (!qwenNeedsCudaArchive || qwenInstall.cuda_archive)
  );

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onPointerDown={e => { overlayPointerDownRef.current = e.target === e.currentTarget; }}
      onClick={e => {
        const isOverlayClick = overlayPointerDownRef.current && e.target === e.currentTarget;
        overlayPointerDownRef.current = false;
        if (isOverlayClick) onClose();
      }}>
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
          {isLocal && upstreamDependencies?.user_install && (
            <div className="mb-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
              <p>{t('upstreamDependenciesHint')}</p>
              {upstreamDependencies.missing?.length > 0 && <p className="mt-1">{t('upstreamDependenciesMissing')}: {upstreamDependencies.missing.join(', ')}</p>}
              <a className="mt-2 inline-block underline" href="/api/dependencies/install-guide" target="_blank" rel="noreferrer">{t('upstreamDependenciesGuide')}</a>
            </div>
          )}
          {isLocal === false && (
            <div className="mb-4 p-3 bg-amber-50 dark:bg-amber-900/20 rounded border border-amber-200 dark:border-amber-800 text-sm text-amber-700 dark:text-amber-300">
              {t('remoteSettingsHint')}
            </div>
          )}
          {schemaError && (
            <div className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
              {t('settingsSchemaLoadFailed')}
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
                fieldsByKey.ASR_MODEL_PATH && <SettingsField field={fieldsByKey.ASR_MODEL_PATH} {...fieldProps} />
              )}
              {asrMode === 'remote' && (
                <div className="space-y-4">
                  {fieldsByKey.ASR_API_URL && <SettingsField field={fieldsByKey.ASR_API_URL} {...fieldProps} />}
                  {fieldsByKey.ASR_REMOTE_MODEL && <SettingsField field={fieldsByKey.ASR_REMOTE_MODEL} {...fieldProps} />}
                  {asrModelInfo && (
                    <p className={`text-xs ${asrModelInfo.isValid ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
                      {asrModelInfo.message}
                    </p>
                  )}
                </div>
              )}
            </div>

            {isLocal && config.TTS_MODE === 'qwen' && (
              <div className="md:col-span-2 space-y-3 rounded-lg border border-slate-300 bg-slate-50 p-4 dark:border-slate-600 dark:bg-slate-700/30">
                <div>
                  <h3 className={`text-sm font-semibold ${textMain}`}>{t('qwenPrivateRuntimeInstall')}</h3>
                  <p className={`mt-1 text-xs ${textMuted}`}>{t('qwenPrivateRuntimeInstallHint')}</p>
                </div>
                {qwenInstallStatus?.installed && qwenInstallStatus?.managed && (
                  <p role="status" className="rounded border border-green-300 bg-green-50 p-2 text-sm text-green-800 dark:border-green-700 dark:bg-green-900/20 dark:text-green-200">
                    {t('qwenCurrentlyInstalled')}: {qwenInstallStatus.runtime_version} · {qwenInstallStatus.device?.toUpperCase()} · {gib(qwenInstallStatus.installed_bytes)} GiB
                  </p>
                )}
                {qwenInstallStatus?.installed && !qwenInstallStatus?.managed && (
                  <p role="alert" className="rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                    {t('qwenLegacyRuntimeDetected')}
                  </p>
                )}
                <QwenRuntimeStatus status={qwenRuntimeStatus} t={t} className={`text-sm ${textMuted}`} />
                {qwenInstallStatus?.managed && !qwenRepairOpen && (
                  <button type="button" onClick={() => setQwenRepairOpen(true)} className="rounded border border-slate-300 px-3 py-2 text-sm text-slate-700 dark:border-slate-600 dark:text-slate-200">
                    {t('qwenRepairRuntime')}
                  </button>
                )}
                {qwenInstallStatus !== null && (!qwenInstallStatus.managed || qwenRepairOpen) && <>
                {qwenInstallStatus.upstream_sources && <p className={`text-xs ${textMuted}`}>{t('qwenSourcesAutofilled')}</p>}
                {[
                  ['llama_archive', qwenInstall.device === 'vulkan' ? 'qwenVulkanArchive' : 'qwenLlamaArchive', qwenInstall.device === 'vulkan' ? 'C:\\Downloads\\llama-b10792-bin-win-vulkan-x64.zip' : 'C:\\Downloads\\llama-b10792-bin-win-cuda-12.4-x64.zip'],
                  ...(qwenNeedsCudaArchive ? [['cuda_archive', 'qwenCudaArchive', 'C:\\Downloads\\cudart-llama-bin-win-cuda-12.4-x64.zip']] : []),
                  ['model_directory', 'qwenModelDirectory', 'C:\\Users\\name\\.lmstudio\\models\\...'],
                ].map(([key, label, placeholder]) => (
                  <label key={key} className={`block text-xs ${textMain}`}>
                    <span className="mb-1 block font-medium">{t(label)}</span>
                    <input
                      type="text"
                      value={qwenInstall[key]}
                      placeholder={placeholder}
                      disabled={qwenInstalling}
                      onChange={event => updateQwenInstall(key, event.target.value)}
                      className={`w-full rounded border px-3 py-2 text-sm ${inputBg} ${inputBorder}`}
                    />
                  </label>
                ))}
                <label className={`block text-xs ${textMain}`}>
                  <span className="mb-1 block font-medium">{t('qwenInstallDevice')}</span>
                  <select value={qwenInstall.device} disabled={qwenInstalling}
                    onChange={event => updateQwenInstall('device', event.target.value)}
                    className={`rounded border px-3 py-2 text-sm ${inputBg} ${inputBorder}`}>
                    <option value="cuda">NVIDIA CUDA</option>
                    <option value="vulkan">AMD Vulkan</option>
                    <option value="cpu">CPU</option>
                  </select>
                </label>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={handleQwenPreflight}
                    disabled={qwenPreflighting || qwenInstalling || !qwenSourcesReady}
                    aria-busy={qwenPreflighting}
                    className="rounded border border-blue-600 px-3 py-2 text-sm text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60 dark:text-blue-300 dark:hover:bg-slate-700">
                    {t(qwenPreflighting ? 'qwenPreflighting' : 'qwenPreflight')}
                  </button>
                  <button type="button" onClick={handleQwenInstall}
                    disabled={qwenInstalling || !qwenPreflight?.ready}
                    aria-busy={qwenInstalling}
                    className="rounded bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60">
                    {t(qwenInstalling ? 'qwenInstalling' : 'qwenInstall')}
                  </button>
                </div>
                {qwenPreflight && (
                  <p role={qwenPreflight.ready ? 'status' : 'alert'} className={`text-sm ${qwenPreflight.ready ? 'text-green-700 dark:text-green-300' : 'text-amber-700 dark:text-amber-300'}`}>
                    {t(qwenPreflight.ready ? 'qwenPreflightReady' : 'qwenPreflightNoSpace')}: {gib(qwenPreflight.required_bytes)} GiB / {gib(qwenPreflight.free_bytes)} GiB
                  </p>
                )}
                {qwenInstallResult?.installed && (
                  <p role="status" className="text-sm text-green-700 dark:text-green-300">
                    {t('qwenInstallReady')}: {qwenInstallResult.model} · {qwenInstallResult.device.toUpperCase()}
                  </p>
                )}
                </>}
              </div>
            )}

            {schema?.sections.map(section => (
              <SettingsSection key={section.id} section={section} t={t}>
                {visibleFields.filter(field => field.section === section.id).map(field => (
                  <SettingsField key={field.key} field={field} {...fieldProps} />
                ))}
                {section.id === 'translation' && config.LLM_PROVIDER === 'lm_studio' && <>
                  {lmStudioRuntimeReady === false && (
                    <div role="alert" className="md:col-span-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                      {t('lmStudioRuntimeMissing')}
                    </div>
                  )}
                  {lmStudioStatusError && (
                    <div role="alert" className="md:col-span-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                      {t('lmStudioDiagnosticsUnavailable')}
                    </div>
                  )}
                  <div className="md:col-span-2">
                    <button type="button" disabled={fetchingModels} aria-busy={fetchingModels} aria-live="polite"
                      onClick={() => setLmStudioRefreshToken(token => token + 1)}
                      className="rounded border border-slate-300 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700">
                      {fetchingModels ? t('detecting') : t('refreshLmStudioDiagnostics')}
                    </button>
                  </div>
                </>}
              </SettingsSection>
            ))}
            {advancedFieldCount > 0 && (
              <div className="md:col-span-2">
                <button type="button" aria-expanded={showAdvanced} onClick={() => setShowAdvanced(value => !value)} className={`rounded border px-3 py-2 text-sm ${inputBorder} ${inputBg} ${textMain}`}>
                  {t(showAdvanced ? 'hideAdvancedSettings' : 'showAdvancedSettings')} ({advancedFieldCount})
                </button>
              </div>
            )}
          </div>
          </ServicesSection>

          <AboutSection edition={capabilities?.edition || 'standard'} t={t} />

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
