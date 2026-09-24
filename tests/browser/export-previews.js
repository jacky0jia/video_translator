// Run against Vite on port 4178 with playwright-cli run-code --filename=tests/browser/export-previews.js.
// All task and output requests are mocked; no real media or settings are changed.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    localStorage.setItem('theme', 'dark');
  });
  const task = {
    task_id: 'export-preview', filename: 'sample.mp4', status: 'completed', target_lang: 'Japanese',
    created_at: '2026-09-19T00:00:00', last_subtitle_format: 'srt', last_process_route: 'dubbing',
    subtitle_outputs: { srt: '/static/output/sample.srt' },
    dubbing_audio_path: '/static/output/sample.wav', dubbing_video_path: '/static/output/sample.mp4',
    transcription_path: '/static/output/source.json', translations: { Japanese: '/static/output/target.json' },
  };
  let subtitleRequests = 0;
  let missing = false;
  let large = false;
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    const body = path === 'tasks' ? { tasks: [task] } : path === 'tasks/export-preview' ? { task } :
      path === 'config' ? { config: {}, is_local: true } : path === 'config/schema' ? { sections: [], fields: [] } :
        path === 'capabilities' ? { edition: 'standard' } : path === 'dubbing/voices' ? { supported_languages: ['Japanese'], voices: [] } : {};
    await route.fulfill({ json: body });
  });
  await page.route('**/static/output/source.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Original mobile line' }] } }));
  await page.route('**/static/output/target.json', route => route.fulfill({ json: { segments: [{ start: 0, end: 2, text: 'Translated mobile line' }] } }));
  await page.route('**/static/output/sample.srt', async route => {
    subtitleRequests++;
    if (route.request().headers().range !== 'bytes=0-262144') throw Error('Subtitle preview did not cap its request');
    if (missing) return route.fulfill({ status: 404, body: 'not found' });
    if (large) return route.fulfill({ status: 200, contentType: 'text/plain', body: 'A'.repeat(300000) });
    await route.fulfill({ status: 206, contentType: 'text/plain', body: '1\n00:00:00,000 --> 00:00:02,000\nTranslated line\n' });
  });
  await page.route('**/static/output/sample.wav', async () => new Promise(() => {}));
  await page.route('**/static/output/sample.mp4', async () => new Promise(() => {}));

  await page.goto('http://127.0.0.1:4178');
  await page.getByText('sample.mp4', { exact: true }).click();
  const doneStageBars = page.locator('.app-stage-done > span');
  if (await doneStageBars.count() < 3) throw Error('Completed workflow stages are missing');
  for (const bar of await doneStageBars.all()) {
    const background = await bar.evaluate(element => getComputedStyle(element).backgroundColor);
    if (background !== 'rgb(16, 185, 129)') throw Error(`Dark completed stage lost its status color: ${background}`);
  }
  await page.getByRole('button', { name: 'Subtitle Style' }).click();
  const boldButton = page.getByTitle('Bold');
  const moveButton = page.getByTitle('Move subtitles up');
  const [boldBox, moveBox] = await Promise.all([boldButton.boundingBox(), moveButton.boundingBox()]);
  if (!boldBox || !moveBox || boldBox.width !== moveBox.width || boldBox.height !== moveBox.height) throw Error('Bold control size differs from the icon controls');
  const fontSelect = page.getByLabel('Source & target font');
  const fontOption = fontSelect.locator('option').first();
  const optionColors = await fontOption.evaluate(element => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, color: style.color };
  });
  if (optionColors.background !== 'rgb(20, 35, 56)' || optionColors.color !== 'rgb(237, 242, 247)') throw Error(`Dark font options are unreadable: ${JSON.stringify(optionColors)}`);
  const subtitleButton = page.getByRole('button', { name: 'Preview Japanese Subtitles' });
  if (subtitleRequests) throw Error('Subtitle fetched before preview opened');
  await subtitleButton.click();
  await page.getByText('Translated line', { exact: false }).waitFor();
  if (subtitleRequests < 1 || await subtitleButton.getAttribute('aria-expanded') !== 'true') throw Error('Subtitle preview state incorrect');
  const firstRequests = subtitleRequests;
  if (await page.getByRole('link', { name: 'Download Japanese Subtitles' }).getAttribute('download') === null) throw Error('Download changed');
  await subtitleButton.click();
  missing = true;
  await subtitleButton.click();
  await page.getByRole('alert').filter({ hasText: 'This output is unavailable' }).waitFor();
  if (subtitleRequests <= firstRequests) throw Error('Missing subtitle was not rechecked');
  await subtitleButton.click();
  missing = false;
  large = true;
  await subtitleButton.click();
  await page.getByText('Showing the first 256 KiB.', { exact: false }).waitFor();
  if ((await page.locator('pre').textContent()).length > 262144) throw Error('Subtitle preview exceeded size cap');

  await page.getByRole('button', { name: 'Preview Japanese Dubbing' }).click();
  await page.locator('audio[controls][src="/static/output/sample.wav"]').waitFor();
  await page.getByRole('button', { name: 'Preview Dubbed Video' }).click();
  await page.locator('video[controls][src="/static/output/sample.mp4"]').waitFor();
  await page.screenshot({ path: '.codex-test-runs/export-preview-design-20260919.png' });
  await page.setViewportSize({ width: 360, height: 800 });
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Preview' }).click();
  await page.getByRole('button', { name: 'Expand subtitle preview' }).click();
  await page.getByText('Translated mobile line', { exact: true }).waitFor();
  if (await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)) throw Error('Mobile subtitle cards overflow horizontally');
  const targetCell = page.locator('td[data-label="Target"]');
  if (!await targetCell.isVisible()) throw Error('Target subtitle is not visible in the mobile timeline');
  const mobileNav = page.getByRole('navigation', { name: 'Main navigation' });
  await mobileNav.getByRole('button', { name: 'Process' }).click();
  const [actionBox, navBox] = await Promise.all([page.locator('.app-process-primary').boundingBox(), mobileNav.boundingBox()]);
  if (!actionBox || !navBox || actionBox.y + actionBox.height > navBox.y - 3) throw Error('Mobile process action is covered by bottom navigation');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  if (await page.getByText('Jacky Jia').count()) throw Error('Personal author name remains in About');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  console.log('PASS: dark workflow stages and style controls, lazy bounded subtitle preview, missing output, audio/video controls, download, author removal');
}
