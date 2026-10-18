async page => {
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.__streams = [];
    window.__snapshots = {};
    window.EventSource = class {
      constructor(url) {
        this.url = url; this.listeners = {}; window.__streams.push(this);
        queueMicrotask(() => this.message(window.__snapshots[url.split('/tasks/')[1].split('/')[0]] || {}));
      }
      message(event) { this.onmessage?.({ data: JSON.stringify(event) }); }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() { this.closed = true; }
    };
  });
  let tasks, mode, armed, heldA, heldB, postCounts;
  const plan = { source: { codec: 'h264', width: 1920, height: 1080 },
    plan: { codec: 'h264', encoder: 'cpu_test', width: 1920, height: 1080, bitrate: 5000000, software: false, reason: 'source_preserved' } };
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: Object.values(tasks) } });
    if (endpoint === 'tasks/A' || endpoint === 'tasks/B') return route.fulfill({ json: { task: tasks[endpoint.at(-1)] } });
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', name: 'Test voice', language: 'zh' }] } });
    if (endpoint.endsWith('/video-plan')) {
      if (endpoint === 'tasks/A/video-plan' && armed && mode === 'preflight-failure') { heldA = route; return; }
      return route.fulfill({ json: plan });
    }
    if (endpoint === 'pipeline') {
      const id = route.request().postDataJSON().task_id;
      postCounts[id]++;
      if (id === 'A') heldA = route; else heldB = route;
      return;
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Source', confidence: -0.2 }] } }));
  await page.route('**/video/**', route => route.abort());
  const results = [];
  for (mode of ['preflight-failure', 'post-failure', 'post-success']) {
    tasks = Object.fromEntries(['A', 'B'].map(id => [id, {
      task_id: id, filename: `Scope-${id}.mp4`, status: 'transcribed', target_lang: 'Chinese',
      last_process_route: 'dubbing', transcription_path: '/static/output/source.json',
      translations: { Chinese: '/static/output/target.json' }, translation_path: '/static/output/target.json',
    }]));
    armed = false; heldA = null; heldB = null; postCounts = { A: 0, B: 0 };
    await page.goto('http://127.0.0.1:5189');
    const history = page.locator('.app-task-card');
    await history.filter({ hasText: 'Scope-A.mp4' }).click();
    const primary = page.locator('.app-process-primary');
    await page.getByRole('button', { name: 'Create dubbed video', exact: true }).waitFor();
    await page.waitForTimeout(400); // finish the independent folded plan preview
    armed = true; await primary.click();
    for (let i = 0; !heldA && i < 100; i++) await page.waitForTimeout(20);
    if (!heldA) throw Error(`${mode}: A request not held`);
    if (!await primary.isDisabled()) throw Error(`${mode}: A start is not locked`);
    await history.filter({ hasText: 'Scope-B.mp4' }).click();
    await page.waitForFunction(() => !document.querySelector('.app-process-primary')?.disabled);
    if (!(await page.locator('.app-current-task').innerText()).includes('Scope-B.mp4')) throw Error('B not selected');
    await primary.click();
    for (let i = 0; !heldB && i < 100; i++) await page.waitForTimeout(20);
    if (!heldB) throw Error(`${mode}: B inherited A startup lock`);
    if (mode === 'post-success') {
      tasks.A = { ...tasks.A, status: 'pipeline_starting', pipeline_status: 'processing' };
      await heldA.fulfill({ json: { status: 'started', task_id: 'A' } });
    } else {
      await heldA.fulfill({ status: 400, json: { detail: 'late failure A' } });
    }
    await page.waitForTimeout(150);
    if (!(await page.locator('.app-current-task').innerText()).includes('Scope-B.mp4')) throw Error('A response stole selection');
    if (!await primary.isDisabled()) throw Error(`${mode}: A finally unlocked B`);
    if (await page.getByText('late failure A', { exact: true }).count()) throw Error('Old A error notified B');
    if (postCounts.B !== 1) throw Error('Unexpected duplicate B start');
    tasks.B = { ...tasks.B, status: 'dubbing', pipeline_status: 'processing',
      pipeline_stage: 'pipeline_dubbing', progress_percent: 42, message: 'B synthesizing' };
    await page.evaluate(snapshot => { window.__snapshots.B = snapshot; }, tasks.B);
    await heldB.fulfill({ json: { status: 'started', task_id: 'B' } });
    await page.waitForFunction(() => window.__streams.some(item => item.url.includes('/B/events') && !item.closed));
    await page.evaluate(snapshot => window.__streams.findLast(item => item.url.includes('/B/events') && !item.closed).message(snapshot), tasks.B);
    await page.locator('[role="progressbar"][aria-label="Dub"][aria-valuenow="42"]').waitFor();
    if (!await primary.isDisabled()) throw Error('B progress is not locked');
    results.push(mode);
    await page.goto('about:blank');
  }
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: results, realModelsUsed: false };
}
