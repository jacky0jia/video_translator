export default function QwenRuntimeStatus({ status, t, className }) {
  if (!status) return null;
  const knownStates = ['idle', 'ready', 'running', 'retrying', 'succeeded', 'cancelled', 'failed'];
  const state = knownStates.includes(status.state) ? status.state : 'unknown';
  const fallbackKey = status.actual_device === 'cpu'
    ? 'qwenCpuFallbackWarning'
    : status.state === 'cancelled' ? 'qwenCpuFallbackCancelled' : 'qwenCpuFallbackPending';
  return (
    <div role={status.fallback_reason || state === 'failed' ? 'alert' : 'status'} className={className}>
      <p>{t('qwenRuntimeStatus')}: {status.actual_device?.toUpperCase() || t('qwenNotRun')} / {t(`qwenState_${state}`)}</p>
      {status.fallback_reason && <p className="text-amber-700 dark:text-amber-300">{t(fallbackKey)}: {status.fallback_reason}</p>}
    </div>
  );
}
