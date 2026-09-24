// Run against Vite on port 4178 using playwright-cli run-code --filename=tests/browser/tts-provider-race.js.
// All API requests are mocked; the first catalog responses deliberately ignore cancellation.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem('app_lang', 'en');
    const nativeFetch = window.fetch.bind(window);
    let staleCatalogs = 0;
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url.includes('/api/dubbing/voices') && staleCatalogs < 2) {
        staleCatalogs += 1;
        return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify({
          tts_mode: 'kokoro',
          supported_languages: ['Chinese', 'English', 'Japanese'],
          voices: [{ id: 'af_heart', name: 'Kokoro Heart', language: 'en' }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })), 700));
      }
      return nativeFetch(input, init);
    };
  });

  const task = {
    task_id: 'provider-race', filename: 'provider-race.mp4', status: 'transcribed',
    target_lang: 'English', transcription_path: '/static/output/source.json',
    translations: {}, translation_profiles: {}, created_at: '2026-09-24T00:00:00',
  };
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (path === 'tasks') body = { tasks: [task] };
    if (path === 'tasks/provider-race') body = { task };
    if (path === 'config') body = { config: { TTS_MODE: 'qwen' }, is_local: true };
    if (path === 'capabilities') body = { edition: 'standard' };
    if (path === 'dubbing/voices') body = {
      tts_mode: 'qwen',
      supported_languages: ['Chinese', 'English', 'Japanese', 'Korean'],
      voices: [{ id: 'en_female_1', name: 'Qwen English Female', language: 'en' }],
    };
    await route.fulfill({ json: body });
  });
  await page.route('**/static/output/source.json', route => route.fulfill({
    json: { segments: [{ start: 0, end: 1, text: 'Hello' }] },
  }));

  await page.goto('http://127.0.0.1:4178');
  await page.getByText('provider-race.mp4', { exact: true }).waitFor();
  await page.getByText('provider-race.mp4', { exact: true }).click();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('settings-changed')));
  await page.getByRole('radio', { name: 'Dubbing', exact: true }).check();
  const voice = page.getByRole('combobox', { name: 'Voice', exact: true });
  await voice.selectOption('en_female_1');
  await page.waitForTimeout(900);
  if (await voice.inputValue() !== 'en_female_1') throw new Error('A stale Kokoro catalog replaced the Qwen catalog');
  if (await voice.locator('option[value="af_heart"]').count()) throw new Error('A stale Kokoro voice remained visible');
  const language = page.getByRole('combobox', { name: 'Target Language', exact: true });
  if (!await language.locator('option[value="Korean"]').count()) throw new Error('A stale Kokoro language catalog replaced Qwen languages');
  await page.unrouteAll({ behavior: 'wait' });
  console.log('PASS: stale TTS catalogs cannot replace the latest provider catalog');
}
