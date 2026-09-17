// Keep one request in flight so a delayed response cannot overwrite newer status.
export function pollRuntimeStatus(onStatus) {
  let active = true;
  let timer;
  let controller;
  async function refresh() {
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/qwen-tts/runtime-status', { signal: controller.signal });
      const data = response.ok ? await response.json() : null;
      if (active) onStatus(data);
    } catch {
      if (active) onStatus(null);
    } finally {
      clearTimeout(timeout);
      if (active) timer = setTimeout(refresh, 3000);
    }
  }
  refresh();
  return () => {
    active = false;
    clearTimeout(timer);
    controller.abort();
  };
}
