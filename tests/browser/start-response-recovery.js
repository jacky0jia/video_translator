async page => {
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    if (location.protocol === 'http:') localStorage.setItem('app_lang', 'en');
    window.__snapshots = {}; window.__streams = [];
    window.EventSource = class {
      constructor(url) {
        this.id = url.split('/')[3]; this.listeners = {}; window.__streams.push(this);
        queueMicrotask(() => this.message(window.__snapshots[this.id] || {}));
      }
      message(data) { if (!this.closed) this.onmessage?.({ data: JSON.stringify(data) }); }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() { this.closed = true; }
    };
  });
  for (const [edition, port] of [['public', 18885], ['supporter', 18886]]) {
    for (const mode of ['completed-http', 'failed-http', 'cancelled-http', 'completed-network',
      'before-response', 'old-completed', 'preflight-rejected', 'stale-completed', 'recover-active', 'task-switch']) {
      await page.unrouteAll({ behavior: 'wait' });
      const name = `${edition}-${mode}.mp4`;
      const base = (id, filename) => ({ task_id: id, filename, target_lang: 'Chinese',
        created_at: '2026-10-20T12:00:00', status: 'failed', pipeline_status: 'failed',
        pipeline_run_id: 'old-run', state_revision: 10, message: 'Old failure', failed_stage: 'pipeline_dubbing',
        last_process_route: 'dubbing', transcription_path: '/static/output/source.json',
        translation_path: '/static/output/target.json', translations: { Chinese: '/static/output/target.json' },
      });
      let current = base('A', name);
      const finish = (terminal, extra = {}) => Object.assign(current, {
        status: terminal, pipeline_status: terminal, pipeline_stage: terminal,
        pipeline_run_id: 'new-run', state_revision: 12, progress_percent: terminal === 'completed' ? 100 : 42,
        message: `Backend ${terminal}`, failed_stage: terminal === 'failed' ? 'pipeline_rendering' : null,
        dubbing_status: terminal, dubbing_target_lang: 'Chinese', dubbing_end_note_enabled: true,
        dubbing_audio_path: '/static/output/a.wav', dubbing_video_path: '/static/output/a.mp4', ...extra,
      });
      if (['old-completed', 'preflight-rejected'].includes(mode)) finish('completed', { pipeline_run_id: 'old-run', state_revision: 10 });
      const other = { ...base('B', 'Other task.mp4'), status: 'dubbing', pipeline_status: 'processing',
        pipeline_stage: 'pipeline_dubbing', pipeline_run_id: 'B-run', state_revision: 20, progress_percent: 42 };
      let posts = 0, release;
      await page.route('**/api/**', async route => {
        const endpoint = route.request().url().split('/api/')[1].split('?')[0];
        if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [current, other] } });
        if (['tasks/A', 'tasks/B'].includes(endpoint)) {
          const task = endpoint === 'tasks/A' ? current : other;
          await page.evaluate(task => { window.__snapshots[task.task_id] = task; }, task);
          return route.fulfill({ json: { task } });
        }
        if (endpoint === 'config') return route.fulfill({ json: { config: { UI_LANGUAGE: 'en' }, is_local: true } });
        if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen',
          voices: [{ id: 'voice', language: 'zh', name: 'Test voice' }], supported_languages: ['Chinese'] } });
        if (endpoint.endsWith('/workflow-options')) {
          current.workflow_options = route.request().postDataJSON(); current.state_revision = 11;
          return route.fulfill({ json: { workflow_options: current.workflow_options, state_revision: 11 } });
        }
        if (endpoint.endsWith('/video-plan')) return route.fulfill(mode === 'preflight-rejected'
          ? { status: 400, json: { detail: 'Preflight rejected' } }
          : { json: { source: {}, plan: { encoder: 'cpu_test', codec: 'h264', width: 1920, height: 1080,
            reason: 'source_preserved', software: true } } });
        if (endpoint === 'pipeline') {
          posts++;
          if (mode === 'old-completed') finish('completed', { pipeline_run_id: 'old-run', state_revision: 12 });
          else if (mode === 'stale-completed') finish('completed', { state_revision: 9 });
          else if (mode === 'recover-active') finish('processing', { status: 'dubbing', pipeline_stage: 'pipeline_dubbing', dubbing_status: 'processing' });
          else finish(mode.startsWith('failed') ? 'failed' : mode.startsWith('cancelled') ? 'cancelled' : 'completed');
          await page.evaluate(task => { window.__snapshots.A = task; }, current);
          await new Promise(resolve => { release = resolve; });
          if (mode === 'completed-network') return route.abort('connectionreset');
          return route.fulfill({ status: 502, json: { detail: 'Start response unavailable' } });
        }
        return route.fulfill({ json: {} });
      });
      await page.route('**/static/output/*.json', route => route.fulfill({ json: {
        segments: [{ start: 0, end: 1, text: 'Fixture', confidence: 1 }],
      } }));
      await page.goto(`http://127.0.0.1:${port}`);
      await page.getByText(name, { exact: true }).click();
      const panel = page.locator('[data-page="process"]');
      const primary = panel.locator('.app-process-primary');
      await page.waitForFunction(() => document.querySelector('.app-process-primary')?.disabled === false);
      await primary.click();
      if (mode !== 'preflight-rejected') {
        const deadline = Date.now() + 10000;
        while (!release && Date.now() < deadline) await page.waitForTimeout(10);
        if (!release) throw new Error(`${edition}/${mode}: pipeline request was not sent`);
        if (!await primary.isDisabled()) throw new Error(`${edition}/${mode}: unlocked before response`);
        if (mode === 'before-response') {
          await page.getByRole('button', { name: 'Refresh List', exact: true }).click();
          await page.locator('.app-current-task').getByText('completed · Chinese', { exact: true }).waitFor();
          if (!await primary.isDisabled()) throw new Error('New snapshot unlocked an in-flight start');
        }
        if (mode === 'task-switch') {
          await page.getByText('Other task.mp4', { exact: true }).click();
          await panel.locator('.app-status-processing').waitFor();
        }
        release();
      }
      const expected = ['old-completed', 'preflight-rejected', 'stale-completed'].includes(mode) ? 'failed'
        : ['recover-active', 'task-switch'].includes(mode) ? 'processing'
        : mode.startsWith('failed') ? 'failed' : mode.startsWith('cancelled') ? 'cancelled' : 'completed';
      await panel.locator(`.app-status-${expected}`).waitFor();
      if (mode === 'failed-http') await panel.getByText('Backend failed', { exact: true }).waitFor();
      if (mode === 'cancelled-http') await panel.getByText('Backend cancelled', { exact: true }).waitFor();
      if (['old-completed', 'stale-completed'].includes(mode)) await panel.getByText('Start response unavailable', { exact: true }).waitFor();
      if (mode === 'preflight-rejected') await panel.getByText('Preflight rejected', { exact: true }).waitFor();
      if (posts !== (mode === 'preflight-rejected' ? 0 : 1)) throw new Error(`${mode}: duplicate start`);
      if (mode === 'task-switch') {
        if (!await primary.isDisabled()) throw new Error('Old A failure unlocked B');
        await page.locator('.app-current-task').getByText('Other task.mp4', { exact: true }).waitFor();
      } else if (mode === 'recover-active') {
        finish('completed', { state_revision: 13 });
        await page.evaluate(task => window.__streams.filter(stream => stream.id === 'A' && !stream.closed).at(-1)?.message(task), current);
        await panel.locator('.app-status-completed').waitFor();
      } else if (expected === 'completed' && await primary.isDisabled()) throw new Error('Completion stayed locked');
      results.push(`${edition}: ${mode}`);
    }
  }
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'wait' });
  if (errors.length) throw new Error(JSON.stringify(errors));
  return { passed: results, pageErrors: errors };
}
