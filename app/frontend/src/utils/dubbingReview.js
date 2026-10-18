import { subtitleReviewRows } from './subtitleReview';

export function dubbingReviewView(review, language, rowCount) {
  if (!review || review.status !== 'required' || review.target_lang !== language) return { active: false, rows: [], issues: [] };
  const rows = subtitleReviewRows(review.rows, rowCount);
  const issues = (Array.isArray(review.issues) ? review.issues : []).flatMap(issue => {
    if (!issue || typeof issue !== 'object') return [];
    const issueRows = subtitleReviewRows((Array.isArray(issue.segment_indices) ? issue.segment_indices : []).map(index =>
      Number.isInteger(index) ? index + 1 : NaN), rowCount);
    if (!issueRows.length) return [];
    const measured = ['raw_seconds', 'fit_seconds', 'available_seconds', 'excess_seconds'].every(key =>
      typeof issue[key] === 'number' && Number.isFinite(issue[key]) && issue[key] >= 0);
    return [{ ...issue, rows: issueRows, measured }];
  });
  return { active: true, rows, issues };
}

export function dubbingIssueMessage(issue, template) {
  return ['raw_seconds', 'fit_seconds', 'available_seconds', 'excess_seconds'].reduce((text, key) =>
    text.replaceAll(`{${key}}`, (Math.ceil(issue[key] * 100) / 100).toFixed(2)), template);
}
