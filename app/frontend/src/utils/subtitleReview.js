// Review metadata can outlive a re-transcription; never link to absent rows.
export function subtitleReviewRows(rows, rowCount) {
  return [...new Set((Array.isArray(rows) ? rows : []).filter(row =>
    Number.isInteger(row) && row >= 1 && row <= rowCount
  ))].sort((a, b) => a - b);
}
