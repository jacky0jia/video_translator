async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => localStorage.setItem('app_lang', 'en'));
  const task = {
    task_id: 'asr-review', filename: 'asr-review.mp4', status: 'completed', target_lang: 'Chinese',
    transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
    translations: { Chinese: '/static/output/target.json' },
  };
  const source = { segments: [
    { start: 0, end: 8, text: 'Thank you for watching!', confidence: -1.1 },
    ...Array.from({ length: 6 }, (_, i) => ({ start: 16 + i * 8, end: 18 + i * 8, text: 'Thanks for watching.', confidence: -0.2 })),
    { start: 60, end: 61, text: '', confidence: -0.2 },
    { start: 63.5, end: 64, text: 'You', confidence: -0.8 },
  ] };
  const target = { segments: source.segments.map((row, index) => ({ ...row, text: index === 7 ? '' : `Translation ${index + 1}` })) };
  const writes = [];
  await page.route('**/api/**', async route => {
    const path = '/api/' + route.request().url().split('/api/')[1].split('?')[0];
    if (path.startsWith('/api/tasks/asr-review/segments/')) {
      const index = Number(path.split('/').at(-1));
      const body = route.request().postDataJSON();
      writes.push({ index, body });
      if (body.source_text !== undefined) Object.assign(source.segments[index], { text: body.source_text, source_text_edited: true });
      if (body.target_text !== undefined) target.segments[index].text = body.target_text;
      return route.fulfill({ json: { status: 'success' } });
    }
    return route.fulfill({ json: path === '/api/tasks' ? { tasks: [task] } : path === '/api/tasks/asr-review' ? { task } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json', route => route.fulfill({ json: route.request().url().endsWith('source.json') ? source : target }));
  const url = 'http://127.0.0.1:5189';
  const openTask = async () => {
    await page.goto(url);
    await page.getByText(task.filename, { exact: true }).click();
    await page.getByTestId('asr-review-notice').getByRole('button', { name: '#7', exact: true }).waitFor();
    await page.locator('video').evaluate(video => {
      Object.defineProperty(video, 'duration', { value: 64, configurable: true });
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    await page.getByTestId('asr-review-notice').getByRole('button', { name: '#9', exact: true }).waitFor();
  };
  await openTask();
  const notice = page.getByTestId('asr-review-notice');
  if (await notice.getByRole('button').count() !== 8) throw Error('Expected opening, six repeated rows and ending');
  if (!await notice.innerText().then(text => text.includes('not confirmed errors'))) throw Error('Review was presented as a definite error');
  await page.getByTitle('Collapse subtitle preview').click();
  await notice.getByRole('button', { name: '#7', exact: true }).click();
  await page.waitForFunction(() => document.activeElement?.id === 'subtitle-row-7');
  if (!await page.locator('#subtitle-row-7').getAttribute('title').then(text => text.includes('repeated text'))) throw Error('Missing repeated-text explanation');

  const edit = async (number, field, value, keyboard = false) => {
    const row = page.locator(`#subtitle-row-${number}`);
    const cell = row.getByRole('button', { name: `${field === 'source' ? 'Source' : 'Target'} #${number}`, exact: true });
    if (keyboard) { await cell.focus(); await cell.press('Enter'); } else { await cell.click(); }
    await row.locator('textarea').fill(value);
    const response = page.waitForResponse(res => res.url().includes(`/segments/${number - 1}`) && res.request().method() === 'POST');
    await row.getByRole('button', { name: 'Save', exact: true }).click();
    await response;
    await row.locator('textarea').waitFor({ state: 'hidden' });
    await row.locator('td').nth(field === 'source' ? 1 : 2).getByText(value, { exact: true }).waitFor();
  };
  await edit(1, 'source', 'Corrected opening');
  if (await notice.getByRole('button', { name: '#1', exact: true }).count()) throw Error('Manual source correction remained flagged');
  await edit(8, 'source', 'Restored source', true);
  await edit(8, 'target', 'Restored target');
  await openTask();
  await page.locator('#subtitle-row-8').getByText('Restored source', { exact: true }).waitFor();
  await page.locator('#subtitle-row-8').getByText('Restored target', { exact: true }).waitFor();
  if (await notice.getByRole('button', { name: '#1', exact: true }).count()) throw Error('Refresh lost manual provenance');
  if (writes.length !== 3 || source.segments.length !== 9 || source.segments[8].text !== 'You') throw Error('Unexpected deletion or writes');
  await page.screenshot({ path: '.codex-test-runs/ghost-text-20261017/transcription-review-ui.png', fullPage: true });
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  return { checks: ['review rows', 'jump after collapse', 'manual correction', 'restore empty source/target', 'refresh persistence', 'no auto deletion'], writes: writes.length };
}
