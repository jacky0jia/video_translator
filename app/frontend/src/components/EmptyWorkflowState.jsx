export default function EmptyWorkflowState({ icon, title, description, action, onAction }) {
  return <div className="app-workflow-empty">
    <div className="app-workflow-empty-icon" aria-hidden="true">{icon}</div>
    <strong>{title}</strong>
    <p>{description}</p>
    {onAction && <button type="button" className="settings-secondary" onClick={onAction}>{action}</button>}
  </div>;
}
