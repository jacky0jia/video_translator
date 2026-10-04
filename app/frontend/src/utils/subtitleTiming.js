export function parseSubtitleTime(value) {
  const parts = value.trim().split(':');
  if (parts.length < 1 || parts.length > 3 || parts.some(part => !/^\d+(?:\.\d+)?$/.test(part))) return NaN;
  return parts.reduce((seconds, part) => seconds * 60 + Number(part), 0);
}

export function findSubtitleOverlaps(segments) {
  const pairs = [];
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      if (Number(segments[i].start) < Number(segments[j].end) - 0.001 &&
          Number(segments[j].start) < Number(segments[i].end) - 0.001) {
        pairs.push([i + 1, j + 1]);
      }
    }
  }
  return pairs;
}
