import { useCallback, useEffect, useState } from 'react';
import UploadForm from './components/UploadForm';
import HistoryList from './components/HistoryList';
import VideoPreview from './components/VideoPreview';
import ControlPanel from './components/ControlPanel';
import ExportPanel from './components/ExportPanel';
import SettingsModal from './components/SettingsModal';
import AppLogo from './components/AppLogo';
import { useTaskSSE } from './hooks/useTaskSSE';
import { useToast } from './contexts/ToastContext';
import { useI18n } from './contexts/I18nContext';
import { useTheme } from './contexts/ThemeContext';

const ACTIVE_STATUSES = new Set([
  'pending', 'extracting_audio', 'waiting_for_gpu', 'switching_model', 'transcribing',
  'translating', 'pipeline_transcribing', 'pipeline_translating', 'pipeline_translated',
  'pipeline_dubbing', 'burning', 'dubbing',
]);

function StatusDot({ status }) {
  const color = status === 'completed' || status === 'transcribed'
    ? 'bg-emerald-500'
    : status === 'failed' || status === 'translation_failed'
      ? 'bg-red-500'
      : ACTIVE_STATUSES.has(status) ? 'bg-indigo-500' : 'bg-slate-400';
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} />;
}

function IconButton({ label, onClick, children }) {
  return <button type="button" onClick={onClick} className="app-icon-button" aria-label={label} title={label}>{children}</button>;
}

export default function App() {
  const showToast = useToast();
  const { t, lang, setLanguage } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const [tasks, setTasks] = useState([]);
  const [currentTask, setCurrentTask] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [listeningTaskId, setListeningTaskId] = useState(null);
  const [highlightTaskId, setHighlightTaskId] = useState(null);
  const [mobilePage, setMobilePage] = useState('preview');
  const [subtitleStyle, setSubtitleStyle] = useState({ offsetY: 0, fontSize: 18, sourceColor: '#FFFFFF', targetColor: '#FDE047', bold: false, fontFamily: 'Arial' });
  const [showSource, setShowSource] = useState(true);
  const [showTarget, setShowTarget] = useState(true);

  const fetchTasks = useCallback(async () => {
    try {
      const response = await fetch('/api/tasks');
      const data = await response.json();
      const next = (data.tasks || []).sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      setTasks(next);
      setCurrentTask(previous => {
        if (!previous) return previous;
        return next.find(item => item.task_id === previous.task_id) || previous;
      });
    } catch (error) {
      console.error('Failed to fetch tasks', error);
    }
  }, []);

  const loadTask = useCallback(async taskId => {
    try {
      const response = await fetch(`/api/tasks/${taskId}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to load task');
      setCurrentTask(data.task);
      setHighlightTaskId(null);
      setShowSource(Boolean(data.task?.transcription_path));
      setShowTarget(true);
      setMobilePage('preview');
    } catch (error) {
      showToast(error.message, 'error');
    }
  }, [showToast]);

  const refreshTask = useCallback(async taskId => {
    await fetchTasks();
    if (taskId) await loadTask(taskId);
  }, [fetchTasks, loadTask]);

  useTaskSSE(listeningTaskId, event => {
    setTasks(previous => previous.map(item => item.task_id === listeningTaskId ? { ...item, ...event } : item));
    setCurrentTask(previous => previous?.task_id === listeningTaskId ? { ...previous, ...event } : previous);
    if (['transcribed', 'completed', 'failed', 'translation_failed', 'cancelled'].includes(event.status)) {
      setListeningTaskId(null);
      refreshTask(listeningTaskId);
    }
  });

  useEffect(() => { fetchTasks(); }, [fetchTasks]);

  const retryTask = async taskId => {
    const response = await fetch(`/api/tasks/${taskId}/retry`, { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.detail || 'Retry failed', 'error');
      return;
    }
    setListeningTaskId(taskId);
    setHighlightTaskId(taskId);
    await fetchTasks();
  };

  const pageVisible = page => mobilePage === page ? 'block' : 'hidden md:block';
  const rightVisible = mobilePage === 'process' || mobilePage === 'export' ? 'flex' : 'hidden md:flex';

  return (
    <div className={theme === 'dark' ? 'dark' : ''}>
      <div className="app-page">
        <div className="app-window">
          <header className="app-topbar">
            <AppLogo />
            <div className="app-current-task min-w-0">
              <span className="app-current-label">{t('workspaceLabel')}</span>
              <strong className="block truncate text-sm font-semibold sm:text-base">{currentTask?.filename || t('workspaceTitle')}</strong>
              <span className="flex items-center gap-1.5 truncate text-xs text-slate-500 dark:text-slate-400"><StatusDot status={currentTask?.status} />{currentTask ? `${currentTask.status || 'ready'} · ${currentTask.target_lang || t('legacyTask')}` : t('workspaceHint')}</span>
            </div>
            <div className="flex items-center gap-2">
              <IconButton label={theme === 'dark' ? 'Use light theme' : 'Use dark theme'} onClick={toggleTheme}>
                {theme === 'dark' ? <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg> : <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.4 15.4A9 9 0 0 1 8.6 3.6 9 9 0 1 0 20.4 15.4Z"/></svg>}
              </IconButton>
              <button type="button" onClick={() => setLanguage(lang === 'zh' ? 'en' : 'zh')} className="app-icon-button text-xs font-semibold" aria-label="Switch interface language">{lang === 'zh' ? 'EN' : '中'}</button>
              <IconButton label="Settings" onClick={() => setSettingsOpen(true)}><svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg></IconButton>
            </div>
          </header>

          <main className="app-layout">
            <aside className={`${pageVisible('tasks')} min-w-0 space-y-3 md:max-h-[calc(100vh-7.5rem)] md:overflow-y-auto`}>
              <UploadForm onUploadStart={(filename, targetLang) => {
                const placeholder = { task_id: `pending-${Date.now()}`, filename, target_lang: targetLang, status: 'uploading', progress_percent: 0, created_at: new Date().toISOString() };
                setTasks(previous => [placeholder, ...previous]);
                setCurrentTask(placeholder);
                setHighlightTaskId(placeholder.task_id);
              }} onUpload={taskId => { setListeningTaskId(taskId); setHighlightTaskId(taskId); fetchTasks().then(() => loadTask(taskId)); }} />
              <HistoryList tasks={tasks} onSelect={loadTask} onRefresh={fetchTasks} onRetry={retryTask} onDelete={ids => { setTasks(previous => previous.filter(item => !ids.includes(item.task_id))); if (ids.includes(currentTask?.task_id)) setCurrentTask(null); }} onDeleteAll={() => { setTasks([]); setCurrentTask(null); }} highlightTaskId={highlightTaskId} selectedTaskId={currentTask?.task_id} />
            </aside>

            <section className={`${pageVisible('preview')} min-w-0`}><VideoPreview task={currentTask} subtitleStyle={subtitleStyle} onSubtitleStyleChange={setSubtitleStyle} showSource={showSource} showTarget={showTarget} onShowSourceChange={setShowSource} onShowTargetChange={setShowTarget} /></section>

            <aside className={`${rightVisible} min-w-0 flex-col gap-3 md:max-h-[calc(100vh-7.5rem)] md:overflow-y-auto`}>
              <div className={mobilePage === 'export' ? 'hidden md:block' : 'block'}><ControlPanel task={currentTask} onTaskRefresh={refreshTask} subtitleStyle={subtitleStyle} showSource={showSource} showTarget={showTarget} onNavigate={setMobilePage} /></div>
              <div className={mobilePage === 'process' ? 'hidden md:block' : 'block'}><ExportPanel task={currentTask} onNavigate={setMobilePage} /></div>
            </aside>
          </main>

          <nav className="app-mobile-nav" aria-label="Main navigation">{[
            ['tasks', 'navTasks', 'M4 5h16M4 12h16M4 19h16'], ['preview', 'navPreview', 'M3 5h18v14H3zM9 9l6 3-6 3z'], ['process', 'navProcess', 'M12 3v18M3 12h18'], ['export', 'navExport', 'M12 3v12m0 0 4-4m-4 4-4-4M5 20h14'],
          ].map(([page, labelKey, path]) => <button key={page} type="button" onClick={() => setMobilePage(page)} aria-current={mobilePage === page ? 'page' : undefined}><svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d={path}/></svg><span>{t(labelKey)}</span></button>)}</nav>
        </div>
        {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
      </div>
    </div>
  );
}
