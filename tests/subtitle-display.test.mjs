import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSubtitleDisplay, placeSubtitleBlocks, subtitleBlocksAt } from '../app/frontend/src/utils/subtitleDisplay.js';

const row = (start, end, text = 'text') => ({ start, end, text });
const build = rows => buildSubtitleDisplay([{ role: 'source', segments: rows }]);
test('new blocks avoid earlier ones and never fall down; freed space is reused', () => {
  const { blocks } = build([row(0, 3), row(1, 7), row(4, 6), row(7, 8)]);
  const placed = placeSubtitleBlocks(blocks, Object.fromEntries(blocks.map(b => [b.id, 20])), 2);
  assert.deepEqual(placed.map(b => b.bottom), [0, 22, 0, 0]);
  assert.equal(subtitleBlocksAt(placed, 3.5)[0].bottom, 22);
  assert.deepEqual(subtitleBlocksAt(placed, 7).map(b => b.row), [4]);
  assert.deepEqual(subtitleBlocksAt(placed, NaN), []);
});
test('bilingual aligned rows share one block; equal timestamps do not hide rows', () => {
  const result = buildSubtitleDisplay([
    { role: 'source', segments: [row(0, 2, 'Source'), row(0, 2, 'Other')] },
    { role: 'target', segments: [row(0, 2, '译文'), row(0, 3, '异时')] },
  ]);
  assert.equal(result.blocks.length, 3);
  assert.deepEqual(result.blocks[0].parts.map(p => p.text), ['Source', '译文']);
  const placed = placeSubtitleBlocks(result.blocks, Object.fromEntries(result.blocks.map(b => [b.id, 40])), 2);
  assert.deepEqual(placed.map(b => b.bottom), [0, 42, 84]);
});
test('validation, ASS clock and text normalization match display contract', () => {
  const result = build([row(1, 1.001, ' a\n b '), row(2, 1), row(null, 3), row(0, 1, '\u0085 '), row(3, 4, 'literal {\\pos(1,1)}')]);
  assert.deepEqual(result.invalidRows, [2, 3]);
  assert.equal(result.blocks[0].text, undefined);
  assert.equal(result.blocks[0].parts[0].text, 'a b');
  assert.equal(result.blocks[0].end, 101);
  assert.equal(subtitleBlocksAt(result.blocks, 1.01).length, 0);
  assert.throws(() => placeSubtitleBlocks(result.blocks, {}, 2), /dimensions/);
});
