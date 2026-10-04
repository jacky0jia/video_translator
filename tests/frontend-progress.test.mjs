import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { localizedTaskMessage, subtitleTranslationProgress } from '../app/frontend/src/hooks/taskProcessingState.js';
import { translations } from '../app/frontend/src/i18n/translations.js';

const en = key => translations.en[key] || key;

test('subtitle translation progress is bounded and monotonic', () => {
  assert.equal(subtitleTranslationProgress(5), 40);
  assert.equal(subtitleTranslationProgress(50), 55);
  assert.equal(subtitleTranslationProgress(95), 70);
  assert.equal(subtitleTranslationProgress(10, 60), 60);
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
