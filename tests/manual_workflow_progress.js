async page => {
  const task = {
    task_id: 'workflow-test', filename: 'Workflow test.mp4', status: 'completed',
    target_lang: 'Chinese', created_at: '2026-10-16T12:00:00',
    transcription_path: '/static/output/source.json', translation_path: '/static/output/translated.json',
    translations: {Chinese: '/static/output/translated.json'},
    last_process_route: 'subtitles', last_end_note_enabled: true,
  };
  await page.route('**/api/**', async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    let body = {};
    if (path === '/api/tasks') body = {tasks: [task]};
    else if (path === '/api/tasks/workflow-test') body = {task};
    else if (path === '/api/config') body = {config: {UI_LANGUAGE:'en'}, is_local:true};
    else if (path === '/api/dubbing/voices') body = {tts_mode:'qwen', voices:[{id:'zh_female_1', language:'zh'}]};
    else if (path === '/api/capabilities') body = {supported_languages:['Chinese','English']};
    await route.fulfill({json:body});
  });
  await page.route('**/static/output/**', route => route.fulfill({json:{video_source:'test.mp4',language:'en',segments:[]}}));
  await page.reload();
  await page.getByText('Workflow test.mp4', {exact:true}).first().click();
  const inspect = () => page.locator('.app-stage').evaluateAll(nodes => nodes.map(n => n.className));
  const check = (value, message) => { if (!value) throw new Error(message); };
  const completed = () => page.getByRole('progressbar').getAttribute('aria-valuenow');
  await page.getByRole('button', {name:'Create subtitle file', exact:true}).waitFor();
  let stages = await inspect();
  check(stages.slice(0,2).every(s => s.includes('app-stage-done')), 'Subtitle stages must be done');
  check(stages.slice(2).every(s => s.includes('app-stage-skipped')), 'Unused stages must be skipped');
  check(await completed() === '100', 'Subtitles must be 100%');
  await page.getByRole('radio', {name:'Dubbing', exact:true}).check();
  await page.getByRole('button', {name:'Create dubbed video', exact:true}).waitFor();
  stages = await inspect();
  check(stages.slice(0,2).every(s => s.includes('app-stage-done')), 'Retain real completed stages');
  check(stages.slice(2).every(s => !s.includes('app-stage-done')), 'Unrun dubbing/render must not be green');
  check(await completed() === '0', 'Unrun workflow must not show 100%');
  await page.getByRole('radio', {name:'Subtitles', exact:true}).check();
  check(await completed() === '100', 'Switching back must restore subtitle completion');
  // A truly completed dubbing task must still restore all four green stages.
  Object.assign(task, {last_process_route:'dubbing', pipeline_status:'completed',
    dubbing_status:'completed', dubbing_target_lang:'Chinese', dubbing_audio_path:'/dub.wav',
    dubbing_video_path:'/dub.mp4', dubbing_end_note_enabled:true});
  await page.reload();
  await page.getByText('Workflow test.mp4', {exact:true}).first().click();
  await page.getByRole('button', {name:'Create dubbed video', exact:true}).waitFor();
  check((await inspect()).every(s => s.includes('app-stage-done')), 'Real dubbing completion must remain green');
  check(await completed() === '100', 'Real dubbing must be 100%');
  return {passed:true, cases:['subtitles', 'switch to unrun dubbing', 'switch back', 'completed dubbing reload']};
}
