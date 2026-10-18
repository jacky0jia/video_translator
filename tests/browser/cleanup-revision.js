async page => {
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.__streams = []; window.__snapshot = {};
    window.EventSource = class {
      constructor(url) {
        this.url = url; this.listeners = {}; window.__streams.push(this);
        queueMicrotask(() => this.message(window.__snapshot));
      }
      message(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
      addEventListener(name, fn) { this.listeners[name] = fn; }
      close() { this.closed = true; }
    };
  });
  let task, heldDetail, holdDetail = false, pipelinePosts = 0, cancelPosts = 0;
  let holdCleanupDetails = false;
  const cleanupDetails = [];
  const base = id => ({ task_id: id, filename: `${id}.mp4`, target_lang: 'Chinese',
    transcription_path: '/static/output/source.json', translations: { Chinese: '/static/output/target.json' },
    translation_path: '/static/output/target.json', last_process_route: 'dubbing',
    pipeline_run_id: 'run', status: 'dubbing', pipeline_status: 'processing',
    pipeline_stage: 'pipeline_dubbing', progress_percent: 64, state_revision: 10 });
  const emit = async snapshot => page.evaluate(data => {
    window.__snapshot = data;
    window.__streams.findLast(item => !item.closed)?.message(data);
  }, snapshot);
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (endpoint === `tasks/${task.task_id}`) {
      if (holdCleanupDetails) { cleanupDetails.push(route); return; }
      if (holdDetail) { holdDetail = false; heldDetail = route; return; }
      return route.fulfill({ json: { task } });
    }
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', language: 'zh', name: 'Test voice' }] } });
    if (endpoint.endsWith('/cancel')) {
      cancelPosts++;
      task = { ...task, status: 'pipeline_cancelling', pipeline_status: 'cancelling',
        pipeline_cleanup_pending: true, state_revision: 11, cancelled_stage: 'pipeline_dubbing' };
      await emit(task);
      return route.fulfill({ json: { status: 'cancelling', task_id: task.task_id } });
    }
    if (endpoint === 'pipeline') pipelinePosts++;
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Test', confidence: -0.2 }] } }));
  await page.route('**/video/**', route => route.abort());
  const selectTask = async () => {
    await page.evaluate(snapshot => { window.__snapshot = snapshot; }, task);
    await page.locator('.app-task-card').filter({ hasText: task.filename }).click();
    await page.waitForFunction(() => window.__streams.some(item => !item.closed));
  };
  const primary = page.locator('.app-process-primary');
  const results = [];
  task = base('cleanup-state');
  await page.goto('http://127.0.0.1:5189'); await selectTask();
  await page.locator('[role="progressbar"][aria-valuenow="64"]').waitFor();
  holdCleanupDetails = true;
  await page.getByRole('button', { name: 'Cancel processing', exact: true }).click();
  await page.locator('.app-status-cancelling').waitFor();
  if (!await primary.isDisabled() || !await page.getByRole('button', { name: 'Cancel processing', exact: true }).isDisabled()) throw Error('Cleanup buttons are unlocked');
  const note = page.getByText('Cancellation requested. Waiting for background work to stop…', { exact: true });
  await note.waitFor();
  if (await note.evaluate(el => getComputedStyle(el).whiteSpace) !== 'normal') throw Error('Stopping reason is truncated');
  results.push('cancel-request-lock');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.locator('.app-status-cancelling').waitFor();
  if (!await primary.isDisabled()) throw Error('Language change restored stale pre-cancel state');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.locator('.app-status-cancelling').waitFor();
  holdCleanupDetails = false;
  for (const route of cleanupDetails) await route.fulfill({ json: { task } });
  results.push('language-before-http-reconciliation');
  await page.reload(); await selectTask();
  await page.locator('.app-status-cancelling').waitFor();
  if (!await primary.isDisabled()) throw Error('Reload lost pending cleanup');
  await emit({ ...task, state_revision: 10, status: 'completed', pipeline_status: 'completed', pipeline_cleanup_pending: false });
  await page.waitForTimeout(80);
  if (!await primary.isDisabled() || !await page.locator('.app-status-cancelling').count()) throw Error('Old completion unlocked cleanup');
  results.push('reload-and-stale-terminal');
  task = { ...task, state_revision: 12, status: 'cancelled', pipeline_status: 'cancelled', pipeline_cleanup_pending: false };
  await emit(task);
  await page.waitForFunction(() => !document.querySelector('.app-process-primary').disabled);
  await page.locator('.app-status-cancelled').waitFor();
  results.push('cleanup-finished-unlock');

  task = base('revision-order'); heldDetail = null;
  await page.goto('about:blank'); await page.goto('http://127.0.0.1:5189'); await selectTask();
  await page.locator('[role="progressbar"][aria-valuenow="64"]').waitFor();
  holdDetail = true;
  await page.getByRole('button', { name: 'Refresh List', exact: true }).click();
  for (let i = 0; !heldDetail && i < 100; i++) await page.waitForTimeout(20);
  if (!heldDetail) throw Error('Did not hold old details');
  task = { ...task, state_revision: 11, progress_percent: 83 };
  await emit(task);
  await page.locator('[role="progressbar"][aria-valuenow="83"]').waitFor();
  await heldDetail.fulfill({ json: { task: { ...task, state_revision: 10, status: 'failed', pipeline_status: 'failed', message: 'old HTTP failure' } } });
  await emit({ ...task, state_revision: 10, progress_percent: 5 });
  await page.waitForTimeout(100);
  await page.locator('[role="progressbar"][aria-valuenow="83"]').waitFor();
  if (!await primary.isDisabled() || (await page.locator('.app-current-task').innerText()).includes('failed')) throw Error('Old HTTP/SSE state won');
  results.push('http-and-live-version-order');
  await page.evaluate(() => { window.__snapshot = { ...window.__snapshot, state_revision: 10, progress_percent: 5 }; window.__streams.findLast(item => !item.closed).onerror(); });
  await page.waitForFunction(() => window.__streams.length >= 2);
  await page.locator('[role="progressbar"][aria-valuenow="83"]').waitFor();
  task = { ...task, state_revision: 12, status: 'pipeline_rendering', pipeline_stage: 'pipeline_rendering', progress_percent: 0 };
  await emit(task);
  await page.locator('[role="progressbar"][aria-label="Render"][aria-valuenow="0"]').waitFor();
  results.push('reconnect-and-stage-reset');
  if (pipelinePosts || cancelPosts !== 1) throw Error('Unexpected work was started');
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: results, realModelsUsed: false };
}
