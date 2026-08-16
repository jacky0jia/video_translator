import { useEffect, useRef, useState } from 'react';
import { useI18n } from '../contexts/I18nContext';
import { useToast } from '../contexts/ToastContext';

export default function HistoryList({ tasks, onSelect, onRefresh, onDelete, onDeleteAll, onRetry, highlightTaskId, selectedTaskId }) {
  const { t } = useI18n();
  const showToast = useToast();
  const itemRefs = useRef({});
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [confirm, setConfirm] = useState(null); // { type: 'single' | 'batch' | 'all', taskId? }

  const doDelete = (taskId) => {
    fetch(`/api/tasks/${taskId}`, { method: 'DELETE' })
      .then(r => {
        if (r.ok) {
          onDelete([taskId]);
        } else {
          showToast(t('deleteFailed'), 'error');
        }
      })
      .catch(() => showToast(t('deleteFailed'), 'error'));
  };

  const doBatchDelete = (ids) => {
    fetch('/api/tasks/batch-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_ids: Array.from(ids) }),
    })
      .then(r => {
        if (r.ok) {
          onDelete(Array.from(ids));
        } else {
          showToast(t('deleteFailed'), 'error');
        }
      })
      .catch(() => showToast(t('deleteFailed'), 'error'));
  };

  const doDeleteAll = () => {
    fetch('/api/tasks', { method: 'DELETE' })
      .then(r => {
        if (r.ok) {
          onDeleteAll();
        } else {
          showToast(t('deleteFailed'), 'error');
        }
      })
      .catch(() => showToast(t('deleteFailed'), 'error'));
  };

  const handleTrashClick = (e, taskId) => {
    e.stopPropagation();
    setConfirm({ type: 'single', taskId });
  };

  const toggleSelect = (e, taskId) => {
    e.stopPropagation();
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) {
        next.delete(taskId);
      } else {
        next.add(taskId);
      }
      return next;
    });
  };

  useEffect(() => {
    if (highlightTaskId && itemRefs.current[highlightTaskId]) {
      itemRefs.current[highlightTaskId].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightTaskId]);

  return (
    <section className="app-panel p-4">
      <h2 className="app-panel-title mb-3">{t('tasks')}</h2>
      <div className="space-y-2 max-h-[34rem] overflow-y-auto pr-1">
        {tasks.length === 0 ? (
          <p className="text-slate-400 dark:text-slate-500 text-center py-4">{t('noTasksFound')}</p>
        ) : (
          tasks.map(task => {
            const isHighlighted = task.task_id === highlightTaskId;
            const isSelected = task.task_id === selectedTaskId;
            const isChecked = selectedIds.has(task.task_id);
            const isPlaceholder = String(task.task_id).startsWith('pending-');
            return (
              <div
                key={task.task_id}
                ref={el => { itemRefs.current[task.task_id] = el; }}
                onClick={() => !isPlaceholder && onSelect(task.task_id)}
                className={`min-w-0 max-w-full overflow-hidden rounded-xl border p-3 transition ${isPlaceholder ? 'opacity-70 cursor-default' : 'cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800'} ${isHighlighted ? 'ring-2 ring-indigo-500' : ''} ${isSelected ? 'border-indigo-500 bg-indigo-50/70 dark:bg-indigo-950/30' : 'border-transparent bg-slate-50 dark:bg-slate-900/35'}`}>
                <div className="flex items-start gap-2">
                  {!isPlaceholder && (
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onClick={e => e.stopPropagation()}
                      onChange={e => toggleSelect(e, task.task_id)}
                      className="mt-1 shrink-0 w-4 h-4 accent-blue-500 cursor-pointer"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between items-start gap-2">
                      <span className="min-w-0 overflow-hidden text-ellipsis break-all text-sm font-medium text-slate-900 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] dark:text-white">{task.filename}</span>
                      <div className="flex items-center gap-1 shrink-0">
                          {onRetry && (
                          <button
                            onClick={e => { e.stopPropagation(); onRetry(task.task_id); }}
                            className="text-slate-400 hover:text-blue-500 dark:hover:text-blue-400 transition p-1"
                            title={t('retranscribe')}
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                            </svg>
                          </button>
                        )}
                        {!isPlaceholder && (
                          <button
                            onClick={e => handleTrashClick(e, task.task_id)}
                            className="text-slate-400 hover:text-red-500 dark:hover:text-red-400 transition p-1"
                            title={t('deleteTask')}
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                      {task.created_at?.split('T')[0]}
                      {(task.target_lang || task.language) && (
                        <span className="ml-2 rounded-full bg-indigo-100 px-2 py-0.5 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">{task.target_lang || task.language}</span>
                      )}
                    </div>
                    {['uploading','pending','extracting_audio','waiting_for_gpu','switching_model','transcribing','translating','pipeline_transcribing','pipeline_translating','pipeline_dubbing'].includes(task.status) && (
                      <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"><div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${Math.max(4, task.progress_percent || 0)}%` }} /></div>
                    )}
                    {(task.status === 'failed' || task.status === 'translation_failed' || task.status === 'cancelled') && task.message && (
                      <div className="text-xs text-red-500 dark:text-red-400 mt-1 truncate" title={task.message}>
                        {task.message}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
      <div className="flex gap-2 mt-3">
        <button onClick={onRefresh}
          className="flex-1 py-2 text-sm text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition border border-gray-200 dark:border-slate-600 rounded">
          {t('refreshList')}
        </button>
        <button
          onClick={() => {
            if (selectedIds.size > 0) setConfirm({ type: 'batch' });
          }}
          disabled={selectedIds.size === 0}
          className={`flex-1 py-2 text-sm rounded border transition ${selectedIds.size > 0 ? 'text-red-500 hover:text-red-600 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20' : 'text-slate-300 dark:text-slate-600 border-gray-100 dark:border-slate-700 cursor-not-allowed'}`}
        >
          {t('deleteSelected')}
        </button>
        <button
          onClick={() => setConfirm({ type: 'all' })}
          disabled={tasks.length === 0}
          className={`flex-1 py-2 text-sm rounded border transition ${tasks.length > 0 ? 'text-red-500 hover:text-red-600 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20' : 'text-slate-300 dark:text-slate-600 border-gray-100 dark:border-slate-700 cursor-not-allowed'}`}
        >
          {t('deleteAll')}
        </button>
      </div>

      {/* Custom confirm dialog */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-slate-800 p-5 rounded-xl shadow-xl border border-gray-200 dark:border-slate-700 max-w-sm w-full mx-4">
            <h3 className="text-base font-semibold mb-3 text-slate-900 dark:text-white">
              {confirm.type === 'all' ? t('confirmDeleteAll') : confirm.type === 'batch' ? t('confirmDeleteSelected') : t('confirmDeleteTask')}
            </h3>
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setConfirm(null)}
                className="px-4 py-2 text-sm rounded border border-gray-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-gray-100 dark:hover:bg-slate-700 transition"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => {
                  if (confirm.type === 'all') {
                    doDeleteAll();
                  } else if (confirm.type === 'batch') {
                    doBatchDelete(selectedIds);
                    setSelectedIds(new Set());
                  } else {
                    doDelete(confirm.taskId);
                  }
                  setConfirm(null);
                }}
                className="px-4 py-2 text-sm rounded bg-red-500 hover:bg-red-600 text-white transition"
              >
                {t('confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
