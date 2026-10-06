import assert from 'node:assert/strict';
import test from 'node:test';
import { translations } from '../app/frontend/src/i18n/translations.js';

test('Qwen distinguishes fixed synthesis speed from uniform track acceleration', () => {
  assert.equal(translations.en.qwenFixedSpeed,
    'Qwen generates speech at 1.00x speed. For subtitle synchronization, the entire audio track can be sped up to a maximum of 1.60x.');
  assert.match(translations.zh.qwenFixedSpeed, /1\.00x.*统一加速至 1\.60x/);
});
