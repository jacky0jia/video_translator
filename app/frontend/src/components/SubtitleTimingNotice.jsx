import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../contexts/I18nContext';

const ACTIVE = new Set(['pending', 'uploading', 'downloading', 'extracting_audio', 'transcribing',
  'translating', 'waiting_for_gpu', 'switching_model', 'pipeline_starting', 'pipeline_transcribing',
  'pipeline_translating', 'pipeline_dubbing', 'pipeline_rendering', 'burning', 'dubbing']);

export default function SubtitleTimingNotice({ task, blocked, acquireUndo, onTaskRefresh, onJump }) {
  const { t } = useI18n();
  const summary = task?.subtitle_timing_summary;
  const scope = useMemo(() => ({}), [task?.task_id, task?.subtitle_timing_active]);
  const current = useRef(null);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  useLayoutEffect(() => {
    current.current = scope;
    return () => { current.current = null; };
  }, [scope]);
  const busy = task?.pipeline_cleanup_pending || ACTIVE.has(task?.status)
    || ['processing', 'cancelling', 'awaiting_compression'].includes(task?.pipeline_status)
    || task?.burn_status === 'processing' || task?.dubbing_status === 'processing';

  const undo = async () => {
    if (blocked || busy || pending === scope || current.current !== scope) return;
    const release = acquireUndo(task.task_id);
    if (!release) return;
    setPending(scope); setError(null);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api/tasks/${task.task_id}/subtitle-timing`, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('unavailable');
      const analysis = await response.json();
      if (current.current !== scope) return;
      if (analysis.active_version !== task.subtitle_timing_active) throw new Error('changed');
      const saved = await fetch(`/api/tasks/${task.task_id}/subtitle-timing/undo`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ state_revision: analysis.state_revision, version_id: analysis.active_version }),
      });
      if (!saved.ok) throw new Error(saved.status === 409 ? 'changed' : 'unavailable');
      await onTaskRefresh?.(task.task_id);
    } catch (failure) {
      if (current.current === scope) setError({ scope, key: failure.message === 'changed' ? 'timingUndoChanged' : 'timingUndoFailed' });
    } finally {
      clearTimeout(timer); release();
      if (current.current === scope) setPending(null);
    }
  };

  if (!summary || (!summary.changed_rows?.length && !summary.remaining_overlaps && summary.status !== 'undone')) return null;
  return <div data-testid="subtitle-timing-notice" role="note" className="mb-3 rounded-lg border border-blue-300 bg-blue-50 px-3 py-2 text-sm text-blue-950 dark:border-blue-700 dark:bg-blue-950/40 dark:text-blue-100">
    <p>{summary.status === 'undone' ? t('timingUndone') : `${t('timingRepaired')} ${summary.changed_rows?.length || 0}`}</p>
    {summary.status !== 'undone' && summary.changed_rows?.length > 0 && <p className="mt-1 text-xs">{t('timingRepairBounds')}</p>}
    {summary.status !== 'undone' && summary.remaining_overlaps > 0 && <>
      <p className="mt-1">{t('timingRemaining')} {summary.remaining_overlaps}</p>
      <p className="text-xs">{t('timingRemainingHint')}</p>
      <div className="mt-1 flex flex-wrap gap-2">{summary.remaining_rows?.map(row => <button key={row} type="button" className="underline" onClick={() => onJump(row)}>#{row}</button>)}</div>
    </>}
    {task.subtitle_timing_active && <button type="button" onClick={undo} disabled={blocked || busy || pending === scope} className="mt-2 rounded border border-blue-500 px-2 py-1 disabled:opacity-50">
      {pending === scope ? t('timingUndoPending') : t('timingUndo')}
    </button>}
    {error?.scope === scope && <p role="alert" className="mt-2">{t(error.key)}</p>}
  </div>;
}
