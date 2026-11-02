async page => {
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.__snapshot = {}; window.__streams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; window.__streams.push(this); queueMicrotask(() => this.send(window.__snapshot)); }
      send(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
      addEventListener() {}
      close() { this.closed = true; }
    };
  });
  const errors = [], pipelines = [], passed = [];
  page.on('pageerror', error => errors.push(error.message));
  let edits = 0, forbidden = 0;
  const issue = indices => ({ segment_indices: indices, group_index: 500, raw_seconds: 12,
    fit_seconds: 7.5, available_seconds: 4, excess_seconds: 3.5 });
  let task = { task_id: 'manual-dub', filename: 'manual-dub.mp4', target_lang: 'Chinese', state_revision: 20,
    status: 'awaiting_dubbing_edit', pipeline_status: 'awaiting_dubbing_edit', dubbing_status: 'awaiting_edit',
    transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
    translations: { Chinese: '/static/output/target.json' }, last_process_route: 'dubbing', compression_attempts: 2,
    translation_reviews: { Chinese: { status: 'approved', review_id: 'original' } },
    dubbing_review: { status: 'required', target_lang: 'Chinese', rows: [6, 7, 999], issues: [issue([5, 6])], needs_recheck: false } };
  const texts = Array.from({ length: 9 }, (_, index) => `Target sentence ${index + 1}.`);
  const publish = async () => page.evaluate(data => {
    window.__snapshot = data;
    window.__streams.filter(stream => !stream.closed).forEach(stream => stream.send(data));
  }, task);
  await page.route('**/api/**', async route => {
    const endpoint = route.request().url().split('/api/')[1].split('?')[0];
    if (endpoint === 'tasks') return route.fulfill({ json: { tasks: [task] } });
    if (endpoint === 'tasks/manual-dub') return route.fulfill({ json: { task } });
    if (endpoint === 'dubbing/voices') return route.fulfill({ json: { tts_mode: 'qwen', voices: [{ id: 'zh_female_1', language: 'zh', name: 'Test voice' }] } });
    if (endpoint.endsWith('/video-plan')) return route.fulfill({ json: { source: { width: 1920, height: 1080, codec: 'h264' }, plan: { reason: 'source_preserved', codec: 'h264', width: 1920, height: 1080 } } });
    if (endpoint.includes('/segments/')) {
      const index = Number(endpoint.split('/').at(-1)); texts[index] = route.request().postDataJSON().target_text; edits++;
      task = { ...task, status: 'awaiting_review', pipeline_status: 'awaiting_review', state_revision: task.state_revision + 1,
        dubbing_review: { ...task.dubbing_review, needs_recheck: true },
        translation_reviews: { Chinese: { review_id: `edited-${edits}`, status: 'pending' } } };
      return route.fulfill({ json: { status: 'success', updated: ['target'] } });
    }
    if (endpoint === 'translate' || endpoint.endsWith('/compression')) { forbidden++; throw Error('Unexpected LLM/compression request'); }
    if (endpoint === 'pipeline') {
      const data = route.request().postDataJSON(); pipelines.push(data);
      if (data.force_translate || data.review_id !== task.translation_reviews.Chinese.review_id) throw Error('Manual continue did not use edited version');
      task = { ...task, status: 'dubbing', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing',
        dubbing_status: 'processing', state_revision: task.state_revision + 1,
        translation_reviews: { Chinese: { ...task.translation_reviews.Chinese, status: 'approved' } } };
      await publish(); return route.fulfill({ json: { status: 'started' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: { segments: texts.map((text, index) => ({
    start: index * 2, end: index * 2 + 1, confidence: 1,
    text: route.request().url().includes('target.json') ? text : `Original sentence ${index + 1}.`,
  })) } }));
  await page.route('**/video/**', route => route.abort());
  const open = async () => {
    await page.goto('http://127.0.0.1:5189'); await page.locator('.app-task-card').filter({ hasText: task.filename }).click();
  };
  const notice = page.getByTestId('dubbing-sync-notice'), primary = page.locator('.app-process-primary');
  await open(); await page.locator('.app-status-awaiting_dubbing_edit').waitFor();
  await notice.waitFor();
  if ((await notice.getByRole('link').allTextContents()).join(',') !== '#6,#7') throw Error('Original-row mapping or bounds are wrong');
  if (!(await notice.innerText()).includes('7.50s')) throw Error('Measurements are missing');
  if (!(await page.locator('#subtitle-row-6').getAttribute('class')).includes('bg-rose')) throw Error('Problem row is not marked');
  if ((await page.locator('#subtitle-row-1').getAttribute('class')).includes('bg-rose')) throw Error('Speech group number was used as source row number');
  if (await primary.count() !== 1 || await primary.isDisabled()) throw Error('Existing continue button was not reused');
  passed.push('original-rows-measurements-and-existing-button');
  await notice.getByRole('link', { name: '#7', exact: true }).click();
  await page.waitForFunction(() => document.activeElement?.id === 'subtitle-row-7');
  passed.push('row-link-expands-scrolls-and-focuses');
  await page.reload(); await page.locator('.app-task-card').filter({ hasText: task.filename }).click();
  await page.locator('.app-status-awaiting_dubbing_edit').waitFor(); await notice.waitFor();
  if (pipelines.length) throw Error('Reload started work');
  passed.push('reload-keeps-manual-waiting-and-marks');
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.getByRole('button', { name: '确认字幕并继续', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Switch interface language', exact: true }).click();
  await page.getByRole('button', { name: 'Continue after review', exact: true }).waitFor();
  passed.push('language-change-keeps-recovery-idle');
  const edit = async row => {
    await page.getByRole('button', { name: `Target #${row}`, exact: true }).click();
    await page.locator('textarea').fill('Shorter.'); await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.locator('.app-status-awaiting_review').waitFor();
    await notice.getByText('Subtitles changed. These are previous measurements; marks remain until speech is measured again.', { exact: true }).waitFor();
  };
  const resume = async () => {
    const response = page.waitForResponse(item => item.url().endsWith('/api/pipeline'));
    await primary.click(); await response;
    await page.locator('.app-status-processing').waitFor();
    if (!await primary.isDisabled()) throw Error('Resume is not locked while measuring');
  };
  await edit(6);
  if ((await notice.getByRole('link').allTextContents()).join(',') !== '#6,#7') throw Error('Editing prematurely cleared old measurements');
  await resume();
  task = { ...task, status: 'awaiting_dubbing_edit', pipeline_status: 'awaiting_dubbing_edit',
    dubbing_status: 'awaiting_edit', pipeline_stage: null, state_revision: task.state_revision + 1,
    dubbing_review: { status: 'required', target_lang: 'Chinese', rows: [7], issues: [issue([6])], needs_recheck: false } };
  await publish(); await page.locator('.app-status-awaiting_dubbing_edit').waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="dubbing-sync-notice"]')?.querySelectorAll('a').length === 1);
  if (await notice.getByRole('link').innerText() !== '#7') throw Error('Resolved rows were not removed after remeasurement');
  passed.push('edited-text-remeasures-and-updates-only-remaining-rows');
  await edit(7); await resume();
  task = { ...task, state_revision: task.state_revision + 1, dubbing_review: null,
    status: 'dubbing', pipeline_status: 'processing', message: '正在合并音视频...', progress_percent: 90 };
  await publish(); await notice.waitFor({ state: 'hidden' });
  if (!await primary.isDisabled()) throw Error('Clearing timing marks pretended the whole workflow was complete');
  passed.push('marks-clear-on-measurement-not-on-edit-or-final-render');
  task = { ...task, status: 'completed', pipeline_status: 'completed', dubbing_status: 'completed',
    dubbing_target_lang: 'Chinese', dubbing_audio_path: '/static/output/audio.wav', dubbing_video_path: '/static/output/video.mp4',
    dubbing_end_note_enabled: true, state_revision: task.state_revision + 1 };
  await publish(); await page.locator('.app-status-completed').waitFor();
  if (pipelines.length !== 2 || forbidden || edits !== 2) throw Error('Unexpected retries, LLM calls or edits');
  if (errors.length) throw Error(errors.join('\n'));
  await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { passed, realModelsUsed: false, additionalWorkflowButtons: 0 };
}
