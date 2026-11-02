async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.addInitScript(() => localStorage.setItem('app_lang', 'en'));
  let pipelineRequests = 0;
  const task = {
    task_id: 'running-lock', filename: 'running-lock.mp4', status: 'transcribed',
    target_lang: 'Chinese', transcription_path: '/static/output/source.json',
    translations: {}, created_at: '2026-10-07T00:00:00',
  };

  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    if (path === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (path === 'tasks/running-lock') return route.fulfill({ json: { task } });
    if (path === 'config') return route.fulfill({ json: { config: { TTS_MODE: 'qwen' }, is_local: true } });
    if (path === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', supported_languages: ['Chinese'], voices: [{ id: 'zh_female_1', name: 'zh_female_1', language: 'zh' }] } });
    if (path.endsWith('/video-plan')) {
      await page.waitForTimeout(200);
      return route.fulfill({ json: { source: { codec: 'h264', width: 1920, height: 1080 }, plan: { codec: 'h264', width: 1920, height: 1080, bitrate: 5000000, encoder: 'h264_test', software: false, reason: 'source_preserved' } } });
    }
    if (path === 'pipeline') {
      pipelineRequests += 1;
      Object.assign(task, {
        status: 'pipeline_translating', pipeline_status: 'processing',
        pipeline_stage: 'pipeline_translating', progress_percent: 40,
        message: '正在翻译...',
      });
      return route.fulfill({ status: 409, json: { detail: 'Pipeline already running' } });
    }
    if (path.endsWith('/events')) {
      const snapshot = pipelineRequests ? {
        ...task, status: 'translating', progress_percent: 60, message: '3/5',
      } : task;
      return route.fulfill({
        contentType: 'text/event-stream',
        body: `data: ${JSON.stringify(snapshot)}\n\n`,
      });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/source.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Source' }] } }));

  await page.goto('http://127.0.0.1:5188');
  await page.getByText('running-lock.mp4', { exact: true }).click();
  await page.getByRole('radio', { name: 'Dubbing', exact: true }).check();
  const button = page.locator('.app-process-primary');
  await page.getByRole('button', { name: 'Create dubbed video', exact: true }).click();
  await page.waitForTimeout(75);
  if (!await button.isDisabled()) throw new Error('Stale transcribed SSE snapshot re-enabled the button during video preflight');
  if (await page.locator('[role="progressbar"][aria-valuenow="100"]').count()) throw new Error('Stale transcribed SSE snapshot completed the dubbing progress bar');
  await page.waitForResponse(response => response.url().endsWith('/api/pipeline'));

  if (pipelineRequests !== 1) throw new Error(`Expected one pipeline request, received ${pipelineRequests}`);
  if (!await button.isDisabled()) throw new Error('Create dubbed video stayed enabled for an active pipeline');
  if (await page.getByText('Pipeline already running', { exact: true }).count()) throw new Error('Conflict text replaced the live processing state');
  await page.locator('.app-status-processing').waitFor();
  await page.locator('[role="progressbar"][aria-valuenow="60"]').waitFor();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
}
