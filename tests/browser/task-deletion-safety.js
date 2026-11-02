// Actual task-list UI, mocked deletion responses; never delete user files.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  let tasks = [], status = 409, calls = 0;
  const errors = [], passed = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    const deleting = route.request().method() === 'DELETE' || path === 'tasks/batch-delete';
    if (deleting) {
      calls++;
      if (status === 200) tasks = path === 'tasks/idle' ? tasks.filter(t => t.task_id !== 'idle') : [];
      return route.fulfill({ status, json: { detail: 'Task is running or stopping', deleted: ['idle', 'busy'] } });
    }
    return route.fulfill({ json: path === 'tasks' ? { tasks }
      : path === 'config' ? { config: {}, is_local: true }
        : path === 'config/schema' ? { sections: [], fields: [] }
          : path === 'dubbing/voices' ? { voices: [] } : {} });
  });
  const list = () => page.locator('section').filter({ has: page.getByRole('button', { name: 'Refresh List', exact: true }) });
  for (const responseStatus of [409, 500, 200]) for (const mode of ['single', 'batch', 'all']) {
    status = responseStatus; calls = 0;
    tasks = ['idle', 'busy'].map(id => ({ task_id: id, filename: `delete-${id}.mp4`, status: 'completed', target_lang: 'Chinese' }));
    await page.goto(base);
    await list().getByRole('button', { name: 'Delete Task', exact: true }).first().waitFor();
    if (mode === 'single') await list().getByRole('button', { name: 'Delete Task', exact: true }).first().click();
    if (mode === 'batch') {
      for (const checkbox of await list().getByRole('checkbox').all()) await checkbox.check();
      await list().getByRole('button', { name: 'Delete Selected', exact: true }).click();
    }
    if (mode === 'all') await list().getByRole('button', { name: 'Delete All', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm', exact: true }).click();
    if (status !== 200) {
      const message = status === 409 ? 'A task is still running or stopping. Cancel it and wait before deleting.' : 'Delete Failed';
      await page.getByText(message, { exact: true }).waitFor();
      if (await list().getByRole('button', { name: 'Delete Task', exact: true }).count() !== 2) throw Error(`${mode}/${status}: task disappeared on refusal`);
    } else {
      await page.waitForFunction(want => document.querySelectorAll('button[title="Delete Task"]').length === want, mode === 'single' ? 1 : 0);
    }
    if (calls !== 1) throw Error(`${mode}/${status}: unexpected deletion requests ${calls}`);
    passed.push(`${mode}/${status}`);
  }
  page.off('pageerror', onError);
  if (errors.length) throw Error(errors.join('; '));
  await page.goto('about:blank');
  return { passed, pageErrors: errors.length };
}
