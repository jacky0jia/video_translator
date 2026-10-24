// Run against Vite on port 5189 using playwright-cli run-code --filename=tests/browser/settings-modal-layering.js.
// All API requests are mocked; no backend configuration is changed.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const task = {
    task_id: 'modal-layering', filename: 'modal-layering.mp4', status: 'completed', target_lang: 'Chinese',
    transcription_path: '/static/output/modal-source.json', translation_path: '/static/output/modal-target.json',
    translations: { Chinese: '/static/output/modal-target.json' },
  };
  await page.route('**/api/**', route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (path === 'tasks') body = { tasks: [task] };
    if (path === 'tasks/modal-layering') body = { task };
    if (path === 'config') body = { config: {}, is_local: true };
    if (path === 'config/schema') body = { sections: [], fields: [] };
    if (path === 'capabilities') body = { edition: 'standard' };
    if (path === 'dubbing/voices') body = { tts_mode: 'kokoro', voices: [] };
    return route.fulfill({ json: body });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: {
    segments: [{ start: 0, end: 10, text: 'Subtitle must stay behind settings', confidence: 1 }],
  } }));

  await page.goto('http://127.0.0.1:5189');
  await page.getByRole('main').getByText(task.filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #1', exact: true }).waitFor();
  await page.locator('video').evaluate(video => {
    Object.defineProperty(video, 'videoWidth', { value: 1280, configurable: true });
    Object.defineProperty(video, 'videoHeight', { value: 720, configurable: true });
    Object.defineProperty(video, 'currentTime', { value: 1, configurable: true });
    video.dispatchEvent(new Event('loadedmetadata'));
    video.dispatchEvent(new Event('timeupdate'));
  });
  const subtitle = page.locator('[data-subtitle-overlay] [data-subtitle-role="source"]');
  await subtitle.waitFor();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('dialog', { name: 'System Settings', exact: true }).waitFor();

  const topLayer = await subtitle.evaluate(node => {
    // The real overlay intentionally ignores pointer input. Enable hit testing
    // without changing its paint order so elementFromPoint can reveal which
    // visual layer is actually on top.
    node.closest('[data-subtitle-overlay]').style.pointerEvents = 'auto';
    const box = node.getBoundingClientRect();
    const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return {
      insideSettings: Boolean(top?.closest('.settings-overlay')),
      topTag: top?.tagName || null,
      topClass: top?.className || null,
    };
  });
  if (!topLayer.insideSettings) {
    throw new Error(`Preview subtitle outranks the settings modal: ${JSON.stringify(topLayer)}`);
  }
  return { passed: true, checked: 'settings modal covers preview subtitle' };
}
