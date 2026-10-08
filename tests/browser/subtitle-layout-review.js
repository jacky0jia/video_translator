async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => localStorage.setItem('app_lang', 'en'));
  const task = {
    task_id: 'subtitle-review', filename: 'subtitle-review.mp4', status: 'completed', target_lang: 'Chinese',
    transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
    translations: { Chinese: '/static/output/target.json' },
    translation_alignment_review: { Chinese: { rows: [6, 15, 205, 216, 217] } },
  };
  const text = '这就像是一个巨大的求和芯片，分布在一个挤满人的房间里，每个人同时完成一小部分工作。AI 的大部分算术运算也可以以同样的方式拆分。';
  let authoritativeReview = false;
  await page.route('**/api/**', route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    return route.fulfill({ json: path === 'tasks' ? { tasks: [task] } : path === 'tasks/subtitle-review' ? { task } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json', route => route.fulfill({ json: {
    segments: Array.from({ length: 62 }, (_, index) => ({ start: index * 2, end: index * 2 + 2, text: index === 0 ? text : `Row ${index + 1}` })),
    ...(route.request().url().endsWith('target.json') && authoritativeReview ? { alignment_review_rows: [2, 62, 217] } : {}),
  } }));
  const url = 'http://127.0.0.1:5188';
  await page.goto(url);
  await page.getByText(task.filename, { exact: true }).click();
  await page.getByRole('button', { name: '#15', exact: true }).waitFor();
  for (const number of [205, 216, 217]) {
    if (await page.getByRole('button', { name: `#${number}`, exact: true }).count()) throw Error('Out-of-range review link remained visible');
  }
  await page.getByTitle('Collapse subtitle preview').click();
  await page.getByRole('button', { name: '#15', exact: true }).click();
  await page.waitForFunction(() => document.activeElement?.id === 'subtitle-row-15');
  await page.locator('video').evaluate(video => {
    Object.defineProperty(video, 'videoWidth', { value: 1920, configurable: true });
    Object.defineProperty(video, 'videoHeight', { value: 1080, configurable: true });
    video.style.aspectRatio = '16 / 9';
    video.dispatchEvent(new Event('loadedmetadata'));
    video.dispatchEvent(new Event('timeupdate'));
  });
  await page.waitForFunction(() => {
    const overlay = document.querySelector('video').parentElement.querySelector('.pointer-events-none');
    const video = document.querySelector('video');
    return Math.abs(overlay.getBoundingClientRect().width / video.getBoundingClientRect().width - 0.9) < 0.01;
  });
  authoritativeReview = true;
  await page.reload();
  await page.getByText(task.filename, { exact: true }).click();
  await page.getByRole('button', { name: '#62', exact: true }).waitFor();
  if (await page.getByRole('button', { name: '#6', exact: true }).count()) throw Error('Stale task-level review replaced current translation review');
  await page.getByRole('button', { name: '#62', exact: true }).click();
  await page.waitForFunction(() => document.activeElement?.id === 'subtitle-row-62');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
}
