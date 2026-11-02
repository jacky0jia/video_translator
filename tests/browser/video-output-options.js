// Run against either edition's local UI. APIs are mocked; no AI/GPU work runs.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => { try { localStorage.setItem('app_lang', 'en'); } catch {} });
  const task = { task_id: 'output-options', filename: 'output-options.mkv', status: 'transcribed', target_lang: 'Chinese', transcription_path: '/static/output/source.json', translations: { Chinese: '/static/output/target.json' }, translation_path: '/static/output/target.json', last_process_route: 'dubbing' };
  let submitted, invalid = false;
  await page.route('**/api/**', async route => {
    const path = '/api/' + route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (path === '/api/tasks') body = { tasks: [task] };
    if (path === '/api/tasks/output-options') body = { task };
    if (path === '/api/config') body = { config: { TTS_MODE: 'qwen' }, is_local: true };
    if (path === '/api/capabilities') body = { edition: 'supporter', capabilities: { url_import: true } };
    if (path === '/api/dubbing/voices') body = { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', name: 'Chinese voice', language: 'zh' }] };
    if (path.endsWith('/video-plan')) {
      if (invalid) return route.fulfill({ status: 400, json: { detail: 'Encoder unavailable' } });
      const request = route.request().postDataJSON();
      const copy = !request.burn_subtitles && !request.end_note_enabled;
      const custom = request.output_options.policy === 'custom';
      body = { source: { codec: 'av1', width: 3840, height: 2160, frame_rate: '60000/1001', duration: 874, bitrate: 12280000, bitrate_source: 'container_estimate' }, plan: { codec: copy ? 'av1' : custom ? request.output_options.codec : 'hevc', width: copy ? 3840 : 2560, height: copy ? 2160 : 1440, frame_rate: '60000/1001', bitrate: 10000000, estimated_bytes: 1113000000, encoder: copy ? 'copy' : 'hevc_test', software: false, reason: copy ? 'stream_copy' : 'av1_balanced_fallback' } };
    }
    if (path === '/api/pipeline') { submitted = route.request().postDataJSON(); body = { status: 'started' }; }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"status":"transcribed"}\n\n' });
    await route.fulfill({ json: body });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Original / edited text' }] } }));
  await page.goto(base);
  await page.getByText(task.filename, { exact: true }).click();
  const details = page.locator('[data-video-output]');
  await details.waitFor();
  if (await details.getAttribute('open') !== null) throw Error('Output controls should start folded');
  await details.locator('summary').click();
  await details.locator('[data-source-profile]').getByText('14:34 · Estimated 12.28 Mbps', { exact: true }).waitFor();
  if (!(await details.locator('[data-source-profile]').textContent()).includes('59.94 fps')) throw Error('Rational source fps not displayed');
  const policy = details.getByRole('combobox', { name: 'Output strategy', exact: true });
  await policy.selectOption('custom');
  await details.getByRole('combobox', { name: 'Video codec', exact: true }).selectOption('h264');
  await details.getByRole('spinbutton').fill('7.5');
  await details.locator('[data-output-plan]').getByText(/H.264/).waitFor();
  await page.getByRole('checkbox', { name: /Show end note/ }).uncheck();
  await details.getByText('Copy original video stream', { exact: true }).first().waitFor();
  if (!await policy.isDisabled()) throw Error('Output policy should be disabled for stream copy');
  if (await details.locator('[data-custom-video]').count()) throw Error('Copy must hide custom encoding fields');
  await page.getByRole('checkbox', { name: /Burn into video/ }).check();
  await details.locator('[data-custom-video]').waitFor();
  if (await policy.isDisabled()) throw Error('Burn should enable output choices');
  await page.getByRole('button', { name: 'Switch interface language' }).click();
  await details.getByText('输出视频', { exact: true }).waitFor();
  await details.getByRole('combobox', { name: '输出策略', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Switch interface language' }).click();
  invalid = true;
  await policy.selectOption('resolution');
  await details.getByRole('alert').filter({ hasText: 'Encoder unavailable' }).waitFor();
  invalid = false;
  await policy.selectOption('custom');
  await details.locator('[data-output-plan]').waitFor();
  // Check narrow layout before starting a mocked pipeline.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Process', exact: true }).click();
  await details.locator('summary').scrollIntoViewIfNeeded();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw Error('Mobile horizontal overflow');
  await page.getByRole('button', { name: 'Create dubbed video', exact: true }).click();
  await page.waitForResponse(response => response.url().endsWith('/api/pipeline'));
  if (submitted?.output_options?.codec !== 'h264' || submitted.output_options.bitrate_mbps !== 7.5) throw Error('Custom choices not sent to pipeline');
  if (submitted.force_translate || submitted.force_dub) throw Error('Changing output should not discard edited translations or speech cache');
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: true, edition: base, output_options: submitted.output_options };
}
