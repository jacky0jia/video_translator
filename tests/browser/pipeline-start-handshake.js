async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.__streams = [];
    window.__snapshot = {};
    window.EventSource = class {
      constructor(url) {
        this.url = url; this.listeners = {}; window.__streams.push(this);
        queueMicrotask(() => this.message(window.__snapshot));
      }
      message(event) { this.onmessage?.({ data: JSON.stringify(event) }); }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() { this.closed = true; }
    };
  });
  let task, mode, pipelineRequests;
  const ready = () => Object.assign(task, {
    status: 'completed', pipeline_status: 'completed', pipeline_stage: 'completed',
    dubbing_status: 'completed', dubbing_target_lang: 'Chinese',
    dubbing_audio_path: '/static/output/dub.wav', dubbing_video_path: '/static/output/dub.mp4',
    dubbing_end_note_enabled: true,
  });
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    if (mode === 'slow-refresh' && pipelineRequests > 0 && (endpoint === 'tasks' || endpoint === 'tasks/handshake')) await page.waitForTimeout(1500);
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (endpoint === 'tasks/handshake') return route.fulfill({ json: { task } });
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', name: 'test voice', language: 'zh' }] } });
    if (endpoint.endsWith('/video-plan')) {
      await page.waitForTimeout(200);
      if (mode === 'preflight-error') return route.fulfill({ status: 400, json: { detail: 'invalid output options' } });
      return route.fulfill({ json: { source: { codec: 'h264', width: 1920, height: 1080 }, plan: { codec: 'h264', encoder: 'cpu_test', width: 1920, height: 1080, bitrate: 5000000, software: false, reason: 'source_preserved' } } });
    }
    if (endpoint === 'pipeline') {
      pipelineRequests += 1;
      await page.waitForTimeout(200);
      Object.assign(task, { status: 'pipeline_starting', pipeline_status: 'processing', pipeline_stage: null, gpu_stage: null, progress_percent: 0, message: 'new start' });
      if (mode === 'instant-completed') ready();
      await page.evaluate(snapshot => { window.__snapshot = snapshot; }, task);
      return route.fulfill(mode === 'conflict'
        ? { status: 409, json: { detail: 'Pipeline already running' } }
        : { json: { status: 'started', task_id: 'handshake' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Source', confidence: -0.2 }] } }));
  await page.route('**/video/**', route => route.abort());
  const results = [];
  for (mode of ['failed', 'completed', 'cancelled', 'conflict', 'preflight-error', 'instant-completed', 'recover-active', 'slow-refresh']) {
    pipelineRequests = 0;
    const oldStatus = ['failed', 'completed', 'cancelled'].includes(mode) ? mode : 'failed';
    task = { task_id: 'handshake', filename: `handshake-${mode}.mp4`, status: oldStatus, pipeline_status: oldStatus,
      target_lang: 'Chinese', last_process_route: 'dubbing', transcription_path: '/static/output/source.json',
      translations: { Chinese: '/static/output/target.json' }, translation_path: '/static/output/target.json',
      failed_stage: 'pipeline_dubbing', message: 'old terminal message' };
    await page.goto('http://127.0.0.1:5189');
    await page.getByText(task.filename, { exact: true }).click();
    await page.getByRole('button', { name: 'Create dubbed video', exact: true }).waitFor();
    await page.evaluate(snapshot => { window.__snapshot = snapshot; }, task);
    const primary = page.locator('.app-process-primary');
    await primary.waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('.app-process-primary').disabled);
    await primary.click();
    await page.waitForTimeout(75);
    if (!await primary.isDisabled()) throw Error(`${mode}: start unlocked before acknowledgement`);
    if (await page.evaluate(() => window.__streams.length) !== 0) throw Error(`${mode}: subscribed to previous run during preflight`);
    if (!await page.getByRole('button', { name: 'Cancel processing', exact: true }).isDisabled()) throw Error('Cancellation was enabled before a run existed');
    if (mode === 'preflight-error') {
      await page.locator('.app-status-failed').waitFor();
      if (pipelineRequests || await page.evaluate(() => window.__streams.length)) throw Error('Failed preflight started work or SSE');
      if (await primary.isDisabled()) throw Error('Failed preflight left UI locked');
      results.push(mode); continue;
    }
    await page.waitForFunction(() => window.__streams.length > 0, undefined, { timeout: mode === 'slow-refresh' ? 1000 : 30000 });
    if (pipelineRequests !== 1) throw Error(`${mode}: duplicate POST`);
    if (mode === 'instant-completed') {
      await page.locator('.app-status-completed').waitFor();
      await page.locator('[role="progressbar"][aria-valuenow="100"]').waitFor();
      results.push(mode); continue;
    }
    await page.locator('.app-status-processing').waitFor();
    await page.evaluate(() => window.__streams.at(-1).message({ status: 'switching_model', gpu_stage: 'tts', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing', progress_percent: 5 }));
    await page.locator('[role="progressbar"][aria-label="Dub"]').waitFor();
    if (!await primary.isDisabled()) throw Error('TTS preparation unlocked main button');
    if (mode === 'failed') {
      await page.evaluate(() => {
        const stream = window.__streams.at(-1); stream.onerror();
        stream.message({ status: 'failed', pipeline_status: 'failed', message: 'late old failure' });
      });
      await page.waitForFunction(() => window.__streams.length >= 2);
      await page.locator('.app-status-processing').waitFor();
    }
    await page.evaluate(() => window.__streams.at(-1).message({ status: 'dubbing', gpu_stage: 'tts', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing', progress: 60, message: '正在合成语音 3/5' }));
    await page.locator('[role="progressbar"][aria-valuenow="60"]').waitFor();
    if (mode === 'recover-active') {
      Object.assign(task, { status: 'pipeline_dubbing', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing' });
      await page.evaluate(event => { window.__snapshot = event; window.__streams.at(-1).message(event); }, task);
      await page.getByText('pipeline_dubbing · Chinese', { exact: true }).waitFor();
      const before = await page.evaluate(() => window.__streams.length);
      await page.evaluate(() => window.__streams.at(-1).message({ status: 'failed', pipeline_status: 'failed', message: 'transient stale failure' }));
      await page.waitForFunction(count => window.__streams.length > count, before);
      await page.locator('.app-status-processing').waitFor();
      if (!await primary.isDisabled()) throw Error('Authoritative running task did not restore the lock');
    }
    if (mode === 'cancelled') {
      Object.assign(task, { status: 'failed', pipeline_status: 'failed', message: 'new run failure' });
      await page.evaluate(event => window.__streams.at(-1).message(event), task);
      await page.locator('.app-status-failed').waitFor();
    } else {
      ready();
      await page.evaluate(event => window.__streams.at(-1).message(event), task);
      await page.locator('.app-status-completed').waitFor();
    }
    results.push(mode);
  }
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: results };
}
