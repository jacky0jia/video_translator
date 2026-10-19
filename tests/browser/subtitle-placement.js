async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    try { localStorage.setItem('app_lang', 'en'); } catch {}
    window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  });
  const task = { task_id: 'placement', filename: 'placement.mp4', status: 'completed', target_lang: 'Chinese',
    transcription_path: '/static/output/source.json', translation_path: '/static/output/target.json',
    translations: { Chinese: '/static/output/target.json' } };
  const spans = [[0, 3], [1, 7], [4, 6], [8, 10], [8, 10]];
  await page.route('**/api/**', route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    return route.fulfill({ json: path === 'tasks' ? { tasks: [task] } : path === 'tasks/placement' ? { task } : {} });
  });
  await page.route('**/video/**', route => route.abort());
  await page.route('**/static/output/*.json*', route => route.fulfill({ json: {
    segments: spans.map(([start, end], i) => ({ start, end, confidence: 1,
      text: route.request().url().includes('source') ? `Source ${i + 1}` : i === 1 ? '这是较长的一条译文，需要换行也要完整显示，不覆盖之前的字幕。'.repeat(3) : `译文 ${i + 1}` })) } }));
  await page.goto('http://127.0.0.1:5189');
  await page.getByText(task.filename, { exact: true }).click();
  await page.getByRole('button', { name: 'Source #2', exact: true }).waitFor();
  await page.locator('video').evaluate(video => {
    Object.defineProperty(video, 'videoWidth', { value: 1920, configurable: true });
    Object.defineProperty(video, 'videoHeight', { value: 1080, configurable: true });
    Object.defineProperty(video, 'currentTime', { value: 0, writable: true, configurable: true });
    video.style.aspectRatio = '16 / 9';
    video.dispatchEvent(new Event('loadedmetadata'));
    video.dispatchEvent(new Event('timeupdate'));
  });
  await page.locator('[data-subtitle-block]').first().waitFor();
  const seek = async time => {
    await page.locator('video').evaluate((video, t) => { video.currentTime = t; video.dispatchEvent(new Event('seeked')); }, time);
    await page.waitForFunction(t => {
      const rows = [...document.querySelectorAll('[data-subtitle-block]')].map(n => Number(n.dataset.subtitleBlock.split(':')[0]));
      return t === 1.5 ? rows.join() === '1,2' : t === 3.5 ? rows.join() === '2' : t === 4.5 ? rows.join() === '2,3' : rows.join() === '4,5';
    }, time);
  };
  const bounds = () => page.locator('[data-subtitle-block]').evaluateAll(nodes => nodes.map(node => {
    const r = node.getBoundingClientRect();
    return { row: Number(node.dataset.subtitleBlock.split(':')[0]), top: r.top, bottom: r.bottom, height: r.height };
  }));
  // Direct seek has the same stable position as linear playback.
  await seek(3.5);
  const direct = (await bounds())[0];
  await seek(1.5);
  const both = await bounds();
  if (!(both[1].bottom < both[0].top && both[1].height > both[0].height)) throw Error('Multiline block failed height-aware avoidance');
  if (Math.abs(both[1].top - direct.top) > 1) throw Error('Direct seek changed placement');
  await seek(3.5);
  if (Math.abs((await bounds())[0].top - direct.top) > 1) throw Error('Later subtitle fell down');
  await seek(4.5);
  const reuse = await bounds();
  if (!(reuse[1].top > reuse[0].bottom)) throw Error('Freed lower space not reused');
  await seek(8.5);
  const equal = await bounds();
  if (!(equal[1].bottom < equal[0].top)) throw Error('Fully overlapping rows were hidden or collided');
  if (await page.locator('[data-subtitle-overflow]').count()) throw Error('Ordinary overlaps incorrectly flagged');
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.waitForFunction(() => {
    const [a, b] = [...document.querySelectorAll('[data-subtitle-block]')].map(n => n.getBoundingClientRect());
    return a && b && b.bottom < a.top;
  });
  await page.getByRole('checkbox', { name: 'Source', exact: true }).uncheck();
  await page.waitForFunction(() => !document.querySelector('[data-subtitle-overlay] [data-subtitle-role="source"]'));
  if (await page.locator('[data-subtitle-block]').count() !== 2) throw Error('Target-only toggle lost rows');
  await page.getByTitle('Fullscreen', { exact: true }).click({ force: true });
  await page.waitForFunction(() => document.fullscreenElement !== null);
  await page.waitForFunction(() => {
    const [a, b] = [...document.querySelectorAll('[data-subtitle-block]')].map(n => n.getBoundingClientRect());
    return a && b && b.bottom < a.top;
  });
  await page.evaluate(() => document.exitFullscreen());
  return { passed: true, checked: ['multiline', 'bilingual', 'stable-seek', 'no-fall', 'reuse-space', 'identical-times', 'resize', 'toggle', 'fullscreen'] };
}
