import { useCallback, useEffect, useState } from 'react';

export default function PathPickerDialog({ picker, onCancel, onSelect, t }) {
  const [currentPath, setCurrentPath] = useState('');
  const [parentPath, setParentPath] = useState(null);
  const [entries, setEntries] = useState([]);
  const [pathInput, setPathInput] = useState(picker.initialPath || '');
  const [selectedPath, setSelectedPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadPath = useCallback(async path => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/fs/browse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, mode: picker.mode }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || t('pathBrowserError'));
      setCurrentPath(data.current_path);
      setParentPath(data.parent_path);
      setEntries(data.entries || []);
      setPathInput(data.current_path);
      setSelectedPath('');
    } catch (loadError) {
      setError(loadError.message || t('pathBrowserError'));
    } finally {
      setLoading(false);
    }
  }, [picker.mode, t]);

  useEffect(() => { loadPath(picker.initialPath || ''); }, [loadPath, picker.initialPath]);

  const choose = () => {
    const path = picker.mode === 'folder' ? currentPath : selectedPath;
    if (path) onSelect(path);
  };

  return <div className="settings-path-overlay" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onCancel();
  }}>
    <section className="settings-path-dialog" role="dialog" aria-modal="true" aria-labelledby="path-picker-title">
      <header>
        <div><p className="settings-eyebrow">VIDEO TRANSLATOR</p><h3 id="path-picker-title">{t(picker.mode === 'folder' ? 'chooseFolder' : 'chooseFile')}</h3></div>
        <button type="button" className="settings-close" aria-label={t('cancel')} onClick={onCancel}>×</button>
      </header>
      <div className="settings-path-toolbar">
        <button type="button" className="settings-secondary" disabled={!parentPath || loading} onClick={() => loadPath(parentPath)}>{t('pathBrowserUp')}</button>
        <form onSubmit={event => { event.preventDefault(); loadPath(pathInput); }}>
          <input className="app-input" aria-label={t('currentFolder')} value={pathInput} onChange={event => setPathInput(event.target.value)} />
          <button type="submit" className="settings-secondary" disabled={loading}>{t('pathBrowserGo')}</button>
        </form>
      </div>
      {error && <p className="settings-alert" role="alert">{error}</p>}
      <div className="settings-path-list" aria-busy={loading}>
        {loading ? <p className="settings-path-empty">{t('loading')}</p> : entries.length ? entries.map(entry =>
          <button
            type="button"
            key={entry.path}
            className={`settings-path-entry ${selectedPath === entry.path ? 'is-selected' : ''}`}
            onClick={() => entry.is_directory ? loadPath(entry.path) : setSelectedPath(entry.path)}
          >
            <span aria-hidden="true">{entry.is_directory ? '▸' : '•'}</span>
            <span>{entry.name}</span>
          </button>
        ) : <p className="settings-path-empty">{t('pathBrowserEmpty')}</p>}
      </div>
      <footer>
        <button type="button" className="settings-secondary" onClick={onCancel}>{t('cancel')}</button>
        <button type="button" className="settings-primary" disabled={picker.mode === 'file' && !selectedPath} onClick={choose}>
          {t(picker.mode === 'folder' ? 'useThisFolder' : 'useSelectedFile')}
        </button>
      </footer>
    </section>
  </div>;
}
