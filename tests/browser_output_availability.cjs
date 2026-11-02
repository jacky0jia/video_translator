async page => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1600, height: 1000 });
  const results = [];
  for (const [edition, port] of [['public', 18875], ['supporter', 18876]]) {
    await page.unrouteAll({ behavior: 'wait' });
    const paths = ['/static/output/target.srt', '/static/output/audio.wav', '/static/output/video.mp4'];
    const make = (id, available) => ({
      task_id: id, filename: `Availability ${id}.mp4`, target_lang: 'Chinese', created_at: '2026-10-20T12:00:00',
      status: 'completed', pipeline_status: 'completed', state_revision: 1, last_process_route: 'dubbing',
      last_subtitle_format: 'srt', subtitle_outputs: { srt: paths[0] },
      dubbing_audio_path: paths[1], dubbing_video_path: paths[2],
      output_availability: Object.fromEntries(paths.map(path => [path, available])),
    });
    let tasks = [make('A', false), make('B', true)];
    await page.route('**/api/**', async route => {
      const pathname = '/api/' + route.request().url().split('/api/')[1].split('?')[0];
      let body = {};
      if (pathname === '/api/tasks') body = { tasks };
      else if (/^\/api\/tasks\/[AB]$/.test(pathname)) body = { task: tasks.find(task => task.task_id === pathname.split('/').pop()) };
      else if (pathname === '/api/config') body = { config: { UI_LANGUAGE: 'en' }, is_local: true };
      else if (pathname === '/api/dubbing/voices') body = { voices: [], supported_languages: ['Chinese'], tts_mode: 'qwen' };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.route('**/static/output/**', route => route.fulfill({
      status: 200, contentType: 'text/plain', body: '1\n00:00:00,000 --> 00:00:01,000\nAvailability preview fixture\n',
    }));
    await page.goto(`http://127.0.0.1:${port}`);
    await page.getByText('Availability A.mp4', { exact: true }).click();
    const panel = page.locator('[data-page="export"]');
    const check = (condition, message) => { if (!condition) throw new Error(`${edition}: ${message}`); };
    const state = async (files, disabled) => {
      await panel.getByText(`${files} available ${files === 1 ? 'file' : 'files'}`, { exact: true }).waitFor();
      check(await panel.locator('a[download]').count() === files, 'incorrect actionable download count');
      check(await panel.locator('button[disabled]').count() === disabled, 'incorrect disabled controls');
    };
    const refresh = async () => {
      await page.getByRole('button', { name: 'Refresh List', exact: true }).click();
      // Detail requests keep the same history revision: file availability is transient.
    };
    await state(0, 6);
    check(await panel.getByText(/File missing, empty or inaccessible/).count() === 3, 'missing file reasons');
    results.push(`${edition}: missing outputs disabled`);

    tasks[0].output_availability[paths[0]] = true;
    await refresh();
    await state(1, 4);
    await panel.getByRole('button', { name: 'Preview Chinese Subtitles', exact: true }).click();
    await panel.getByText(/Availability preview fixture/).waitFor();
    results.push(`${edition}: mixed outputs and real subtitle preview`);

    tasks[0].output_availability[paths[0]] = false;
    await refresh();
    await state(0, 6);
    check(await panel.locator('pre').count() === 0, 'invalidated preview remained open');
    results.push(`${edition}: refresh disables an already-open unavailable preview`);

    tasks[0].output_availability = Object.fromEntries(paths.map(path => [path, true]));
    await refresh();
    await state(3, 0);
    await page.getByText('Availability B.mp4', { exact: true }).click();
    await state(3, 0);
    check(await panel.locator('[aria-expanded="true"]').count() === 0, 'preview state leaked between tasks sharing output paths');
    results.push(`${edition}: restored outputs and task ownership`);

    delete tasks[1].output_availability;
    await refresh();
    await state(0, 6);
    check(await panel.getByText(/File availability has not been checked/).count() === 3, 'unknown treated as ready');
    results.push(`${edition}: unknown availability fails closed`);

    tasks[1].output_availability = Object.fromEntries(paths.map(path => [path, true]));
    tasks[1].dubbing_audio_path = '/static/output/new-audio.wav';
    await refresh();
    await state(2, 2);
    check(await panel.locator('a[href="/static/output/new-audio.wav"]').count() === 0, 'old path authorized a new path');
    results.push(`${edition}: availability bound to exact output path`);

    tasks[1].dubbing_video_path = '/static/output/part#1.mp4';
    tasks[1].output_availability['/static/output/part#1.mp4'] = true;
    await refresh();
    await state(1, 4);
    results.push(`${edition}: fragment cannot enable a different browser target`);

    tasks[1].dubbing_video_path = '/static/output/part%231.mp4';
    tasks[1].output_availability['/static/output/part%231.mp4'] = true;
    await refresh();
    await state(2, 2);
    check(await panel.locator('a[href="/static/output/part%231.mp4"]').count() === 1, 'encoded filename was disabled');
    results.push(`${edition}: correctly encoded special filename stays enabled`);

    tasks[1].last_process_route = 'subtitles';
    tasks[1].last_burn_enabled = true;
    tasks[1].burn_path = '/static/output/burn.mp4';
    tasks[1].output_availability['/static/output/burn.mp4'] = false;
    await refresh();
    await state(1, 2);
    check(await panel.getByText('Hard-subtitled video', { exact: true }).count() === 1, 'burn output missing');
    results.push(`${edition}: standalone burn file check`);
  }
  if (errors.length) throw new Error(JSON.stringify(errors));
  return { passed: results, pageErrors: errors };
}
