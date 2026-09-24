import { useState } from 'react';
import { useI18n } from '../contexts/I18nContext';

export default function SubtitleStyleControls({ style = {}, onChange, fonts = [], fontLabels = {} }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const { offsetY = 0, fontSize = 18, sourceColor = '#FFFFFF', targetColor = '#FDE047', bold = false, fontFamily = 'Arial' } = style;
  const update = patch => onChange?.({ ...style, ...patch });
  const fontOptions = fonts.length > 0 ? fonts : Object.keys(fontLabels);

  return (
    <>
      <div className="mt-3 flex justify-end">
        <button type="button" onClick={() => setOpen(value => !value)} className="app-secondary-button" aria-expanded={open}>
          {t('subtitleStyle')}
        </button>
      </div>
      {open && (
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <button type="button" onClick={() => update({ offsetY: offsetY + 10 })} title={t('moveSubtitleUp')} className="h-8 w-8 flex items-center justify-center rounded bg-gray-100 dark:bg-slate-700 hover:bg-gray-200 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 transition">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>
          </button>
          <button type="button" onClick={() => update({ offsetY: offsetY - 10 })} title={t('moveSubtitleDown')} className="h-8 w-8 flex items-center justify-center rounded bg-gray-100 dark:bg-slate-700 hover:bg-gray-200 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 transition">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
          </button>
          <button type="button" onClick={() => update({ fontSize: fontSize + 2 })} title={t('increaseFontSize')} className="h-8 px-2 flex items-center justify-center rounded bg-gray-100 dark:bg-slate-700 hover:bg-gray-200 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-xs font-bold transition">A+</button>
          <button type="button" onClick={() => update({ fontSize: Math.max(10, fontSize - 2) })} title={t('decreaseFontSize')} className="h-8 px-2 flex items-center justify-center rounded bg-gray-100 dark:bg-slate-700 hover:bg-gray-200 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-xs font-bold transition">A-</button>
          <label className="h-8 w-8 flex items-center justify-center cursor-pointer rounded bg-gray-100 dark:bg-slate-700 overflow-hidden" title={t('sourceColor')}>
            <input type="color" value={sourceColor} onChange={event => update({ sourceColor: event.target.value })} className="h-10 w-10 p-0 border-0 cursor-pointer bg-transparent scale-125" />
          </label>
          <label className="h-8 w-8 flex items-center justify-center cursor-pointer rounded bg-gray-100 dark:bg-slate-700 overflow-hidden" title={t('targetColor')}>
            <input type="color" value={targetColor} onChange={event => update({ targetColor: event.target.value })} className="h-10 w-10 p-0 border-0 cursor-pointer bg-transparent scale-125" />
          </label>
          <button type="button" onClick={() => update({ bold: !bold })} title={t('bold') || 'Bold'} className={`h-8 w-8 flex items-center justify-center rounded text-xs font-bold transition ${bold ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200'}`}>B</button>
          <label className="flex h-8 items-center gap-2 rounded bg-gray-100 px-2 text-xs text-slate-600 dark:bg-slate-700 dark:text-slate-300">
            <span>{t('sourceAndTargetFont')}</span>
            <select value={fontFamily} onChange={event => update({ fontFamily: event.target.value })} aria-label={t('sourceAndTargetFont')} title={t('sourceAndTargetFont')} className="app-font-select min-w-28 bg-transparent text-xs text-slate-700 dark:text-slate-200 focus:outline-none">
              {fontOptions.map(name => <option key={name} value={name}>{fontLabels[name] || name}</option>)}
            </select>
          </label>
        </div>
      )}
    </>
  );
}
