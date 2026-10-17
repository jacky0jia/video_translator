import { useEffect, useRef } from 'react';

export function useTaskSSE(taskId, onEvent) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!taskId) return;
    let source;
    let retryTimeout;
    let closed = false;

    const connect = () => {
      if (closed) return;
      const connection = new EventSource(`/api/tasks/${taskId}/events`);
      source = connection;
      const isCurrent = () => !closed && source === connection;
      connection.onmessage = (e) => {
        if (!isCurrent()) return;
        try {
          const data = JSON.parse(e.data);
          onEventRef.current?.(data);
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
