export function fieldIsVisible(field, values) {
  return (field.visible_when || []).every(rule => {
    const current = values[rule.key];
    if (rule.operator === 'in') return (rule.value || []).includes(current);
    if (rule.operator === 'truthy') return Boolean(current);
    if (rule.operator === 'empty') return current === undefined || current === null || current === '';
    return current === rule.value;
  });
}
