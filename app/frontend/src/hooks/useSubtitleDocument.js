import { useCallback, useEffect, useState } from 'react';
import { fetchSubtitleDocument } from '../utils/subtitleDocument';
const EMPTY_SEGMENTS = Object.freeze([]);

export function useSubtitleDocument(taskId, path, refreshKey) {
  const scope = JSON.stringify([taskId || '', path || '']);
  const [snapshot, setSnapshot] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!taskId || !path) return;
    let current = true;
    const controller = new AbortController();
    setSnapshot(previous => ({ scope, status: 'loading', data: previous?.scope === scope ? previous.data : null }));
    fetchSubtitleDocument(path, { signal: controller.signal }).then(data => {
      if (current) setSnapshot({ scope, status: 'ready', data });
    }).catch(() => {
      // Retain same-document content on refresh failure, but mark it read-only.
      if (current) setSnapshot(previous => ({ scope, status: 'error', data: previous?.scope === scope ? previous.data : null }));
    });
    return () => { current = false; controller.abort(); };
  }, [taskId, path, scope, refreshKey, attempt]);
  const reload = useCallback(() => setAttempt(value => value + 1), []);
  const setSegments = useCallback(updater => setSnapshot(previous => {
    if (previous?.scope !== scope || previous.status !== 'ready') return previous;
    const segments = typeof updater === 'function' ? updater(previous.data.segments) : updater;
    return { ...previous, data: { ...previous.data, segments } };
  }), [scope]);
  const owned = snapshot?.scope === scope;
  const status = !taskId || !path ? 'missing' : owned ? snapshot.status : 'loading';
  const data = owned && path ? snapshot.data : null;
  return { status, data, segments: data?.segments || EMPTY_SEGMENTS, setSegments, reload };
}
