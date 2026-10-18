async page => {
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { addEventListener() {} close() {} };
  });
  const errors = [], saves = [], pipelines = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  const saved = { route: 'dubbing', subtitle_format: 'ass', burn_subtitles: true,
    end_note_enabled: false, subtitle_content: 'bilingual', voice: 'voice-2', speed: 1.2,
    output_options: { policy: 'custom', codec: 'h264', resolution: '1080', bitrate_mbps: 8 },
    subtitle_style: { offsetY: 12, fontSize: 30, sourceColor: '#FFFFFF', targetColor: '#FDE047', bold: true, fontFamily: 'Arial' },
    show_source: false, show_target: true };
  const base = id => ({ task_id: id, filename: `${id}.mp4`, status: 'awaiting_review', pipeline_status: 'awaiting_review',
    target_lang: 'Chinese', transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
    translations: { Chinese: '/static/output/target.json' }, state_revision: 5,
    translation_reviews: { Chinese: { review_id: `review-${id}`, status: 'pending' } }, created_at: '2026-10-19T00:00:00' });
  const tasks = { A: { ...base('A'), workflow_options: saved }, B: base('B') };
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = request.url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: Object.values(tasks) } });
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'edge', voices: [
      { id: 'voice-1', language: 'zh', name: 'Voice one' }, { id: 'voice-2', language: 'zh', name: 'Voice two' },
    ] } });
    if (endpoint.endsWith('/video-plan')) return route.fulfill({ json: { source: { width: 1920, height: 1080, codec: 'h264' },
      plan: { reason: 'custom', codec: 'h264', width: 1920, height: 1080 } } });
    const id = endpoint.split('/')[1];
    if (/^tasks\/[^/]+$/.test(endpoint)) return route.fulfill({ json: { task: tasks[id] } });
    if (endpoint.endsWith('/workflow-options')) {
      const options = request.postDataJSON(); saves.push({ id, options });
      tasks[id] = { ...tasks[id], workflow_options: options, state_revision: tasks[id].state_revision + 1 };
      return route.fulfill({ json: { workflow_options: options, state_revision: tasks[id].state_revision } });
    }
    if (endpoint === 'pipeline') {
      pipelines.push(request.postDataJSON());
      return route.fulfill({ status: 400, json: { detail: 'Isolated test stop before synthesis' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: { language: 'en', segments: [
    { start: 0, end: 2, text: 'Example sentence', confidence: 1 },
  ] } }));
  await page.route('**/video/**', route => route.abort());
  const select = async id => {
    await page.locator('.app-task-card').filter({ hasText: `${id}.mp4` }).click();
    await page.locator('.app-current-task').filter({ hasText: `${id}.mp4` }).waitFor();
  };
  const panel = page.locator('[data-page="process"]');
  const inspect = () => panel.evaluate(el => ({
    route: el.querySelector('input[name="process-route"]:checked')?.value,
    selects: [...el.querySelectorAll('select')].map(input => input.value),
    toggles: [...el.querySelectorAll('input[type="checkbox"]')].map(input => input.checked),
    speed: el.querySelector('input[type="range"]')?.value,
    format: [...el.querySelectorAll('fieldset input')].findIndex(input => input.checked),
  }));
  const verifyA = async () => {
    await panel.getByRole('combobox').first().filter({ has: page.locator('option[value="voice-2"]') }).waitFor();
    const view = await inspect();
    if (view.route !== 'dubbing' || view.format !== 2 || view.selects[0] !== 'voice-2'
      || view.speed !== '1.2' || view.toggles.join() !== 'true,false') throw Error(`A request not restored: ${JSON.stringify(view)}`);
    if (await page.getByRole('checkbox', { name: 'Source', exact: true }).isChecked()) throw Error('A source visibility not restored');
    if (!await page.getByRole('checkbox', { name: 'Target', exact: true }).isChecked()) throw Error('A target visibility not restored');
  };
  await page.goto('http://127.0.0.1:5189'); await select('A'); await verifyA();
  results.push('review-restores-route-voice-speed-format-burn-and-visibility');
  await page.reload(); await select('A'); await verifyA();
  if (saves.length || pipelines.length) throw Error('Reload saved or started a workflow');
  results.push('reload-restores-without-starting-or-approving');
  await select('B');
  const b = await inspect();
  if (b.route !== 'subtitles' || b.format !== 0 || b.toggles[0] !== false) throw Error('A options leaked into B');
  if (!await page.getByRole('checkbox', { name: 'Source', exact: true }).isChecked()) throw Error('A preview visibility leaked into B');
  results.push('task-switch-resets-form-and-preview');
  await select('A'); await verifyA();
  await panel.getByRole('combobox').first().selectOption('voice-1');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.getByRole('button', { name: '确认字幕并继续', exact: true }).waitFor();
  if ((await inspect()).selects[0] !== 'voice-1') throw Error('Language change overwrote manual voice');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.getByRole('button', { name: 'Continue after review', exact: true }).waitFor();
  results.push('catalog-and-language-reload-preserve-manual-selection');
  const launched = page.waitForResponse(response => response.url().endsWith('/api/pipeline'));
  await page.locator('.app-process-primary').click(); await launched;
  await page.locator('.app-status-failed').waitFor();
  if (saves.length !== 1 || pipelines.length !== 1) throw Error('Request not saved exactly once');
  const record = saves[0].options, actual = pipelines[0];
  if (saves[0].id !== 'A' || record.voice !== 'voice-1' || record.subtitle_style.fontSize !== 30
    || record.subtitle_style.offsetY !== 12 || !record.subtitle_style.bold || record.show_source !== false
    || actual.voice !== record.voice || actual.speed !== record.speed || actual.subtitle_style.fontSize !== 30
    || actual.output_options.bitrate_mbps !== 8 || actual.review_id !== 'review-A') throw Error('Saved options differ from actual continuation');
  results.push('existing-button-saves-request-style-output-and-review-before-synthesis');
  await page.reload(); await select('A');
  await panel.getByRole('combobox').first().filter({ has: page.locator('option[value="voice-1"]') }).waitFor();
  if ((await inspect()).selects[0] !== 'voice-1') throw Error('Submitted voice lost on refresh');
  results.push('last-submitted-options-survive-another-refresh');
  if (errors.length) throw Error(errors.join('\n'));
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: results, realModelsUsed: false };
}
