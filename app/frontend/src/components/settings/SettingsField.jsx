export default function SettingsField({ field, config, onChange, t, inputBg, inputBorder, textMain, models = [], fetchingModels, asrModelInfo, onBrowse }) {
  const isModel = ['LLM_MODEL_NAME', 'OLLAMA_MODEL', 'LM_STUDIO_MODEL'].includes(field.key);
  const inputType = field.value_type === 'secret' ? 'password' : ['integer', 'number'].includes(field.value_type) ? 'number' : 'text';
  const step = field.value_type === 'integer' ? 1 : field.value_type === 'number' ? 'any' : undefined;
  const className = 'app-input w-full';
  const inputId = `setting-${field.key.toLowerCase()}`;
  if (field.value_type === 'boolean') return <label htmlFor={inputId} className="settings-checkbox">
    <input id={inputId} type="checkbox" className="h-4 w-4 shrink-0 accent-indigo-600" checked={config[field.key] ?? field.default ?? false} onChange={event => onChange(field.key, event.target.checked)} />
    <span><strong>{t(field.label_key)}</strong>{field.help_key && <small>{t(field.help_key)}</small>}</span>
  </label>;
  return (
    <div className={field.span === 2 ? 'md:col-span-2' : ''}>
      <label htmlFor={inputId} className="app-field-label">{t(field.label_key)}</label>
      {field.help_key && <p className="settings-card-hint mb-1">{t(field.help_key)}</p>}
      {field.value_type === 'enum' ? (
        <select id={inputId} value={config[field.key] ?? ''} onChange={event => onChange(field.key, event.target.value)} className={className}>
          {(field.choices || []).map(choice => <option key={choice} value={choice}>{choice}</option>)}
        </select>
      ) : field.value_type === 'path' ? (
        <div>
          <div className="flex gap-2">
            <input id={inputId} type="text" value={config[field.key] ?? ''} onChange={event => onChange(field.key, event.target.value)} placeholder={field.placeholder_key ? t(field.placeholder_key) : ''} className={`flex-1 ${className}`} />
            <button type="button" onClick={() => onBrowse?.(field.key)} className="settings-secondary">{t('browse')}</button>
          </div>
          {field.key === 'ASR_MODEL_PATH' && asrModelInfo && <p className={`text-xs mt-1 ${asrModelInfo.isValid ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>{asrModelInfo.message}</p>}
        </div>
      ) : isModel && models.length > 0 ? (
        <div className="flex gap-2">
          <select id={inputId} value={config[field.key] ?? ''} onChange={event => onChange(field.key, event.target.value)} className={`flex-1 ${className}`}>
            {models.map(model => <option key={model} value={model}>{model}</option>)}
          </select>
          {fetchingModels && <span className="text-xs text-slate-400 self-center">...</span>}
        </div>
      ) : (
        <input id={inputId} type={inputType} step={step} min={field.minimum ?? undefined} max={field.maximum ?? undefined} value={config[field.key] ?? ''} onChange={event => onChange(field.key, event.target.value)} placeholder={field.placeholder_key ? t(field.placeholder_key) : ''} className={className} />
      )}
    </div>
  );
}
