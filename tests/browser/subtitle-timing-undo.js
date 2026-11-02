// Real page; persistence is simulated. No media, models or user documents.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const tasks = ['a', 'b'].map(id => ({ task_id: id, filename: `timing-${id}.mp4`, status: 'awaiting_review',
    state_revision: 10, target_lang: 'Chinese', pipeline_status: 'awaiting_review',
    transcription_path: `/static/output/source-${id}.json`, translation_path: `/static/output/target-${id}.json`,
    translations: { Chinese: `/static/output/target-${id}.json` }, subtitle_timing_active: 'a'.repeat(32),
    subtitle_timing_summary: { status: 'applied', changed_rows: [1], remaining_overlaps: 1, remaining_rows: [3, 4] } }));
  let posts = 0, mode = 'success', release;
  const errors = [], passed = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json', route => {
    const task = tasks.find(task => route.request().url().includes(`-${task.task_id}.json`));
    return route.fulfill({ json: { segments: [0, 2, 5, 7].map((start, i) => ({
      start, end: [task.subtitle_timing_active ? 2 : 2.1, 4, 8, 9][i], text: `Row ${i + 1}`, confidence: -.2 })) } });
  });
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    const task = tasks.find(task => path.startsWith(`tasks/${task.task_id}`));
    if (path.endsWith('/subtitle-timing/undo')) {
      posts += 1;
      const body = route.request().postDataJSON();
      if (body.state_revision !== 10 || body.version_id !== 'a'.repeat(32)) throw Error('Undo omitted current version');
      if (mode === 'hold') await new Promise(resolve => { release = resolve; });
      if (mode === 'conflict') return route.fulfill({ status: 409, json: { detail: 'Edited after repair' } });
      task.subtitle_timing_active = null; task.state_revision += 1;
      task.subtitle_timing_summary.status = 'undone';
      return route.fulfill({ json: { changed: true, active_version: null, state_revision: task.state_revision } });
    }
    if (path.endsWith('/subtitle-timing')) return route.fulfill({ json: {
      state_revision: task.state_revision, active_version: task.subtitle_timing_active, changes: [], overlaps: [[3, 4]], issues: [] } });
    return route.fulfill({ json: path === 'tasks' ? { tasks } : task ? { task }
      : path === 'config' ? { config: {}, is_local: true } : path === 'dubbing/voices' ? { voices: [] } : {} });
  });
  const select = async id => {
    await page.getByRole('main').getByText(`timing-${id}.mp4`, { exact: true }).click();
    await page.getByTestId('subtitle-timing-notice').waitFor();
    await page.getByRole('button', { name: 'Source #1', exact: true }).waitFor();
  };
  try {
    await page.goto('http://127.0.0.1:5189'); await select('a');
    const undo = page.getByRole('button', { name: 'Undo timing repair', exact: true });
    await undo.waitFor({ timeout: 5000 });
    await page.getByRole('button', { name: 'Source #1', exact: true }).click();
    if (!await undo.isDisabled()) throw Error('Undo could discard a visible draft');
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    mode = 'conflict'; await undo.click();
    await page.getByTestId('subtitle-timing-notice').getByRole('alert').waitFor();
    if (await undo.isDisabled()) throw Error('Conflict permanently locked undo');
    passed.push('draft-protection-and-conflict');
    mode = 'hold';
    await undo.evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => document.querySelector('[data-testid="subtitle-timing-notice"] button[disabled]'));
    if (posts !== 2) throw Error('Duplicate undo submitted');
    await page.getByRole('button', { name: 'Source #1', exact: true }).click();
    if (await page.locator('textarea').count()) throw Error('Editing raced pending undo');
    await select('b'); release(); release = null;
    await undo.waitFor();
    if (!await page.getByTestId('subtitle-timing-notice').innerText().then(text => text.includes('1'))) throw Error('Late A changed B summary');
    passed.push('duplicate-and-switch-isolation');
    await select('a');
    await page.getByTestId('subtitle-timing-notice').getByText('Timing repair undone.', { exact: true }).waitFor();
    await page.reload(); await select('a');
    await page.getByTestId('subtitle-timing-notice').getByText('Timing repair undone.', { exact: true }).waitFor();
    if (await undo.count()) throw Error('Refresh restored obsolete undo action');
    passed.push('successful-undo-refresh');
    if (errors.length) throw Error(errors.join('; '));
    return { passed, unhandledErrors: errors };
  } finally {
    release?.(); await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
    page.off('pageerror', onError);
  }
}
