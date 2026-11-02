async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const tasks = ['a', 'b'].map(id => ({ task_id: id, filename: `loading-${id}.mp4`, status: 'completed', target_lang: 'Chinese',
    transcription_path: `/static/output/source-${id}.json`, translation_path: `/static/output/target-${id}.json`,
    translations: { Chinese: `/static/output/target-${id}.json`, Japanese: `/static/output/japanese-${id}.json` } }));
  let mode = 'hold-http', targetMode = 'ok', sourceRequests = 0, writes = 0, targetText = 'Target a', releaseSource, sourceHeld, releaseTarget, targetHeld;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    if (path === 'tasks/a/segments/0' && route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      if (Object.keys(payload).join() !== 'target_text') throw Error('Unexpected write to source/timing');
      writes++; targetText = payload.target_text;
      return route.fulfill({ json: { success: true } });
    }
    const id = path.startsWith('tasks/') ? path.split('/')[1] : '';
    return route.fulfill({ json: path === 'tasks' ? { tasks } : id ? { task: tasks.find(task => task.task_id === id) } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json*', async route => {
    const path = route.request().url();
    const source = path.includes('/source-');
    const id = path.includes('-a.json') ? 'a' : 'b';
    if (!source && targetMode === 'http') return route.fulfill({ status: 500, json: { detail: 'temporary' } });
    if (!source && targetMode === 'hold-japanese' && path.includes('/japanese-')) {
      targetHeld = true;
      await new Promise(resolve => { releaseTarget = resolve; });
    }
    if (source) {
      sourceRequests++;
      if (mode === 'hold-http') {
        sourceHeld = true;
        await new Promise(resolve => { releaseSource = resolve; });
        return route.fulfill({ status: 503, json: { detail: 'temporary' } }).catch(() => {});
      }
      if (mode === 'http') return route.fulfill({ status: 503, json: { detail: 'temporary' } });
      if (mode === 'malformed') return route.fulfill({ json: { detail: 'not a subtitle document' } });
      if (mode === 'network') return route.abort('connectionreset');
      if (mode === 'empty') return route.fulfill({ json: { segments: [] } });
      if (mode === 'hold' && id === 'a') {
        sourceHeld = true;
        await new Promise(resolve => { releaseSource = resolve; });
      }
    }
    const text = source ? `Source ${id}` : path.includes('/japanese-') ? `Japanese ${id}` : id === 'a' ? targetText : 'Target b';
    await route.fulfill({ json: { segments: [{ start: 1, end: 3, text, confidence: null }], alignment_review_rows: [] } }).catch(() => {});
  });
  await page.goto('http://127.0.0.1:5189');
  await page.getByRole('main').getByText(tasks[0].filename, { exact: true }).click();
  const sourceError = page.locator('[data-subtitle-load-error="source"]');
  // A hung source request must not prevent the target request from completing.
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Target a' }).waitFor();
  if (!sourceHeld) throw Error('Source request was not held');
  releaseSource(); sourceHeld = false;
  await sourceError.waitFor();
  mode = 'ok';
  await sourceError.getByRole('button', { name: 'Retry loading subtitles' }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).filter({ hasText: 'Source a' }).waitFor();
  await sourceError.waitFor({ state: 'hidden' });

  await page.getByRole('button', { name: 'Target #1', exact: true }).click();
  await page.locator('textarea').fill('Edited target a');
  mode = 'http';
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await sourceError.waitFor();
  await page.getByRole('button', { name: 'Source #1', exact: true }).filter({ hasText: 'Source a' }).waitFor();
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Edited target a' }).waitFor();
  await page.getByRole('button', { name: 'Source #1', exact: true }).click();
  if (await page.locator('textarea').count()) throw Error('Stale source allowed editing');
  if (writes !== 1) throw Error('Source data was written during a load failure');
  mode = 'ok';
  await sourceError.getByRole('button', { name: 'Retry loading subtitles' }).click();
  await sourceError.waitFor({ state: 'hidden' });
  await page.reload();
  await page.getByRole('main').getByText(tasks[0].filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).filter({ hasText: 'Source a' }).waitFor();
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Edited target a' }).waitFor();

  const beforeLanguageSwitch = sourceRequests;
  const languages = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Video Preview', exact: true }) })
    .locator('select').filter({ has: page.locator('option[value="Japanese"]') });
  await page.getByRole('button', { name: 'Target #1', exact: true }).click();
  await page.locator('textarea').fill('Unsaved draft for Chinese only');
  await languages.selectOption('Japanese');
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Japanese a' }).waitFor();
  if (await page.locator('textarea').count()) throw Error('Editor draft carried into another language');
  if (sourceRequests !== beforeLanguageSwitch) throw Error('Language switch unnecessarily restarted source loading');
  await languages.selectOption('Chinese');
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Edited target a' }).waitFor();
  targetMode = 'hold-japanese';
  await languages.selectOption('Japanese');
  for (let attempt = 0; attempt < 100 && !targetHeld; attempt++) await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  if (!targetHeld) throw Error('Could not stage a delayed target request');
  await languages.selectOption('Chinese');
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Edited target a' }).waitFor();
  releaseTarget(); targetMode = 'ok';
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  if (await page.getByRole('button', { name: 'Target #1', exact: true }).textContent() !== 'Edited target a') throw Error('Late Japanese response overwrote selected Chinese');

  mode = 'hold';
  await page.getByRole('main').getByText(tasks[0].filename, { exact: true }).click();
  for (let attempt = 0; attempt < 100 && !sourceHeld; attempt++) await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  if (!sourceHeld) throw Error('Could not stage a delayed source request');
  await page.getByRole('main').getByText(tasks[1].filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).filter({ hasText: 'Source b' }).waitFor();
  releaseSource();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  if (await page.getByRole('button', { name: 'Source #1', exact: true }).textContent() !== 'Source b') throw Error('Late A response overwrote task B');
  mode = 'ok'; targetMode = 'http';
  await page.getByRole('main').getByText(tasks[1].filename, { exact: true }).click();
  const targetError = page.locator('[data-subtitle-load-error="target"]');
  await targetError.waitFor();
  await page.getByRole('button', { name: 'Target #1', exact: true }).click();
  if (await page.locator('textarea').count()) throw Error('Stale target allowed editing');
  targetMode = 'ok';
  await targetError.getByRole('button', { name: 'Retry loading subtitles' }).click();
  await targetError.waitFor({ state: 'hidden' });

  for (const failure of ['malformed', 'network']) {
    mode = failure;
    await page.getByRole('main').getByText(tasks[1].filename, { exact: true }).click();
    await sourceError.waitFor();
    await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: 'Target b' }).waitFor();
    mode = 'ok';
    await sourceError.getByRole('button', { name: 'Retry loading subtitles' }).click();
    await sourceError.waitFor({ state: 'hidden' });
  }
  mode = 'empty';
  await page.getByRole('main').getByText(tasks[1].filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).waitFor({ state: 'hidden' });
  if (await sourceError.count()) throw Error('Valid empty subtitles incorrectly treated as transport failure');
  if (errors.length) throw Error(errors.join('\n'));
  return { passed: true, writes, checked: ['hung source independence', 'HTTP failure', 'retry', 'read-only stale source and target', 'edit-refresh', 'reload', 'language isolation', 'late task and language responses', 'invalid JSON structure', 'network failure', 'valid empty'] };
}
