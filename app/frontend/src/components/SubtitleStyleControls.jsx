import { useI18n } from '../contexts/I18nContext';

export default function SubtitleStyleControls({
  style = {}, onChange, fonts = [], fontLabels = {},
  showSource, showTarget, onShowSourceChange, onShowTargetChange,
}) {
  const { t } = useI18n();
  const { offsetY = 0, fontSize = 18, sourceColor = '#FFFFFF', targetColor = '#FDE047', bold = false, fontFamily = 'Arial' } = style;
  const update = patch => onChange?.({ ...style, ...patch });
  const fontOptions = fonts.length > 0 ? fonts : Object.keys(fontLabels);

  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/70 p-2.5 dark:border-slate-700 dark:bg-slate-900/30">
      <div className="flex items-center gap-2 flex-wrap">
        <button data-style-control type="button" onClick={() => update({ offsetY: offsetY + 10 })} title={t('moveSubtitleUp')} aria-label={t('moveSubtitleUp')} className="app-style-control h-8 w-8 flex items-center justify-center text-slate-700 dark:text-slate-200 transition">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M4 19h16M12 16V5m0 0L8.5 8.5M12 5l3.5 3.5" /></svg>
        </button>
        <button data-style-control type="button" onClick={() => update({ offsetY: offsetY - 10 })} title={t('moveSubtitleDown')} aria-label={t('moveSubtitleDown')} className="app-style-control h-8 w-8 flex items-center justify-center text-slate-700 dark:text-slate-200 transition">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M4 5h16M12 8v11m0 0-3.5-3.5M12 19l3.5-3.5" /></svg>
        </button>
        <button data-style-control type="button" onClick={() => update({ fontSize: fontSize + 2 })} title={t('increaseFontSize')} className="app-style-control h-8 px-2 flex items-center justify-center text-slate-700 dark:text-slate-200 text-xs font-bold transition">A+</button>
        <button data-style-control type="button" onClick={() => update({ fontSize: Math.max(10, fontSize - 2) })} title={t('decreaseFontSize')} className="app-style-control h-8 px-2 flex items-center justify-center text-slate-700 dark:text-slate-200 text-xs font-bold transition">A-</button>
        <label data-style-control className="app-style-control h-8 w-8 flex items-center justify-center cursor-pointer overflow-hidden" title={t('sourceColor')}>
          <input type="color" value={sourceColor} onChange={event => update({ sourceColor: event.target.value })} className="h-10 w-10 p-0 border-0 cursor-pointer bg-transparent scale-125" />
        </label>
        <label data-style-control className="app-style-control h-8 w-8 flex items-center justify-center cursor-pointer overflow-hidden" title={t('targetColor')}>
          <input type="color" value={targetColor} onChange={event => update({ targetColor: event.target.value })} className="h-10 w-10 p-0 border-0 cursor-pointer bg-transparent scale-125" />
        </label>
        <button data-style-control type="button" onClick={() => update({ bold: !bold })} title={t('bold') || 'Bold'} className={`app-style-control h-8 w-8 flex items-center justify-center text-xs font-bold transition ${bold ? 'app-style-control-active' : 'text-slate-700 dark:text-slate-200'}`}>B</button>
        <label data-style-control className="app-style-control flex h-8 items-center gap-2 px-2 text-xs text-slate-600 dark:text-slate-300">
          <span>{t('sourceAndTargetFont')}</span>
          <select value={fontFamily} onChange={event => update({ fontFamily: event.target.value })} aria-label={t('sourceAndTargetFont')} title={t('sourceAndTargetFont')} className="app-font-select min-w-24 bg-transparent text-xs text-slate-700 dark:text-slate-200 focus:outline-none">
            {fontOptions.map(name => <option key={name} value={name}>{fontLabels[name] || name}</option>)}
          </select>
        </label>
        <label className="flex cursor-pointer select-none items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300"><input type="checkbox" checked={showSource} onChange={event => onShowSourceChange?.(event.target.checked)} className="h-4 w-4 rounded border-gray-300 bg-gray-100 text-blue-600 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-700" />{t('showSource')}</label>
        <label className="flex cursor-pointer select-none items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300"><input type="checkbox" checked={showTarget} onChange={event => onShowTargetChange?.(event.target.checked)} className="h-4 w-4 rounded border-gray-300 bg-gray-100 text-blue-600 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-700" />{t('showTarget')}</label>
      </div>
      <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{t('subtitleDisplayHint')}</p>
    </div>
  );
}
