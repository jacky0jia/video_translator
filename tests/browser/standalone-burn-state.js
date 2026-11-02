async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.__streams = []; window.__snapshot = {};
    window.EventSource = class {
      constructor() { window.__streams.push(this); queueMicrotask(() => this.message(window.__snapshot)); }
      message(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
      addEventListener() {}
      close() { this.closed = true; }
    };
  });
  let task, mode, started, finish, translateCalls, denied;
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (endpoint === 'tasks/burn-state') {
      if (mode === 'status-error-running' && denied > 0 && task.burn_status === 'processing') await page.evaluate(() => { window.__fallbackChecked = true; });
      return route.fulfill({ json: { task } });
    }
    if (endpoint === 'translate') { translateCalls += 1; return route.fulfill({ json: {} }); }
    if (endpoint === 'export') {
      Object.assign(task, { last_process_route: 'subtitles', last_burn_enabled: true });
      return route.fulfill({ json: { status: 'success' } });
    }
    if (endpoint === 'burn') {
      started = true;
      Object.assign(task, { status: 'burning', burn_status: 'processing', pipeline_status: null, pipeline_stage: null, gpu_stage: null, progress_percent: 0 });
      return route.fulfill({ json: { status: 'started' } });
    }
    if (endpoint === 'burn/burn-state') {
      if (mode === 'status-error-running') { denied += 1; return route.fulfill({ status: 503, json: { detail: 'status lookup unavailable' } }); }
      if (finish && mode === 'status-error') return route.fulfill({ status: 503, json: { detail: 'status lookup unavailable' } });
      return route.fulfill({ json: { burn_status: task.burn_status, burn_error: task.burn_error, burn_path: task.burn_path } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Subtitle', confidence: -0.2 }] } }));
  await page.route('**/video/**', route => route.abort());
  const passed = [];
  for (mode of ['success', 'failure', 'status-error', 'legacy-reload', 'status-error-running']) {
    started = false; finish = false; translateCalls = 0; denied = 0;
    task = { task_id: 'burn-state', filename: `burn-${mode}.mp4`, status: 'completed', pipeline_status: 'completed', pipeline_stage: 'completed', gpu_stage: 'asr',
      last_process_route: 'subtitles', target_lang: 'Chinese', transcription_path: '/static/output/source.json',
      translation_path: '/static/output/target.json', translations: { Chinese: '/static/output/target.json' },
      burn_status: 'completed', burn_path: '/static/output/old.mp4', dubbing_status: 'completed', dubbing_audio_path: '/static/output/previous.wav' };
    if (mode === 'legacy-reload') Object.assign(task, { last_burn_enabled: true, burn_status: 'failed', burn_error: 'new render failed' });
    await page.goto('http://127.0.0.1:5189');
    await page.getByText(task.filename, { exact: true }).click();
    if (mode === 'legacy-reload') {
      await page.locator('.app-status-failed').waitFor();
      await page.getByText('new render failed', { exact: true }).waitFor();
      passed.push(mode); continue;
    }
    await page.getByRole('checkbox', { name: /Burn into video/ }).check();
    await page.evaluate(value => { window.__snapshot = value; }, task);
    await page.getByRole('button', { name: 'Create hard-subtitled video', exact: true }).click();
    await page.waitForResponse(response => response.url().endsWith('/api/burn'));
    if (!started) throw Error('Burn was not started');
    await page.getByText('burning · Chinese', { exact: true }).waitFor();
    await page.waitForFunction(() => window.__streams.length > 0);
    await page.evaluate(() => window.__streams.at(-1).message({ status: 'burning', progress: 55, message: 'rendering' }));
    await page.locator('[role="progressbar"][aria-valuenow="55"][aria-label="Render"]').waitFor();
    if (!await page.locator('.app-process-primary').isDisabled()) throw Error('Active burn unlocked primary button');
    if (mode === 'status-error-running') {
      await page.waitForFunction(() => window.__fallbackChecked === true);
      await page.locator('.app-status-processing').waitFor();
      if (!await page.locator('.app-process-primary').isDisabled()) throw Error('Status lookup failure unlocked a still-running render');
    }
    if (mode === 'success' || mode === 'status-error-running') Object.assign(task, { status: 'completed', burn_status: 'completed', burn_path: '/static/output/new.mp4' });
    else Object.assign(task, { status: 'failed', burn_status: 'failed', burn_error: 'new render failed', message: 'new render failed', failed_stage: 'burning' });
    finish = true;
    if (mode === 'success' || mode === 'status-error-running') await page.locator('.app-status-completed').waitFor();
    else {
      await page.locator('.app-status-failed').waitFor();
      await page.getByText('new render failed', { exact: true }).first().waitFor();
      await page.getByText('failed · Chinese', { exact: true }).waitFor();
      if (mode === 'failure') {
        const retry = page.waitForResponse(response => response.url().endsWith('/api/burn'));
        await page.getByRole('button', { name: 'Retry failed step', exact: true }).click();
        await retry;
        if (translateCalls !== 0) throw Error('A render-only retry unnecessarily retranslated the source');
        Object.assign(task, { status: 'failed', burn_status: 'failed', burn_error: 'new render failed', message: 'new render failed' });
        await page.locator('.app-status-failed').waitFor();
      }
      await page.reload();
      await page.getByText(task.filename, { exact: true }).click();
      await page.locator('.app-status-failed').waitFor();
    }
    if (task.dubbing_audio_path !== '/static/output/previous.wav') throw Error('Old dubbing output was removed');
    passed.push(mode);
  }
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed };
}
