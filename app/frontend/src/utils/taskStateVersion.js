// Shared by task details and live streams: a slow HTTP response must not undo
// a newer SSE state. Revisions are monotonic per task, across pipeline runs.
const latest = new Map();

export function stateRevision(state) {
  return Number.isSafeInteger(state?.state_revision) && state.state_revision >= 0 ? state.state_revision : null;
}

export function acceptTaskState(taskId, state) {
  const revision = stateRevision(state);
  if (!taskId || revision === null) return true; // legacy/unversioned producers
  if (revision < (latest.get(taskId) ?? -1)) return false;
  latest.set(taskId, revision);
  return true;
}

export function olderTaskState(incoming, previous) {
  const next = stateRevision(incoming), before = stateRevision(previous);
  return next !== null && before !== null && next < before;
}
