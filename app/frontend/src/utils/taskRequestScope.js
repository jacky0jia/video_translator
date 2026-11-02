// Selection intent changes before a request starts, not when it finishes.
export function createTaskRequestScope() {
  let selectedId = null;
  let selection = 0;
  let detail = 0;
  let list = 0;
  return {
    selectedId: () => selectedId,
    select(id) { selectedId = id; selection += 1; detail += 1; },
    invalidateDetail() { detail += 1; },
    invalidateList() { list += 1; },
    beginList() { return ++list; },
    acceptsList(ticket) { return ticket === list; },
    beginDetail(id) {
      if (!id || id !== selectedId) return null;
      return { id, selection, detail: ++detail };
    },
    acceptsDetail(ticket) {
      return Boolean(ticket && ticket.id === selectedId
        && ticket.selection === selection && ticket.detail === detail);
    },
  };
}
