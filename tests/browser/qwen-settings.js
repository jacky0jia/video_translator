// Run against Vite on port 4178 using playwright-cli run-code --filename=tests/browser/qwen-settings.js.
// All API requests are mocked; no backend configuration is changed.
async page => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
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
    if (path === 'qwen-tts/runtime-status') { runtimeRequests++; body = status; }
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:4178');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByText('Jacky Jia', { exact: true }).waitFor();
  await page.getByText('Services & diagnostics', { exact: true }).click();
  const ttsSelect = page.getByLabel('TTS Mode');
  const ttsOptions = await ttsSelect.locator('option').allTextContents();
  if (ttsOptions.join(',') !== 'kokoro,edge,qwen') throw new Error(`Unexpected TTS choices: ${ttsOptions}`);
  await page.getByRole('alert').filter({ hasText: 'legacy development runtime' }).waitFor();
  const llama = page.getByLabel('llama.cpp CUDA archive', { exact: true });
  await llama.waitFor();
  if (await llama.inputValue() !== sources.cuda.llama_archive) throw new Error('CUDA path was not filled');
  if (await page.getByLabel('Qwen model directory', { exact: true }).inputValue() !== sources.model_directory) throw new Error('Model path was not filled');
  const device = page.getByRole('combobox', { name: /Execution device/ });
  await device.selectOption('vulkan');
  if (await page.getByLabel('llama.cpp Vulkan archive', { exact: true }).inputValue() !== sources.vulkan.llama_archive) throw new Error('Vulkan path did not change');
  await device.selectOption('cpu');
  if (await llama.inputValue() !== sources.cuda.llama_archive) throw new Error('CPU did not use CUDA/CPU archive');
  await llama.fill('D:\\Custom\\llama.zip');
  await device.selectOption('vulkan');
  if (await page.getByLabel('llama.cpp Vulkan archive', { exact: true }).inputValue() !== 'D:\\Custom\\llama.zip') throw new Error('Custom archive path overwritten');
  await device.selectOption('cpu');
  const translation = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Translation', exact: true }) });
  await translation.getByRole('button', { name: 'Refresh LM Studio diagnostics', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Show advanced settings (1)', exact: true }).click();
  await page.getByLabel('LM Studio CLI Path').waitFor();
  await page.getByRole('alert').filter({ hasText: 'CUDA unavailable' }).waitFor();
  await page.getByText('CPU / Synthesis completed', { exact: false }).waitFor();
  const checkbox = page.locator('#setting-qwen_auto_cpu_fallback');
  await checkbox.uncheck();
  await page.getByRole('button', { name: 'Save Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByText('Services & diagnostics', { exact: true }).click();
  if (await checkbox.isChecked() || config.QWEN_AUTO_CPU_FALLBACK !== false) throw new Error('Fallback preference did not persist as boolean');
  await page.getByRole('combobox', { name: /Interface language/ }).selectOption('zh');
  await page.getByText('CPU / 合成完成', { exact: false }).waitFor();
  status = { actual_device: 'cpu', state: 'failed', fallback_reason: 'CUDA unavailable' };
  await page.getByText('CPU / 合成失败', { exact: false }).waitFor();
  status = { actual_device: 'cpu', state: 'cancelled', fallback_reason: null };
  await page.getByRole('status').filter({ hasText: 'CPU / 已取消' }).waitFor();
  if (await page.getByRole('alert').filter({ hasText: 'CUDA unavailable' }).count()) throw new Error('Stale fallback warning remained');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  local = false;
  const before = runtimeRequests;
  const beforeInstall = installStatusRequests;
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: '保存设置', exact: true }).waitFor();
  // Observe an entire polling interval, including the initial config request.
  await page.waitForTimeout(3300);
  if (runtimeRequests !== before) throw new Error('Remote settings requested local runtime status');
  if (installStatusRequests !== beforeInstall) throw new Error('Remote settings requested local source paths');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  local = true;
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByText('Services & diagnostics', { exact: true }).click();
  await page.getByLabel('TTS 模式').selectOption('edge');
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.getByLabel('目标语言').locator('option[value="Korean"]').waitFor({ state: 'attached' });
  const edgeLanguages = await page.getByLabel('目标语言').locator('option').allTextContents();
  if (edgeLanguages.join(',') !== 'Chinese,English,Japanese,Korean') throw new Error(`Unexpected Edge languages: ${edgeLanguages}`);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByText('Services & diagnostics', { exact: true }).click();
  await page.getByLabel('TTS 模式').selectOption('kokoro');
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.getByLabel('目标语言').locator('option[value="Korean"]').waitFor({ state: 'detached' });
  const uploadLanguages = await page.getByLabel('目标语言').locator('option').allTextContents();
  if (uploadLanguages.includes('Korean')) throw new Error('Korean remained available for Kokoro');
  if (config.UI_LANGUAGE !== 'zh') throw new Error('Settings did not persist Chinese to the application');
  await page.reload();
  await page.getByLabel('目标语言').waitFor();
  if (await page.evaluate(() => localStorage.getItem('app_lang')) !== 'zh') throw new Error('Saved Chinese did not restore with empty browser storage');
  local = false;
  await page.reload();
  await page.getByLabel('Target Language').waitFor();
  local = true;
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Save Settings', exact: true }).click();
  if (config.UI_LANGUAGE !== 'en') throw new Error('Explicit English did not replace saved Chinese');
  await page.reload();
  await page.getByLabel('Target Language').waitFor();
  await page.unrouteAll({ behavior: 'wait' });
  console.log('PASS: settings feedback, provider languages, fallback states, remote request isolation');
}
