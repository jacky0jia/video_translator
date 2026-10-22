// Real UI with mocked persistence only. No user files, models or GPU touched.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const tasks = ['a', 'b'].map(id => ({ task_id: id, filename: `edit-owner-${id}.mp4`, status: 'transcribed', target_lang: 'Chinese',
    transcription_path: `/static/output/source-${id}.json`, translation_path: `/static/output/target-${id}.json`,
    translations: { Chinese: `/static/output/target-${id}.json` } }));
  const documents = {};
  for (const id of ['a', 'b']) for (const track of ['source', 'target']) documents[`${track}-${id}`] = {
    segments: [0, 1].map(i => ({ start: i * 3, end: i * 3 + 2, text: `${track} ${id} ${i}`, confidence: -0.2 })),
  };
  let mode = 'success', release, writes = [];
  const errors = [], passed = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    if (/^tasks\/[ab]\/segments\/\d+$/.test(path)) {
      const [, id, , row] = path.split('/'), payload = route.request().postDataJSON(), behavior = mode;
      writes.push({ path, payload });
      if (behavior.startsWith('hold')) await new Promise(resolve => { release = resolve; });
      if (behavior === 'network') return route.abort('connectionreset');
      if (behavior.includes('conflict')) return route.fulfill({ status: 409, json: { detail: 'Save conflict' } });
      const index = Number(row);
      if (payload.source_text !== undefined) documents[`source-${id}`].segments[index].text = payload.source_text;
      if (payload.target_text !== undefined) documents[`target-${id}`].segments[index].text = payload.target_text;
      if (payload.start !== undefined) for (const track of ['source', 'target']) Object.assign(documents[`${track}-${id}`].segments[index], { start: payload.start, end: payload.end });
      return route.fulfill({ json: { status: 'success', updated: ['source_text' in payload ? 'source' : 'target_text' in payload ? 'target' : 'timing'] } });
    }
    const id = path.startsWith('tasks/') ? path.split('/')[1] : '';
    await route.fulfill({ json: path === 'tasks' ? { tasks } : id ? { task: tasks.find(t => t.task_id === id) }
      : path === 'config' ? { config: {}, is_local: true } : path === 'config/schema' ? { sections: [], fields: [] }
        : path === 'dubbing/voices' ? { voices: [] } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json', route => {
    const name = route.request().url().split('/').at(-1).replace('.json', '');
    return route.fulfill({ json: documents[name] });
  });
  const select = async id => {
    await page.getByRole('main').getByText(`edit-owner-${id}.mp4`, { exact: true }).click();
    await page.getByRole('button', { name: 'Source #1', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Target #1', exact: true }).waitFor();
  };
  const fresh = async () => { mode = 'success'; writes = []; release = null; await page.goto(base); await select('a'); };
  const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const held = async () => { for (let i = 0; i < 100 && !release; i++) await frames(); if (!release) throw Error('Save request not held'); };
  const finish = async () => {
    const response = page.waitForResponse(r => r.url().includes('/segments/'));
    release(); release = null; await response; await frames();
  };
  try {
    for (const behavior of ['hold', 'hold-conflict']) {
      await fresh(); mode = behavior;
      await page.getByRole('button', { name: 'Target #1', exact: true }).click();
      await page.locator('textarea').fill(`A saved ${behavior}`); await page.getByRole('button', { name: 'Save', exact: true }).click(); await held();
      await select('b'); await page.getByRole('button', { name: 'Target #1', exact: true }).click();
      await page.locator('textarea').fill('B unsaved draft'); await finish();
      if (await page.locator('textarea').count() !== 1 || await page.locator('textarea').inputValue() !== 'B unsaved draft') throw Error('Late A response closed or replaced B draft');
      if (await page.getByText('Save Save conflict', { exact: true }).count()) throw Error('Old task error appeared on B');
      if (writes.length !== 1 || writes[0].path !== 'tasks/a/segments/0') throw Error('Save crossed task boundary');
      passed.push(`task-switch-${behavior}`);
    }
    await fresh(); mode = 'hold';
    await page.getByRole('button', { name: 'Target #1', exact: true }).click();
    await page.locator('textarea').fill('Old same-task edit'); await page.getByRole('button', { name: 'Save', exact: true }).click(); await held();
    await page.getByRole('button', { name: 'Source #2', exact: true }).click(); await page.locator('textarea').fill('New same-task draft');
    await finish();
    if (await page.locator('textarea').count() !== 1 || await page.locator('textarea').inputValue() !== 'New same-task draft') throw Error('Old save closed a new edit session in the same task');
    passed.push('same-task-new-editor');
    await fresh(); mode = 'hold';
    await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill('One submission');
    await page.getByRole('button', { name: 'Save', exact: true }).evaluate(button => { button.click(); button.click(); }); await held();
    if (writes.length !== 1 || !await page.getByRole('button', { name: 'Save', exact: true }).isDisabled()) throw Error('Duplicate save was not locked');
    await finish(); passed.push('duplicate-save');
    for (const leave of ['cancel', 'round-trip']) {
      await fresh(); mode = 'hold';
      await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill(`Old ${leave}`);
      await page.getByRole('button', { name: 'Save', exact: true }).click(); await held();
      if (leave === 'cancel') await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      else { await select('b'); await select('a'); }
      await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill(`New ${leave}`);
      if (!await page.getByRole('button', { name: 'Save', exact: true }).isDisabled()) throw Error('Reopening task allowed an overlapping save');
      await finish();
      if (await page.locator('textarea').inputValue() !== `New ${leave}` || await page.getByRole('button', { name: 'Save', exact: true }).isDisabled()) throw Error('Old finalizer damaged or locked reopened draft');
      mode = 'success'; await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.locator('textarea').waitFor({ state: 'hidden' });
      if (documents['target-a'].segments[0].text !== `New ${leave}` || writes.length !== 2) throw Error('Reopened draft failed to save after old request settled');
      passed.push(`reopen-${leave}`);
    }
    for (const field of ['text', 'time']) {
      await fresh(); mode = 'hold';
      const original = field === 'text' ? documents['target-a'].segments[0].text : documents['source-a'].segments[0].start;
      if (field === 'text') {
        await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill('Older write changes baseline');
      } else {
        await page.getByRole('button', { name: 'Edit time #1', exact: true }).click();
        await page.getByRole('textbox', { name: 'Start time', exact: true }).fill('00:00.75');
      }
      await page.getByRole('button', { name: 'Save', exact: true }).click(); await held();
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.getByRole('button', { name: field === 'text' ? 'Target #1' : 'Edit time #1', exact: true }).click();
      await finish(); mode = 'success';
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'hidden' });
      const persisted = field === 'text' ? documents['target-a'].segments[0].text : documents['source-a'].segments[0].start;
      if (persisted !== original || writes.length !== 2) throw Error(`Reopened original ${field} draft was incorrectly treated as unchanged`);
      passed.push(`restore-original-${field}`);
    }
    for (const behavior of ['network', 'conflict']) {
      await fresh(); mode = behavior;
      const draft = `Keep failed draft ${behavior}`;
      await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill(draft);
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await page.getByText(/^Save /).waitFor();
      if (await page.locator('textarea').inputValue() !== draft || await page.getByRole('button', { name: 'Save', exact: true }).isDisabled()) throw Error('Save failure lost draft or left save locked');
      mode = 'success'; await page.getByRole('button', { name: 'Save', exact: true }).click();
      await page.locator('textarea').waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: draft }).waitFor();
      passed.push(`retry-${behavior}`);
    }
    await fresh();
    await page.getByRole('button', { name: 'Source #1', exact: true }).click(); await page.locator('textarea').fill('');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.locator('textarea').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill('');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.locator('textarea').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Source #1', exact: true }).click(); await page.locator('textarea').fill('Restored source');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.locator('textarea').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Target #1', exact: true }).click(); await page.locator('textarea').fill('Restored target');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.locator('textarea').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Edit time #1', exact: true }).click();
    await page.getByRole('textbox', { name: 'Start time', exact: true }).fill('00:00.50');
    await page.getByRole('textbox', { name: 'End time', exact: true }).fill('00:02.50');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByRole('textbox', { name: 'Start time', exact: true }).waitFor({ state: 'hidden' });
    if (documents['source-a'].segments[0].text !== 'Restored source' || documents['target-a'].segments[0].text !== 'Restored target'
      || documents['target-a'].segments[0].start !== 0.5) throw Error('Empty subtitle refill or time save regression');
    passed.push('source-target-empty-refill-and-time');
    if (errors.length) throw Error('Unhandled page errors: ' + errors.join('; '));
    return { passed, unhandledErrors: errors };
  } finally {
    release?.();
    await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' }); page.off('pageerror', onError);
  }
}
