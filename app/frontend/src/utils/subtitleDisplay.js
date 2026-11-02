// Stable placement mirrored by services/subtitle_display.py. No retiming or
// priority hiding: already placed blocks keep their position until they end.
const space = /[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu;
const tickAt = seconds => Math.floor(seconds * 100 + 1e-7);

export function buildSubtitleDisplay(tracks) {
  const groups = new Map(), invalidRows = new Set();
  tracks.forEach((document, track) => document.segments.forEach((row, index) => {
    if (typeof row.text !== 'string') return;
    const text = row.text.replace(space, ' ').replace(/^ +| +$/gu, '');
    if (!text) return;
    if (!Number.isFinite(row.start) || !Number.isFinite(row.end) || row.start < 0 || row.end <= row.start || row.end > 90071992547409 || row.start > 90071992547409) { invalidRows.add(index + 1); return; }
    const start = tickAt(row.start), end = Math.max(start + 1, tickAt(row.end));
    const id = `${index + 1}:${start}:${end}`;
    if (!groups.has(id)) groups.set(id, { id, row: index + 1, start, end, parts: [] });
    groups.get(id).parts.push({ track, role: document.role, text });
  }));
  return { blocks: [...groups.values()].sort((a, b) => a.start - b.start || a.row - b.row || a.end - b.end),
    invalidRows: [...invalidRows].sort((a, b) => a - b) };
}

export function placeSubtitleBlocks(blocks, heights, gap) {
  let active = [];
  return blocks.map(block => {
    const height = heights[block.id];
    if (!(Number.isFinite(height) && height > 0 && Number.isFinite(gap) && gap >= 0)) throw Error('Invalid subtitle block dimensions');
    active = active.filter(other => other.end > block.start);
    let bottom = 0;
    for (const other of [...active].sort((a, b) => a.bottom - b.bottom)) {
      if (bottom + height + gap <= other.bottom + 1e-7) break;
      if (bottom < other.bottom + other.height + gap) bottom = other.bottom + other.height + gap;
    }
    const current = { ...block, bottom, height };
    active.push(current); return current;
  });
}

export function subtitleBlocksAt(blocks, seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return [];
  const tick = tickAt(seconds);
  return blocks.filter(block => block.start <= tick && tick < block.end);
}
