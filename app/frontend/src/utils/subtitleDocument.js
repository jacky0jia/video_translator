// Validate preview transport, not recognition quality or confidence metadata.
export async function fetchSubtitleDocument(path, { signal, timeoutMs = 15000 } = {}) {
  if (signal?.aborted) throw new Error('aborted');
  const controller = new AbortController();
  let timer, onAbort;
  const interrupted = new Promise((_, reject) => {
    onAbort = () => { controller.abort(); reject(new Error('aborted')); };
    signal?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => { controller.abort(); reject(new Error('timeout')); }, timeoutMs);
  });
  try {
    return await Promise.race([interrupted, (async () => {
      const response = await fetch(path, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('http');
      const data = await response.json();
      if (!data || !Array.isArray(data.segments) || data.segments.some(row => !row || Array.isArray(row)
        || !Number.isFinite(row.start) || !Number.isFinite(row.end) || typeof row.text !== 'string')) throw new Error('invalid');
      return data;
    })()]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}
