import { useEffect, useRef } from 'react';
import { acceptTaskState } from '../utils/taskStateVersion';

export function useTaskSSE(taskId, onEvent, knownRevision) {
  const onEventRef = useRef({ taskId, onEvent });
  onEventRef.current = { taskId, onEvent };
  acceptTaskState(taskId, { state_revision: knownRevision });

  useEffect(() => {
    if (!taskId) return;
    let source;
    let retryTimeout;
    let closed = false;

    const connect = () => {
      if (closed) return;
      const connection = new EventSource(`/api/tasks/${taskId}/events`);
      source = connection;
      const isCurrent = () => !closed && source === connection && onEventRef.current.taskId === taskId;
      connection.onmessage = (e) => {
        if (!isCurrent()) return;
        try {
          const data = JSON.parse(e.data);
          if (!acceptTaskState(taskId, data)) return;
          onEventRef.current.onEvent?.(data);
        } catch {
          // ignore parse errors
        }
      };
      connection.addEventListener('not_found', () => {
        if (!isCurrent()) return;
        closed = true;
        connection.close();
        clearTimeout(retryTimeout);
      });
      connection.onerror = () => {
        if (!isCurrent()) return;
        connection.close();
        source = null;
        if (!closed) {
          retryTimeout = setTimeout(connect, 1000);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      clearTimeout(retryTimeout);
      source?.close();
    };
  }, [taskId]);
}
