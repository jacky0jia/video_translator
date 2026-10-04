import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { localizedTaskMessage, stageProgressForEvent } from '../app/frontend/src/hooks/taskProcessingState.js';
import { translations } from '../app/frontend/src/i18n/translations.js';

const en = key => translations.en[key] || key;

test('current-stage progress follows dubbing and rendering events', () => {
  assert.equal(stageProgressForEvent({ status: 'pipeline_dubbing', progress_percent: 75 }), 0);
  assert.equal(stageProgressForEvent({ status: 'dubbing', message: '正在合成语音 38/112...', progress: 27 }), 27);
  assert.equal(stageProgressForEvent({ status: 'dubbing', message: '正在合成语音 70/112...', progress: 45 }, 27), 45);
  assert.equal(stageProgressForEvent({ status: 'dubbing', progress_percent: 75 }, 45), 45);
  assert.equal(stageProgressForEvent({ status: 'pipeline_rendering', progress_percent: 90 }, 100), 0);
  assert.equal(stageProgressForEvent({ status: 'burning', progress: 68 }), 68);
  assert.equal(stageProgressForEvent({ status: 'translating', progress_percent: 51 }), 51);
  assert.equal(stageProgressForEvent({ status: 'completed', progress_percent: 100 }), 100);
});

test('live speech and translation progress localize in English', () => {
  assert.equal(localizedTaskMessage({ status: 'dubbing', message: '正在合成语音 3/16' }, en), 'Synthesizing speech 3/16...');
  assert.equal(localizedTaskMessage({ status: 'dubbing', message: '正在对齐时间轴' }, en), 'Aligning the audio timeline...');
  assert.equal(localizedTaskMessage({ status: 'translating', message: '3/16' }, en), 'Translating batch 3/16...');
  assert.equal(localizedTaskMessage({ status: 'pipeline_rendering', pipeline_stage: 'pipeline_rendering' }, en), 'Rendering the final video...');
});

test('color controls use borderless inner swatches', () => {
  const controls = readFileSync(new URL('../app/frontend/src/components/SubtitleStyleControls.jsx', import.meta.url), 'utf8');
  const styles = readFileSync(new URL('../app/frontend/src/index.css', import.meta.url), 'utf8');
  assert.equal((controls.match(/className="app-color-swatch cursor-pointer"/g) || []).length, 2);
  assert.match(styles, /\.app-color-swatch::-webkit-color-swatch\s*\{[^}]*border:\s*0/);
});

test('four-stage overview appears before current-stage progress', () => {
  const panel = readFileSync(new URL('../app/frontend/src/components/ProcessPanel.jsx', import.meta.url), 'utf8');
  const status = panel.slice(panel.indexOf('function ProcessingStatus'), panel.indexOf('export default function ProcessPanel'));
  assert.ok(status.indexOf('PROCESS_STAGES.map') < status.indexOf('role="progressbar"'));
  assert.match(status, /className="grid grid-cols-4 gap-1\.5 rounded-xl border border-slate-200[^\"]*"[^>]*>\{PROCESS_STAGES\.map/);
  assert.match(status, /<div className="px-1">\s*<div className="mb-2 flex items-start/);
});
