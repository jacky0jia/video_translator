// Serve a production build and real 60-second sample.wav/sample.mp4 at /static/output/.
// Only task/config endpoints are simulated; media loading and seeking use real HTTP.
async page => {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const tasks = ['a', 'b'].map(id => ({ task_id: id, filename: `seek-${id}.mp4`, status: 'completed',
    last_process_route: 'dubbing', target_lang: 'Chinese', dubbing_audio_path: '/static/output/sample.wav',
    dubbing_video_path: '/static/output/sample.mp4',
    output_availability: { '/static/output/sample.wav': true, '/static/output/sample.mp4': true } }));
  await page.route('**/api/**', route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    return route.fulfill({ json: path === 'tasks' ? { tasks } : path.startsWith('tasks/')
      ? { task: tasks.find(task => path === `tasks/${task.task_id}`) }
      : path === 'config' ? { config: {}, is_local: true } : path === 'dubbing/voices' ? { voices: [] } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  const errors = [], passed = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  try {
    await page.setViewportSize({ width: 1080, height: 1000 });
    await page.goto(base);
    await page.getByRole('main').getByText('seek-a.mp4', { exact: true }).click();
    const panel = page.locator('[data-page="export"]');
    for (const [type, label] of [['audio', 'Chinese Dubbing'], ['video', 'Dubbed video']]) {
      const pattern = `**/static/output/sample.${type === 'audio' ? 'wav' : 'mp4'}`;
      let release;
      const held = new Promise(resolve => { release = resolve; });
      const holdMedia = async route => { await held; await route.continue(); };
      await page.route(pattern, holdMedia);
      await panel.getByRole('button', { name: `Preview ${label}`, exact: true }).click();
      const slider = panel.getByRole('slider', { name: `Preview position ${label}`, exact: true });
      await slider.waitFor({ timeout: 5000 });
      if (!await slider.isDisabled()) throw Error(`${type}: seeking enabled before metadata`);
      release();
      await page.waitForFunction(type => {
        const media = document.querySelector(`[data-page="export"] ${type}`);
        return media?.duration > 59 && media.seekable.length > 0;
      }, type);
      await page.unroute(pattern, holdMedia);
      if (await slider.isDisabled()) throw Error(`${type}: loaded media cannot seek`);
      const media = panel.locator(type);
      await slider.scrollIntoViewIfNeeded();
      const box = await slider.boundingBox();
      const mediaBox = await media.boundingBox();
      if (box.width < mediaBox.width * .9) throw Error(`${type}: timeline squeezed by native controls`);
      // Drag using actual pointer input, not programmatic currentTime assignment.
      await page.mouse.move(box.x + 8, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * .7, box.y + box.height / 2, { steps: 12 });
      await page.mouse.up();
      await page.waitForFunction(type => {
        const media = document.querySelector(`[data-page="export"] ${type}`);
        return !media.seeking && media.currentTime > 39 && media.currentTime < 45;
      }, type, { timeout: 5000 });
      await slider.press('Home');
      await slider.press('ArrowRight');
      await page.waitForFunction(type => Math.abs(document.querySelector(`[data-page="export"] ${type}`).currentTime - 1) < .2, type);
      // Native player actions must synchronize the separate timeline as well.
      await media.evaluate(async el => { el.currentTime = 20; await el.play(); });
      await page.waitForFunction(type => document.querySelector(`[data-page="export"] ${type}`).currentTime > 20.2, type);
      const position = Number(await slider.inputValue());
      if (position < 20 || position > 23) throw Error(`${type}: slider did not follow playback`);
      await page.mouse.move(box.x + box.width / 3, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * .25, box.y + box.height / 2, { steps: 6 });
      await page.mouse.up();
      await page.waitForFunction(type => {
        const media = document.querySelector(`[data-page="export"] ${type}`);
        return !media.seeking && !media.paused && media.currentTime > 13 && media.currentTime < 18;
      }, type);
      await media.evaluate(el => el.pause());
      passed.push(`${type}: full-width drag, keyboard and playback synchronization`);
    }
    await panel.screenshot({ path: '.codex-test-runs/h04-seeking-after.png' });
    await page.getByRole('main').getByText('seek-b.mp4', { exact: true }).click();
    if (await panel.locator('audio,video').count()) throw Error('New task inherited old task preview');
    await panel.getByRole('button', { name: 'Preview Chinese Dubbing', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-page="export"] audio')?.duration > 59);
    if (await panel.locator('audio').evaluate(el => el.currentTime) !== 0) throw Error('New task inherited playback position');
    passed.push('task switch resets preview and position');
    await page.route('**/static/output/sample.mp4', route => route.fulfill({ status: 404, body: 'Missing' }));
    await panel.getByRole('button', { name: 'Preview Dubbed video', exact: true }).click();
    await panel.getByRole('alert').waitFor();
    if (await panel.getByRole('slider', { name: 'Preview position Dubbed video', exact: true }).count()) {
      throw Error('Failed media left an actionable progress slider');
    }
    passed.push('failed media shows unavailable state instead of a seek control');
    if (errors.length) throw Error(errors.join('\n'));
    return { passed, pageErrors: errors };
  } finally { page.off('pageerror', onError); }
}
