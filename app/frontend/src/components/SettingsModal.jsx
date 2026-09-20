import { useState, useEffect, useRef, useCallback } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import { useTheme } from '../contexts/ThemeContext';
import SettingsField from './settings/SettingsField';
import SettingsLayout from './settings/SettingsLayout';
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
  const [activeSection, setActiveSection] = useState('transcription');
  const [advancedSections, setAdvancedSections] = useState({});
  const [qwenSetupOpen, setQwenSetupOpen] = useState(false);
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
  const hiddenKeys = new Set(['ASR_MODEL_PATH', 'ASR_API_URL', 'ASR_REMOTE_MODEL', 'UI_LANGUAGE', 'TTS_MODE', 'TTS_API_URL', 'TTS_API_KEY', 'TTS_MODEL', 'TTS_DEFAULT_VOICE', 'TTS_SPEED']);
  const sectionFields = (section, advanced = false) => (schema?.fields || []).filter(field =>
    field.section === section && !hiddenKeys.has(field.key)
    && (field.level === 'advanced') === advanced
    && fieldIsVisible(field, { ...config, ASR_MODE: asrMode })
  );
  const renderFields = (fields) => fields.map(field => <SettingsField key={field.key} field={field} {...fieldProps} />);
  const renderAdvanced = (section, exclude = []) => {
    const fields = sectionFields(section, true).filter(field => !exclude.includes(field.key));
    if (!fields.length) return null;
    return <details className="settings-details" open={Boolean(advancedSections[section])} onToggle={event => setAdvancedSections(current => ({ ...current, [section]: event.currentTarget.open }))}>
      <summary>{t('advancedSettings')}</summary><div className="settings-grid">{renderFields(fields)}</div>
    </details>;
  };
  const qwenNeedsCudaArchive = qwenInstall.device !== 'vulkan';
  const qwenSourcesReady = Boolean(
    qwenInstall.llama_archive
    && qwenInstall.model_directory
    && (!qwenNeedsCudaArchive || qwenInstall.cuda_archive)
  );

  return <SettingsLayout {...{
    t, lang, setLanguage, theme, setTheme, onClose, overlayPointerDownRef, handleSubmit,
    hardware, isLocal, upstreamDependencies, schemaError, activeSection, setActiveSection,
    inputBg, inputBorder, textMain, textMuted, asrMode, handleAsrModeChange, fieldsByKey,
    fieldProps, asrModelInfo, config, handleChange, renderFields, sectionFields, renderAdvanced,
    lmStudioRuntimeReady, lmStudioStatusError, fetchingModels, setLmStudioRefreshToken,
    qwenInstallStatus, qwenRuntimeStatus, qwenSetupOpen, setQwenSetupOpen, qwenRepairOpen,
    setQwenRepairOpen, qwenInstall, qwenInstalling, qwenPreflighting, qwenPreflight,
    qwenInstallResult, qwenSourcesReady, qwenNeedsCudaArchive, updateQwenInstall,
    handleQwenPreflight, handleQwenInstall, gib,
  }} />;
}
