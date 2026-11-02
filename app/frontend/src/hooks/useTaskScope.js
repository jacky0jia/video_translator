import { useCallback, useLayoutEffect, useMemo, useState } from 'react';

// A -> B -> A creates three independent scopes. Network work already submitted
// for an old scope may finish, but must not write to the current task's UI.
export function useTaskScope(taskId) {
  const scope = useMemo(() => ({
    taskId, active: false,
    startRequestRef: { current: false },
    completionHandledRef: { current: false },
  }), [taskId]);
  // Invalidate on commit, not during render: an abandoned React render must
  // not deactivate the task that is still on screen.
  useLayoutEffect(() => {
    scope.active = true;
    return () => { scope.active = false; };
  }, [scope]);
  return scope;
}

export function useTaskScopedState(scope, initial) {
  const [value, update] = useState(initial);
  const setValue = useCallback(next => {
    if (scope.active) update(next);
  }, [scope]);
  return [value, setValue];
}
