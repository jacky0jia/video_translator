async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const task = { task_id: 'fullscreen', filename: 'fullscreen-fixture.mp4', status: 'completed', target_lang: 'Chinese',
    transcription_path: '/static/output/fs-source.json', translation_path: '/static/output/fs-target.json',
    translations: { Chinese: '/static/output/fs-target.json' } };
  await page.route('**/api/**', route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    return route.fulfill({ json: path === 'tasks' ? { tasks: [task] } : path === 'tasks/fullscreen' ? { task } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: {
    segments: [{ start: 0, end: 10, text: 'Subtitle inside the actual picture', confidence: 1 }],
  } }));
  await page.goto('http://127.0.0.1:5189');
  await page.getByRole('main').getByText(task.filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).waitFor();
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const bounds = () => page.locator('video').evaluate(video => {
    const v = video.getBoundingClientRect(), w = video.parentElement.getBoundingClientRect();
    const overlay = video.parentElement.querySelector('[data-subtitle-overlay]').getBoundingClientRect();
    return { video: { x: v.x, y: v.y, width: v.width, height: v.height, bottom: v.bottom },
      wrapper: { x: w.x, y: w.y, width: w.width, height: w.height, bottom: w.bottom },
      baseline: overlay.bottom, controls: video.controls, fit: getComputedStyle(video).objectFit };
  });
  const assertFillsScreen = (actual, name) => {
    for (const field of ['x', 'y', 'width', 'height']) {
      if (Math.abs(actual.video[field] - actual.wrapper[field]) > 1) throw Error(`${name}: video ${field}=${actual.video[field]}, fullscreen container ${field}=${actual.wrapper[field]}`);
    }
    if (!actual.controls || actual.fit !== 'contain') throw Error(`${name}: native controls or uncropped picture fit was lost`);
  };
  const measurements = [];
  // These baselines are hand-derived for a 1920x1080 viewport and the existing
  // 80-source-pixel bottom margin, not computed with the production helper.
  for (const [name, width, height, expectedBaseline, resizedBaseline] of [
    ['wide', 1920, 800, 860, 663.333333], ['portrait', 1080, 1920, 1035, 862.5], ['standard', 1920, 1080, 1000, 756.666667],
  ]) {
    await page.locator('video').evaluate((video, { width, height }) => {
      Object.defineProperty(video, 'videoWidth', { value: width, configurable: true });
      Object.defineProperty(video, 'videoHeight', { value: height, configurable: true });
      Object.defineProperty(video, 'currentTime', { value: 1, configurable: true });
      video.style.aspectRatio = `${width}/${height}`;
      video.dispatchEvent(new Event('loadedmetadata')); video.dispatchEvent(new Event('timeupdate'));
    }, { width, height });
    await settle();
    const embedded = await bounds();
    await page.getByTitle('Fullscreen', { exact: true }).click({ force: true });
    await page.waitForFunction(() => !!document.fullscreenElement);
    await settle();
    const fullscreen = await bounds();
    assertFillsScreen(fullscreen, name);
    if (Math.abs(fullscreen.baseline - expectedBaseline) > 1) throw Error(`${name}: subtitle baseline ${fullscreen.baseline}, expected ${expectedBaseline}`);
    await page.setViewportSize({ width: 1280, height: 900 });
    await settle();
    const resized = await bounds();
    assertFillsScreen(resized, `${name} resized`);
    if (Math.abs(resized.baseline - resizedBaseline) > 1) throw Error(`${name}: resized subtitle baseline ${resized.baseline}, expected ${resizedBaseline}`);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await settle();
    await page.getByTitle('Exit Fullscreen', { exact: true }).click({ force: true });
    await page.waitForFunction(() => !document.fullscreenElement);
    await settle();
    const restored = await bounds();
    for (const field of ['width', 'height']) if (Math.abs(restored.video[field] - embedded.video[field]) > 1) throw Error(`${name}: embedded ${field} did not restore`);
    measurements.push({ name, videoHeight: fullscreen.video.height, wrapperHeight: fullscreen.wrapper.height, baseline: fullscreen.baseline });
  }
  // Another element entering fullscreen must not resize this preview.
  const beforeOther = await bounds();
  await page.evaluate(() => {
    const button = document.createElement('button'); button.id = 'other-fullscreen-fixture'; button.textContent = 'Other fullscreen fixture';
    button.onclick = () => button.requestFullscreen(); document.body.appendChild(button);
  });
  await page.locator('#other-fullscreen-fixture').click();
  await page.waitForFunction(() => document.fullscreenElement?.id === 'other-fullscreen-fixture');
  await settle();
  const duringOther = await bounds();
  if (Math.abs(beforeOther.video.height - duringOther.video.height) > 1) throw Error('Unrelated fullscreen resized preview');
  if (await page.getByTitle('Exit Fullscreen', { exact: true }).count()) throw Error('Unrelated fullscreen marked preview as fullscreen');
  await page.evaluate(() => document.exitFullscreen());
  await page.waitForFunction(() => !document.fullscreenElement);
  if (errors.length) throw Error(errors.join('\n'));
  return { passed: true, measurements, checked: ['wide', 'portrait', 'standard', 'resize', 'exit restoration', 'fullscreen ownership', 'native controls retained'] };
}
