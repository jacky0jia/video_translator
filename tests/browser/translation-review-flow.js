async page => {
  await page.goto('about:blank');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch { /* about:blank has no storage */ }
    window.__streams = []; window.__snapshot = {};
    window.EventSource = class {
      constructor(url) { this.url = url; window.__streams.push(this); queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(window.__snapshot) })); }
      addEventListener() {}
      close() { this.closed = true; }
    };
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let task, text = '译文', generation = 0, translations = 0, exports = 0, approvals = 0;
  const pipelines = [], results = [];
  const base = id => ({ task_id: id, filename: `${id}.mp4`, status: 'transcribed', target_lang: 'Chinese',
    transcription_path: '/static/output/source.json', translations: {}, last_process_route: 'subtitles',
    state_revision: 1, created_at: '2026-10-18T00:00:00' });
  const publish = async () => page.evaluate(snapshot => {
    window.__snapshot = snapshot;
    window.__streams.filter(stream => !stream.closed).forEach(stream => stream.onmessage?.({ data: JSON.stringify(snapshot) }));
  }, task);
  const pause = () => {
    task = { ...task, status: 'awaiting_review', pipeline_status: 'awaiting_review', pipeline_stage: null,
      progress_percent: 0, translations: { Chinese: '/static/output/target.json' },
      translation_path: '/static/output/target.json', state_revision: task.state_revision + 1,
      translation_reviews: { Chinese: { review_id: `review-${++generation}`, status: 'pending' } } };
  };
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = request.url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (endpoint === `tasks/${task.task_id}`) return route.fulfill({ json: { task } });
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', name: 'Test voice', language: 'zh' }] } });
    if (endpoint.endsWith('/video-plan')) return route.fulfill({ json: { source: { width: 1920, height: 1080, codec: 'h264' }, plan: { reason: 'source_preserved', codec: 'h264', width: 1920, height: 1080 } } });
    if (endpoint === 'translate') {
      translations++; pause(); await publish();
      return route.fulfill({ json: { status: 'success', review_required: true, review: task.translation_reviews.Chinese, state_revision: task.state_revision } });
    }
    if (endpoint.endsWith('/translation-review')) {
      const data = request.postDataJSON(), review = task.translation_reviews.Chinese;
      if (data.review_id !== review.review_id) return route.fulfill({ status: 409, json: { detail: 'Wrong review version' } });
      approvals++; task = { ...task, status: 'transcribed', pipeline_status: null, state_revision: task.state_revision + 1,
        translation_reviews: { Chinese: { ...review, status: 'approved' } } };
      return route.fulfill({ json: { approved: true, review: task.translation_reviews.Chinese, state_revision: task.state_revision } });
    }
    if (endpoint.endsWith('/segments/0')) {
      text = request.postDataJSON().target_text; pause();
      return route.fulfill({ json: { status: 'success', updated: ['target'] } });
    }
    if (endpoint === 'export') {
      if (task.translation_reviews.Chinese.status !== 'approved') throw Error('Unapproved export');
      exports++; task = { ...task, status: 'completed', pipeline_status: null, state_revision: task.state_revision + 1,
        subtitle_outputs: { srt: '/static/output/translated.srt' } };
      return route.fulfill({ json: { status: 'success' } });
    }
    if (endpoint === 'pipeline') {
      const data = request.postDataJSON(); pipelines.push(data);
      if (!data.review_id) { pause(); }
      else {
        if (data.review_id !== task.translation_reviews.Chinese.review_id) throw Error('Pipeline used an old review token');
        task = { ...task, status: 'pipeline_dubbing', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing',
          state_revision: task.state_revision + 1, progress_percent: 0 };
      }
      await publish();
      return route.fulfill({ json: { status: 'started' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: { language: 'en', segments: [
    { start: 0, end: 2, text: route.request().url().includes('target.json') ? text : 'Original sentence', confidence: 1 },
  ] } }));
  await page.route('**/video/**', route => route.abort());
  const open = async () => {
    await page.goto('about:blank'); await page.goto('http://127.0.0.1:5189');
    await page.locator('.app-task-card').filter({ hasText: task.filename }).click();
  };
  const primary = page.locator('.app-process-primary');
  const waiting = async () => {
    await page.getByRole('button', { name: 'Continue after review', exact: true }).waitFor();
    if (await primary.isDisabled()) throw Error('Review continue button is disabled');
    if (await primary.count() !== 1) throw Error('Extra primary action added');
    if (await page.locator('.app-stage-done').count() !== 2) throw Error('Review stage bar must show only ASR and translation complete');
  };
  task = base('subtitle-review'); await open();
  await primary.click(); await waiting();
  if (translations !== 1 || exports || approvals || pipelines.length) throw Error('Translation did not stop before downstream work');
  results.push('subtitle-translation-pauses-before-export');
  await page.reload();
  await page.locator('.app-task-card').filter({ hasText: task.filename }).click();
  await waiting();
  if (approvals || exports) throw Error('Reload silently approved or exported');
  results.push('reload-restores-review-without-approval');
  await page.getByRole('button', { name: 'Target #1', exact: true }).click();
  await page.locator('textarea').fill('人工修正后的译文');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: 'Target #1', exact: true }).filter({ hasText: '人工修正后的译文' }).waitFor();
  await waiting();
  await primary.click(); await page.locator('.app-status-completed').waitFor();
  if (translations !== 1 || approvals !== 1 || exports !== 1) throw Error('Continue retranslated or failed to export edited version');
  results.push('edited-version-continues-on-existing-button');
  await page.getByRole('button', { name: 'Re-translate', exact: true }).click(); await waiting();
  if (translations !== 2 || exports !== 1 || approvals !== 1) throw Error('Retranslation reused old approval');
  results.push('retranslation-requires-new-review');
  await page.getByRole('radio', { name: 'Dubbing', exact: true }).check();
  const launched = page.waitForResponse(response => response.url().endsWith('/api/pipeline'));
  await primary.click();
  await launched;
  await page.waitForFunction(() => document.querySelector('.app-status-processing'));
  await page.waitForFunction(() => document.querySelector('.app-process-primary')?.disabled);
  if (pipelines.length !== 1 || pipelines[0].force_translate || pipelines[0].review_id !== task.translation_reviews.Chinese.review_id) throw Error('Dubbing continuation lost review or regenerated translation');
  results.push('route-switch-continues-reviewed-script');
  task = { ...base('dubbing-review'), last_process_route: 'dubbing' };
  await open(); await primary.click(); await waiting();
  if (pipelines.length !== 2 || pipelines[1].review_id) throw Error('Initial dubbing launch implicitly approved translation');
  results.push('dubbing-translation-stops-before-tts');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.getByRole('button', { name: '确认字幕并继续', exact: true }).waitFor();
  if (pipelines.length !== 2) throw Error('Language switch started processing');
  results.push('language-switch-keeps-review-idle');
  if (errors.length) throw Error(errors.join('\n'));
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed: results, realModelsUsed: false };
}
