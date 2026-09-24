// Run against Vite on port 4178 using playwright-cli run-code --filename=tests/browser/qwen-settings.js.
// All API requests are mocked; no backend configuration is changed.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => localStorage.removeItem('app_lang'));
  let local = true;
  let runtimeRequests = 0;
  let installStatusRequests = 0;
  const sources = { cuda: { llama_archive: 'D:\\Files\\upstream-downloads\\qwen-cuda\\llama.zip', cuda_archive: 'D:\\Files\\upstream-downloads\\qwen-cuda\\cuda.zip' }, vulkan: { llama_archive: 'D:\\Files\\upstream-downloads\\qwen-vulkan\\llama.zip', cuda_archive: '' }, model_directory: 'D:\\Files\\upstream-downloads\\qwen-model' };
  let config = { TTS_MODE: 'qwen', QWEN_AUTO_CPU_FALLBACK: true, LLM_PROVIDER: 'lm_studio', LM_STUDIO_CLI_PATH: 'lms' };
  let status = { actual_device: 'cpu', state: 'succeeded', fallback_reason: 'CUDA unavailable' };
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('/api/')[1].split('?')[0];
    let body = {};
    if (path === 'tasks') body = [];
    if (path === 'config') {
      if (route.request().method() === 'POST') config = route.request().postDataJSON();
      body = { config, is_local: local };
    }
    if (path === 'config/schema') body = {
      sections: [
        { id: 'translation', label_key: 'settingsSectionTranslation' },
        { id: 'dubbing', label_key: 'settingsSectionDubbing' },
      ],
      fields: [
        { key: 'LLM_PROVIDER', section: 'translation', value_type: 'enum', choices: ['lm_studio'], default: 'lm_studio', label_key: 'llmProvider' },
        { key: 'LM_STUDIO_CLI_PATH', section: 'translation', level: 'advanced', value_type: 'string', default: 'lms', label_key: 'lmStudioCliPath', visible_when: [{ key: 'LLM_PROVIDER', operator: 'equals', value: 'lm_studio' }] },
        { key: 'TTS_MODE', section: 'dubbing', value_type: 'enum', choices: ['kokoro', 'edge', 'qwen'], default: 'kokoro', label_key: 'ttsMode' },
        { key: 'KOKORO_MODEL_PATH', section: 'dubbing', level: 'advanced', value_type: 'path', default: '', label_key: 'kokoroModelPath', visible_when: [{ key: 'TTS_MODE', operator: 'equals', value: 'kokoro' }] },
        { key: 'KOKORO_VOICES_PATH', section: 'dubbing', level: 'advanced', value_type: 'path', default: '', label_key: 'kokoroVoicesPath', visible_when: [{ key: 'TTS_MODE', operator: 'equals', value: 'kokoro' }] },
        { key: 'QWEN_AUTO_CPU_FALLBACK', section: 'dubbing', value_type: 'boolean', default: true, label_key: 'qwenAutoCpuFallback', visible_when: [{ key: 'TTS_MODE', operator: 'equals', value: 'qwen' }] },
      ],
    };
    if (path === 'capabilities') body = { edition: 'standard' };
    if (path === 'lm-studio/status') body = { healthy: true, runtime_ready: true, models: ['translator'] };
    if (path === 'qwen-tts/install-status') { installStatusRequests++; body = { installed: true, managed: false, upstream_sources: sources }; }
    if (path === 'dubbing/voices') body = config.TTS_MODE === 'kokoro'
      ? { tts_mode: 'kokoro', supported_languages: ['Chinese', 'English', 'Japanese'], voices: [] }
      : config.TTS_MODE === 'edge'
        ? { tts_mode: 'edge', supported_languages: ['Chinese', 'English', 'Japanese', 'Korean'], online_languages: ['zh', 'en', 'ja', 'ko'], voices: [] }
        : { tts_mode: 'qwen', supported_languages: ['Chinese', 'English', 'Japanese', 'Korean'], online_languages: [], voices: [] };
    if (path === 'qwen-tts/preflight') body = { ready: true, required_bytes: 1000000000, free_bytes: 2000000000 };
    if (path === 'qwen-tts/install') body = { installed: true, managed: true, model: 'Qwen', device: 'cpu' };
    if (path === 'qwen-tts/runtime-status') { runtimeRequests++; body = status; }
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:4178');
  await page.getByText('Your video workspace').waitFor();
  for (const heading of ['Upload Video', 'Task List', 'Video Preview', 'Process', 'Export']) {
    await page.getByRole('heading', { name: heading, exact: true }).waitFor();
  }
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'System Settings' });
  await dialog.getByText('Advanced Settings', { exact: true }).click();
  await dialog.getByLabel('Compute Type', { exact: true }).waitFor();
  await dialog.getByText('Advanced Settings', { exact: true }).click();
  await dialog.getByRole('button', { name: 'Dubbing', exact: true }).click();
  await dialog.locator('.settings-provider-row button').filter({ hasText: 'Qwen3-TTS' }).waitFor();
  await dialog.getByText('legacy development runtime', { exact: false }).waitFor();
  await dialog.getByText('CPU / Synthesis completed', { exact: false }).waitFor();
  if (await dialog.getByLabel('llama.cpp CUDA archive').count()) throw new Error('Qwen source paths should start collapsed');
  await dialog.getByRole('button', { name: 'Set up Qwen3-TTS' }).click();
  await dialog.getByText('Edit source paths').click();
  const llama = dialog.getByLabel('llama.cpp CUDA archive', { exact: true });
  if (await llama.inputValue() !== sources.cuda.llama_archive) throw new Error('CUDA path was not filled');
  const device = dialog.getByRole('combobox', { name: 'Execution device' });
  await device.selectOption('vulkan');
  if (await dialog.getByLabel('llama.cpp Vulkan archive', { exact: true }).inputValue() !== sources.vulkan.llama_archive) throw new Error('Vulkan path did not change');
  await device.selectOption('cpu');
  await llama.fill('D:\\Custom\\llama.zip');
  await device.selectOption('vulkan');
  if (await dialog.getByLabel('llama.cpp Vulkan archive', { exact: true }).inputValue() !== 'D:\\Custom\\llama.zip') throw new Error('Manual source path was overwritten');
  await device.selectOption('cpu');
  await dialog.getByRole('button', { name: 'Check sources and space' }).click();
  await dialog.getByText('Preflight passed', { exact: false }).waitFor();
  await dialog.getByRole('button', { name: 'Verify and install' }).click();
  await dialog.getByText('Currently installed', { exact: false }).waitFor();
  await dialog.locator('#setting-qwen_auto_cpu_fallback').uncheck();
  await dialog.getByRole('button', { name: 'Kokoro', exact: false }).click();
  if (await dialog.locator('#setting-qwen_auto_cpu_fallback').count()) throw new Error('Qwen fallback leaked into Kokoro');
  await dialog.getByText('Custom Kokoro paths').click();
  await dialog.getByText('not a voice cloning sample', { exact: false }).waitFor();
  await dialog.getByLabel('Preset voices file').waitFor();
  await dialog.getByRole('button', { name: 'Edge voices', exact: false }).click();
  if (await dialog.getByLabel('Preset voices file').count()) throw new Error('Kokoro paths leaked into Edge');
  await dialog.locator('.settings-note').filter({ hasText: 'Audio text is sent' }).waitFor();
  await dialog.getByRole('button', { name: 'Translation', exact: true }).click();
  await dialog.getByRole('button', { name: 'Refresh LM Studio diagnostics' }).waitFor();
  await dialog.getByRole('button', { name: 'Appearance', exact: true }).click();
  await dialog.getByRole('combobox', { name: 'Interface Language' }).selectOption('zh');
  await page.getByRole('dialog').getByRole('button', { name: '保存设置' }).click();
  if (config.QWEN_AUTO_CPU_FALLBACK !== false || config.TTS_MODE !== 'edge' || config.UI_LANGUAGE !== 'zh') throw new Error('Settings did not persist');
  local = false;
  const before = runtimeRequests, beforeInstall = installStatusRequests;
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.waitForTimeout(3300);
  if (runtimeRequests !== before || installStatusRequests !== beforeInstall) throw new Error('Remote settings requested local Qwen status');
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await page.getByRole('dialog').boundingBox();
  if (!bounds || bounds.width > 391 || bounds.x < -1) throw new Error('Settings overflows a mobile viewport');
  await page.getByRole('dialog').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).last().click();
  const header = page.locator('.app-topbar');
  const headerBox = await header.boundingBox();
  const headerOverflows = await header.evaluate(element => element.scrollWidth > element.clientWidth);
  if (!headerBox || headerBox.height > 66 || headerOverflows) throw new Error('Mobile header remains crowded or overflows');
  const mobileNav = page.getByRole('navigation', { name: 'Main navigation' });
  await mobileNav.getByRole('button', { name: '处理', exact: true }).click();
  await page.getByRole('button', { name: '前往任务', exact: true }).click();
  if (await mobileNav.getByRole('button', { name: '任务', exact: true }).getAttribute('aria-current') !== 'page') throw new Error('Process empty-state action did not open tasks');
  await mobileNav.getByRole('button', { name: '导出', exact: true }).click();
  await page.getByText('暂无可导出内容', { exact: true }).waitFor();
  await page.getByRole('button', { name: '前往任务', exact: true }).click();
  await page.unrouteAll({ behavior: 'wait' });
  console.log('PASS: settings flows, remote isolation, compact mobile header and empty-state navigation');
}
