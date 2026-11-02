// Run against either edition's local frontend. API/SSE only: no GPU or models.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.__streams = []; window.__snapshot = {};
    window.EventSource = class {
      constructor(url) { this.url = url; window.__streams.push(this); queueMicrotask(() => this.message(window.__snapshot)); }
      addEventListener() {}
      close() { this.closed = true; }
      message(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    };
  });
  let task, burns = 0, translations = 0;
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (endpoint === 'tasks') body = { tasks: [task] };
    if (endpoint === `tasks/${task.task_id}`) body = { task };
    if (endpoint === 'config') body = { config: {}, is_local: true };
    if (endpoint === 'config/schema') body = { sections: [], fields: [] };
    if (endpoint === 'capabilities') body = { edition: 'standard' };
    if (endpoint === 'dubbing/voices') body = { voices: [], supported_languages: [] };
    if (endpoint === 'translate') translations++;
    if (endpoint.endsWith('/translation-review')) body = { approved: true };
    if (endpoint.endsWith('/video-plan')) body = {
      source: { codec: 'h264', width: 640, height: 360, duration: 10, frame_rate: '30/1', bitrate: 1000000 },
      plan: { codec: 'h264', width: 640, height: 360, encoder: 'libx264', software: true, reason: 'source_preserved' },
    };
    if (endpoint === 'burn') {
      burns++; Object.assign(task, { status: 'burning', burn_status: 'processing', state_revision: 12 });
      await page.evaluate(value => { window.__snapshot = value; }, task);
      body = { status: 'started' };
    }
    if (endpoint === `burn/${task.task_id}`) {
      Object.assign(task, { status: 'completed', burn_status: 'completed', burn_path: '/static/output/retried.mp4', state_revision: 13, progress_percent: 100 });
      body = { burn_status: 'completed' };
    }
    await route.fulfill({ json: body });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Text' }] } }));
  await page.route('**/video/**', route => route.abort());
  const passed = [];
  for (const mode of ['completed', 'failed', 'reconnect-completed', 'reconnect-failed']) {
    task = { task_id: mode, filename: `${mode}.mp4`, status: 'burning', burn_status: 'processing',
      pipeline_status: null, pipeline_stage: null, state_revision: 10, progress_percent: 55,
      last_process_route: 'subtitles', last_burn_enabled: true, target_lang: 'Chinese',
      transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
      translations: { Chinese: '/static/output/target.json' } };
    await page.goto(base);
    await page.evaluate(value => { window.__snapshot = value; }, task);
    await page.getByRole('main').getByText(task.filename, { exact: true }).click();
    await page.locator('.app-status-processing').waitFor();
    if (!await page.locator('.app-process-primary').isDisabled()) throw Error('Restored burn was not locked');
    await page.waitForFunction(() => window.__streams.length > 0);
    // Exercise a real browser reload while the backend remains active; there
    // must be no local start/poll promise left to carry the result to the UI.
    await page.reload();
    await page.evaluate(value => { window.__snapshot = value; }, task);
    await page.getByRole('main').getByText(task.filename, { exact: true }).click();
    await page.locator('.app-status-processing').waitFor();
    await page.waitForFunction(() => window.__streams.length > 0);
    const failed = mode.endsWith('failed');
    Object.assign(task, { status: failed ? 'failed' : 'completed', burn_status: failed ? 'failed' : 'completed',
      state_revision: 11, progress_percent: failed ? 55 : 100, message: failed ? 'Render failed' : 'completed',
      failed_stage: failed ? 'burning' : null, burn_error: failed ? 'Render failed' : null,
      burn_path: failed ? null : '/static/output/restored.mp4' });
    await page.evaluate(value => { window.__snapshot = value; }, task);
    if (mode.startsWith('reconnect')) {
      await page.evaluate(() => {
        const old = window.__streams.at(-1); old.onerror();
        old.message({ status: 'failed', burn_status: 'failed', state_revision: 99, message: 'Stale connection error' });
      });
      await page.waitForFunction(() => window.__streams.length > 1);
    } else await page.evaluate(() => window.__streams.at(-1).message(window.__snapshot));
    await page.locator(failed ? '.app-status-failed' : '.app-status-completed').waitFor();
    if (await page.locator('.app-process-primary').isDisabled()) throw Error('Terminal snapshot left the button locked');
    if (failed) {
      await page.getByText('Render failed', { exact: true }).first().waitFor();
      const previousBurns = burns;
      const retry = page.waitForResponse(r => r.url().endsWith('/api/burn'));
      await page.getByRole('button', { name: 'Retry failed step', exact: true }).click();
      await retry;
      await page.locator('.app-status-completed').waitFor();
      if (burns !== previousBurns + 1 || translations) throw Error('Retry did not only render');
    }
    const expected = failed ? '/static/output/retried.mp4' : '/static/output/restored.mp4';
    await page.locator(`a[download][href="${expected}"]`).waitFor();
    passed.push(mode);
  }
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed, burns, translations };
}
