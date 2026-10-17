// Advisory only: never remove speech or treat a low score as proof of an error.
// Compute from the source rows so old JSON files also benefit, without rewriting them.
export function transcriptionReview(segments, duration) {
  const rows = Array.isArray(segments) ? segments : [];
  const hints = new Map();
  const eligible = row => row && !row.source_text_edited && typeof row.text === 'string' && row.text.trim();
  const key = row => eligible(row) ? row.text.toLowerCase().replace(/[^\p{L}\p{N}_]+/gu, ' ').trim() : '';
  const keys = rows.map(key);
  let first = 0;
  for (let stop = 1; stop <= rows.length; stop += 1) {
    if (stop < rows.length && keys[stop] === keys[first]) continue;
    if (keys[first] && stop - first >= 6 && Number.isFinite(rows[first].start) &&
        Number.isFinite(rows[stop - 1].end) && rows[stop - 1].end - rows[first].start >= 32) {
      for (let index = first; index < stop; index += 1) hints.set(index + 1, 'asrReviewRepeat');
    }
    first = stop;
  }
  const knownScore = row => typeof row?.confidence === 'number' && Number.isFinite(row.confidence) && row.confidence !== -1;
  const opening = rows[0];
  if (eligible(opening) && knownScore(opening) && Number.isFinite(opening.start) &&
      opening.start >= 0 && opening.start <= 0.25 && opening.confidence < -1) {
    hints.set(1, 'asrReviewOpening');
  }
  const ending = rows[rows.length - 1];
  if (eligible(ending) && knownScore(ending) && Number.isFinite(duration) && duration > 0 &&
      Number.isFinite(ending.start) && Number.isFinite(ending.end) && ending.start >= duration - 1 &&
      ending.end > ending.start && ending.end <= duration + 0.05 && ending.end - ending.start <= 1.5 &&
      ending.confidence < -0.65 && ending.text.trim().split(/\s+/u).length <= 2 &&
      Array.from(ending.text.trim()).length <= 12) {
    hints.set(rows.length, 'asrReviewEnding');
  }
  return [...hints].sort(([a], [b]) => a - b).map(([row, reason]) => ({ row, reason }));
}
