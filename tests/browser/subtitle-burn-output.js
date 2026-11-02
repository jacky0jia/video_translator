// Both editions, with mocked APIs. No media encoding or AI/GPU work runs.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { addEventListener() {} removeEventListener() {} close() {} };
  });
  const task = { task_id: 'subtitle-output', filename: 'subtitle-output.mp4', status: 'transcribed',
    target_lang: 'Chinese', last_process_route: 'subtitles', transcription_path: '/static/output/source.json',
    translation_path: '/static/output/target.json', translations: { Chinese: '/static/output/target.json' } };
  let invalid = false, plans = 0, burns = [], exports = 0, pipelines = 0;
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (path === 'tasks') body = { tasks: [task] };
    if (path === 'tasks/subtitle-output') body = { task };
    if (path === 'config') body = { config: {}, is_local: true };
    if (path === 'config/schema') body = { sections: [], fields: [] };
    if (path === 'capabilities') body = { edition: 'standard' };
    if (path === 'dubbing/voices') body = { voices: [], supported_languages: [] };
    if (path.endsWith('/video-plan')) {
      plans++;
      if (invalid) return route.fulfill({ status: 400, json: { detail: 'Encoder unavailable' } });
      const request = route.request().postDataJSON();
      if (!request.burn_subtitles) throw Error('Subtitle route must only plan burned video');
      body = { source: { codec: 'h264', width: 1920, height: 1080, duration: 10, frame_rate: '30/1', bitrate: 8000000 },
        plan: { codec: 'h264', width: 1920, height: 1080, frame_rate: '30/1', bitrate: 7500000,
          encoder: 'libx264', software: true, reason: 'custom', estimated_bytes: 9615000 } };
    }
    if (path.endsWith('/translation-review')) body = { approved: true };
    if (path === 'export') { exports++; body = {}; }
    if (path === 'pipeline') pipelines++;
    if (path === 'burn') { burns.push(route.request().postDataJSON()); body = { status: 'started' }; }
    if (path === 'burn/subtitle-output') body = { burn_status: 'completed' };
    await route.fulfill({ json: body });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Subtitle text' }] } }));
  await page.route('**/video/**', route => route.abort());
  await page.goto(base);
  await page.getByRole('main').getByText(task.filename, { exact: true }).click();
  const details = page.locator('[data-video-output]');
  if (await details.count()) throw Error('Plain subtitle export should not show video options');
  await page.getByRole('button', { name: 'Create subtitle file', exact: true }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Create subtitle file' && !b.disabled));
  if (exports !== 1 || plans !== 0 || burns.length || pipelines) throw Error('Plain subtitle export invoked video work');
  await page.getByRole('checkbox', { name: /Burn into video/ }).check();
  await details.locator('summary').click();
  const policy = details.getByRole('combobox', { name: 'Output strategy', exact: true });
  await policy.selectOption('custom');
  await details.getByRole('combobox', { name: 'Video codec', exact: true }).selectOption('h264');
  await details.getByRole('combobox', { name: 'Output resolution', exact: true }).selectOption('1080');
  await details.getByRole('spinbutton').fill('7.5');
  await details.locator('[data-output-plan]').waitFor();
  invalid = true;
  await page.getByRole('button', { name: 'Create hard-subtitled video', exact: true }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Create hard-subtitled video' && !b.disabled));
  if (burns.length || exports !== 1) throw Error('Failed preflight must stop before export/burn');
  invalid = false;
  const burnResponse = page.waitForResponse(r => r.url().endsWith('/api/burn'));
  await page.getByRole('button', { name: 'Create hard-subtitled video', exact: true }).click();
  await burnResponse;
  const choices = burns[0]?.output_options;
  if (choices?.policy !== 'custom' || choices.codec !== 'h264' || choices.resolution !== '1080' || choices.bitrate_mbps !== 7.5) throw Error('Burn lost selected choices: ' + JSON.stringify(burns));
  if (pipelines) throw Error('Subtitle burn started a dubbing pipeline');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Process', exact: true }).click();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw Error('Output options overflow at mobile width');
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: true, plans, choices, exports, pipelines };
}
