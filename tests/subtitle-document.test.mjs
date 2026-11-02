import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchSubtitleDocument } from '../app/frontend/src/utils/subtitleDocument.js';

test('preview loader preserves subtitle metadata, including legacy null confidence', async () => {
  const document = { segments: [{ start: 0, end: 2, text: 'Source', confidence: null }], alignment_review_rows: [1], custom: 42 };
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    assert.equal(path, '/source.json'); assert.equal(options.cache, 'no-store');
    return { ok: true, json: async () => document };
  };
  try { assert.strictEqual(await fetchSubtitleDocument('/source.json'), document); }
  finally { globalThis.fetch = original; }
});

test('HTTP, malformed JSON and invalid structure are errors, not empty successful subtitles', async () => {
  const original = globalThis.fetch;
  try {
    for (const response of [
      { ok: false, json: async () => ({ segments: [] }) },
      { ok: true, json: async () => { throw new Error('json'); } },
      { ok: true, json: async () => ({ detail: 'not subtitles' }) },
      { ok: true, json: async () => ({ segments: [null] }) },
      { ok: true, json: async () => ({ segments: [{ start: 0, end: 2, text: {} }] }) },
    ]) {
      globalThis.fetch = async () => response;
      await assert.rejects(fetchSubtitleDocument('/source.json'));
    }
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ segments: [] }) });
    assert.deepEqual(await fetchSubtitleDocument('/source.json'), { segments: [] });
  } finally { globalThis.fetch = original; }
});

test('timeout and parent cancellation settle even if transport ignores abort', async () => {
  const original = globalThis.fetch;
  let requestSignal;
  globalThis.fetch = async (_path, options) => { requestSignal = options.signal; return new Promise(() => {}); };
  try {
    await assert.rejects(fetchSubtitleDocument('/hung.json', { timeoutMs: 20 }), /timeout/);
    assert.equal(requestSignal.aborted, true);
    const controller = new AbortController();
    const pending = fetchSubtitleDocument('/old.json', { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, /aborted/);
    assert.equal(requestSignal.aborted, true);
    await assert.rejects(fetchSubtitleDocument('/never.json', { signal: controller.signal }), /aborted/);
  } finally { globalThis.fetch = original; }
});
