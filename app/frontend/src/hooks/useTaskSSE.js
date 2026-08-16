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
      source = new EventSource(`/api/tasks/${taskId}/events`);
      source.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          onEventRef.current?.(data);
        } catch {
          // ignore parse errors
        }
      };
      source.addEventListener('not_found', () => {
        closed = true;
        source.close();
        clearTimeout(retryTimeout);
      });
      source.onerror = () => {
        source.close();
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
