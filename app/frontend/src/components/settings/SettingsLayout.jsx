import SettingsField from './SettingsField';
import QwenRuntimeStatus from './QwenRuntimeStatus';
import { InterfaceSettingsSection } from '../SettingsSections';

export default function SettingsLayout(props) {
  const { t, lang, setLanguage, theme, setTheme, onClose, overlayPointerDownRef, handleSubmit,
    hardware, isLocal, upstreamDependencies, schemaError, activeSection, setActiveSection,
    inputBg, inputBorder, textMain, textMuted, asrMode, handleAsrModeChange, fieldsByKey,
    fieldProps, asrModelInfo, config, handleChange, renderFields, sectionFields, renderAdvanced,
    lmStudioRuntimeReady, lmStudioStatusError, fetchingModels, setLmStudioRefreshToken,
    qwenInstallStatus, qwenRuntimeStatus, qwenSetupOpen, setQwenSetupOpen, qwenRepairOpen,
    setQwenRepairOpen, qwenInstall, qwenInstalling, qwenPreflighting, qwenPreflight,
    qwenInstallResult, qwenSourcesReady, qwenNeedsCudaArchive, updateQwenInstall,
    handleQwenPreflight, handleQwenInstall, gib } = props;
  const sections = [
    ['transcription', 'settingsSectionTranscription', 'settingsTranscriptionHint'],
    ['translation', 'settingsSectionTranslation', 'settingsTranslationHint'],
    ['dubbing', 'settingsSectionDubbing', 'settingsDubbingHint'],
    ['video', 'settingsSectionVideo', 'settingsVideoHint'],
    ['appearance', 'appearance', 'settingsAppearanceHint'],
  ];
  const active = sections.find(item => item[0] === activeSection) || sections[0];
  const field = key => fieldsByKey[key] && <SettingsField field={fieldsByKey[key]} {...fieldProps} />;
  return <div className="settings-overlay" onPointerDown={event => { overlayPointerDownRef.current = event.target === event.currentTarget; }}
    onClick={event => { const outside = overlayPointerDownRef.current && event.target === event.currentTarget; overlayPointerDownRef.current = false; if (outside) onClose(); }}>
    <div className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header className="settings-header">
        <div><p className="settings-eyebrow">VIDEO TRANSLATOR</p><h2 id="settings-title">{t('settings')}</h2><p>{t('settingsIntro')}</p></div>
        <button type="button" onClick={onClose} className="settings-close" aria-label={t('cancel')}>&times;</button>
      </header>
      <form onSubmit={handleSubmit} className="settings-form">
        <nav className="settings-nav" aria-label={t('settings')}>
          {sections.map(([id, label]) => <button key={id} type="button" aria-current={activeSection === id ? 'page' : undefined}
            className={activeSection === id ? 'is-active' : ''} onClick={() => setActiveSection(id)}>{t(label)}</button>)}
        </nav>
        <main className="settings-main">
          <div className="settings-section-heading"><div><p className="settings-eyebrow">{t('settings')}</p><h3>{t(active[1])}</h3><p>{t(active[2])}</p></div></div>
          {schemaError && <p role="alert" className="settings-alert">{t('settingsSchemaLoadFailed')}</p>}
          {isLocal === false && activeSection !== 'appearance' && <p className="settings-note">{t('remoteSettingsHint')}</p>}
          {activeSection === 'transcription' && <div className="settings-card">
            <h4>{t('asrMode')}</h4><p className="settings-card-hint">{t('settingsAsrHint')}</p>
            <div className="settings-choice-row">
              {isLocal && <button type="button" className={asrMode === 'local' ? 'is-selected' : ''} onClick={() => handleAsrModeChange('local')}>{t('asrLocalModel')}</button>}
              <button type="button" className={asrMode === 'remote' ? 'is-selected' : ''} onClick={() => handleAsrModeChange('remote')}>{t('asrRemoteApi')}</button>
            </div>
            <div className="settings-grid">
              {asrMode === 'local' && isLocal && field('ASR_MODEL_PATH')}
              {asrMode === 'remote' && <>{field('ASR_API_URL')}{field('ASR_REMOTE_MODEL')}</>}
              {asrMode === 'remote' && asrModelInfo && <p className="settings-note">{asrModelInfo.message}</p>}
              {renderFields(sectionFields('transcription'))}
            </div>
            {renderAdvanced('transcription')}
          </div>}
          {activeSection === 'translation' && <div className="settings-card">
            <h4>{t('llmProvider')}</h4><p className="settings-card-hint">{t('settingsTranslationProviderHint')}</p>
            <div className="settings-choice-row">
              {[['lm_studio', 'LM Studio'], ['ollama', 'Ollama'], ['openai_compatible', 'OpenAI-compatible']].map(([value, label]) =>
                <button key={value} type="button" className={config.LLM_PROVIDER === value ? 'is-selected' : ''} onClick={() => handleChange('LLM_PROVIDER', value)}>{label}</button>)}
            </div>
            <div className="settings-grid">{renderFields(sectionFields('translation').filter(item => item.key !== 'LLM_PROVIDER'))}</div>
            {config.LLM_PROVIDER === 'lm_studio' && <div className="settings-inline-status">
              {lmStudioRuntimeReady === false && <p role="alert">{t('lmStudioRuntimeMissing')}</p>}
              {lmStudioStatusError && <p role="alert">{t('lmStudioDiagnosticsUnavailable')}</p>}
              <button type="button" disabled={fetchingModels} onClick={() => setLmStudioRefreshToken(token => token + 1)}>{fetchingModels ? t('detecting') : t('refreshLmStudioDiagnostics')}</button>
            </div>}
            {renderAdvanced('translation')}
          </div>}
          {activeSection === 'dubbing' && <div className="settings-card">
            <h4>{t('ttsMode')}</h4><p className="settings-card-hint">{t('settingsSpeechHint')}</p>
            <div className="settings-provider-row">
              {[['kokoro', 'Kokoro', 'settingsKokoroSummary'], ['edge', 'Edge voices', 'settingsEdgeSummary'], ['qwen', 'Qwen3-TTS', 'settingsQwenSummary']].map(([value, label, hint]) =>
                <button key={value} type="button" className={config.TTS_MODE === value ? 'is-selected' : ''} onClick={() => handleChange('TTS_MODE', value)}><strong>{label}</strong><span>{t(hint)}</span></button>)}
            </div>
            {config.TTS_MODE === 'edge' && <p className="settings-note">{t('settingsEdgeNotice')}</p>}
            {config.TTS_MODE === 'kokoro' && <>
              {upstreamDependencies?.missing?.length > 0 && <p className="settings-note">{t('upstreamDependenciesMissing')}: {upstreamDependencies.missing.join(', ')}. <a href="/api/dependencies/install-guide" target="_blank" rel="noreferrer">{t('upstreamDependenciesGuide')}</a></p>}
              <details className="settings-details"><summary>{t('settingsCustomKokoroPaths')}</summary><p className="settings-card-hint">{t('settingsKokoroVoicesHint')}</p><div className="settings-grid">{field('KOKORO_MODEL_PATH')}{field('KOKORO_VOICES_PATH')}</div></details>
            </>}
            {config.TTS_MODE === 'qwen' && <div className="settings-runtime">
              {qwenInstallStatus?.installed && <p role="status" className="settings-note">{qwenInstallStatus.managed ? t('qwenCurrentlyInstalled') : t('qwenLegacyRuntimeDetected')}{qwenInstallStatus.managed && `: ${qwenInstallStatus.runtime_version} · ${qwenInstallStatus.device?.toUpperCase()} · ${gib(qwenInstallStatus.installed_bytes)} GiB`}</p>}
              <QwenRuntimeStatus status={qwenRuntimeStatus} t={t} className="settings-card-hint" />
              {qwenInstallStatus?.managed && !qwenRepairOpen && <button type="button" className="settings-secondary" onClick={() => { setQwenRepairOpen(true); setQwenSetupOpen(true); }}>{t('qwenRepairRuntime')}</button>}
              {qwenInstallStatus && (!qwenInstallStatus.managed || qwenRepairOpen) && <>
                <button type="button" className="settings-secondary" aria-expanded={qwenSetupOpen} onClick={() => setQwenSetupOpen(value => !value)}>{t(qwenSetupOpen ? 'settingsHideQwenSetup' : 'settingsOpenQwenSetup')}</button>
                {qwenSetupOpen && <div className="settings-setup">
                  <p className="settings-card-hint">{t('settingsQwenSetupHint')} <a href="/api/dependencies/install-guide" target="_blank" rel="noreferrer">{t('upstreamDependenciesGuide')}</a></p>
                  <label>{t('qwenInstallDevice')}<select value={qwenInstall.device} disabled={qwenInstalling} onChange={event => updateQwenInstall('device', event.target.value)}><option value="cuda">NVIDIA CUDA</option><option value="vulkan">AMD Vulkan</option><option value="cpu">CPU</option></select></label>
                  <details className="settings-details"><summary>{t('settingsQwenSourcePaths')}</summary><p className="settings-card-hint">{t('qwenSourcesAutofilled')}</p><div className="settings-grid">
                    {[
                      ['llama_archive', qwenInstall.device === 'vulkan' ? 'qwenVulkanArchive' : 'qwenLlamaArchive'],
                      ...(qwenNeedsCudaArchive ? [['cuda_archive', 'qwenCudaArchive']] : []),
                      ['model_directory', 'qwenModelDirectory'],
                    ].map(([key, label]) => <label key={key}>{t(label)}<input type="text" value={qwenInstall[key]} disabled={qwenInstalling} onChange={event => updateQwenInstall(key, event.target.value)} /></label>)}
                  </div></details>
                  <div className="settings-actions"><button type="button" className="settings-secondary" disabled={qwenPreflighting || qwenInstalling || !qwenSourcesReady} onClick={handleQwenPreflight}>{t(qwenPreflighting ? 'qwenPreflighting' : 'qwenPreflight')}</button><button type="button" className="settings-primary" disabled={qwenInstalling || !qwenPreflight?.ready} onClick={handleQwenInstall}>{t(qwenInstalling ? 'qwenInstalling' : 'qwenInstall')}</button></div>
                  {qwenPreflight && <p role={qwenPreflight.ready ? 'status' : 'alert'} className="settings-note">{t(qwenPreflight.ready ? 'qwenPreflightReady' : 'qwenPreflightNoSpace')}: {gib(qwenPreflight.required_bytes)} GiB / {gib(qwenPreflight.free_bytes)} GiB</p>}
                  {qwenInstallResult?.installed && <p role="status" className="settings-note">{t('qwenInstallReady')}: {qwenInstallResult.model} · {qwenInstallResult.device.toUpperCase()}</p>}
                </div>}
              </>}
              <div className="settings-grid">{renderFields(sectionFields('dubbing').filter(item => item.key === 'QWEN_AUTO_CPU_FALLBACK'))}</div>
            </div>}
            {renderAdvanced('dubbing', ['KOKORO_MODEL_PATH', 'KOKORO_VOICES_PATH'])}
          </div>}
          {activeSection === 'video' && <div className="settings-card"><h4>{t('settingsVideoHeading')}</h4><p className="settings-card-hint">{t('settingsVideoHint')}</p><div className="settings-grid">{renderFields(sectionFields('video'))}</div>{renderAdvanced('video')}</div>}
          {activeSection === 'appearance' && <div className="settings-card"><h4>{t('appearance')}</h4><p className="settings-card-hint">{t('settingsAppearanceHint')}</p><InterfaceSettingsSection lang={lang} setLanguage={setLanguage} theme={theme} setTheme={setTheme} inputBg={inputBg} inputBorder={inputBorder} textMain={textMain} t={t} />{hardware && <p className="settings-card-hint">{t('detected')}: {hardware[2]}</p>}</div>}
        </main>
        <footer className="settings-footer"><button type="button" className="settings-secondary" onClick={onClose}>{t('cancel')}</button><button type="submit" className="settings-primary">{t('saveSettings')}</button></footer>
      </form>
    </div>
  </div>;
}
