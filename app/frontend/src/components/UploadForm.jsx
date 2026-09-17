import { useEffect, useState } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useI18n } from '../contexts/I18nContext';
import ProgressBar from './ProgressBar';

export default function UploadForm({ onUpload, onUploadStart }) {
  const showToast = useToast();
  const { t } = useI18n();
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [targetLang, setTargetLang] = useState('Chinese');
  const [supportedLanguages, setSupportedLanguages] = useState(['Chinese', 'English', 'Japanese']);

  useEffect(() => {
    let active = true;
    let controller;
    const loadLanguages = () => {
      controller?.abort();
      controller = new AbortController();
      return fetch('/api/dubbing/voices', { cache: 'no-store', signal: controller.signal })
      .then(response => response.ok ? response.json() : Promise.reject(new Error('voices')))
      .then(data => {
        if (!active || !Array.isArray(data.supported_languages) || !data.supported_languages.length) return;
        setSupportedLanguages(data.supported_languages);
        setTargetLang(current => data.supported_languages.includes(current) ? current : data.supported_languages[0]);
      })
      .catch(error => { if (error?.name !== 'AbortError') return undefined; });
    };
    loadLanguages();
    window.addEventListener('settings-changed', loadLanguages);
    return () => {
      active = false;
      controller?.abort();
      window.removeEventListener('settings-changed', loadLanguages);
    };
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) return;
    setLoading(true);
    setUploadProgress(0);
    onUploadStart?.(file.name, targetLang);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('target_lang', targetLang);

    try {
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/transcribe');
        xhr.timeout = 600000; // 10 minutes total timeout

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setUploadProgress(Math.round((event.loaded / event.total) * 100));
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText));
            } catch (e) {
              reject(new Error(t('invalidServerResponse')));
            }
          } else {
            let err = {};
            try { err = JSON.parse(xhr.responseText); } catch {}
            reject(new Error(err.detail || `HTTP ${xhr.status}`));
          }
        };

        xhr.onerror = () => reject(new Error(t('networkError')));
        xhr.ontimeout = () => reject(new Error(t('uploadTimeout')));
        xhr.send(formData);
      });

      showToast(t('taskStarted'), 'success');
      onUpload?.(data.task_id);
    } catch (err) {
      showToast(t('uploadFailed') + ': ' + err.message, 'error');
    } finally {
      setLoading(false);
      setUploadProgress(0);
    }
  };

  return (
    <section className="app-panel p-4">
      <h2 className="app-panel-title mb-3">{t('uploadVideo')}</h2>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="block text-sm font-medium mb-1">{t('videoFile')}</label>
          <div className="flex items-center gap-2">
            <label className="shrink-0 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded text-sm font-medium cursor-pointer transition">
              {t('chooseFile')}
              <input
                type="file"
                accept="video/*"
                onChange={e => setFile(e.target.files[0])}
                className="hidden"
              />
            </label>
            <span className="text-sm text-slate-500 dark:text-slate-400 truncate">
              {file ? file.name : t('noFileChosen')}
            </span>
          </div>
        </div>
        <div>
          <label htmlFor="upload-target-language" className="block text-sm font-medium mb-1">{t('targetLanguage')}</label>
          <select
            id="upload-target-language"
            value={targetLang}
            onChange={e => setTargetLang(e.target.value)}
            disabled={loading}
            className="app-input w-full"
          >
            {supportedLanguages.map(language => <option key={language} value={language}>{language}</option>)}
          </select>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{t('taskTargetHint')}</p>
        </div>
        {loading && (
          <div className="mt-1">
            <ProgressBar
              percent={uploadProgress}
              message={t('uploading')}
              status="uploading"
            />
          </div>
        )}
        <button type="submit" disabled={loading}
          className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg font-medium transition duration-200">
          {loading ? `${t('uploading')} ${uploadProgress > 0 ? uploadProgress + '%' : ''}` : t('upload')}
        </button>
      </form>
    </section>
  );
}
