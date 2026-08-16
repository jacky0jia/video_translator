export default function ProgressBar({ percent, message, status }) {
  const isIndeterminate = percent == null || percent === 0 || percent === undefined;
  const isError = status === 'failed' || status === 'translation_failed' || status === 'cancelled';
  const isSuccess = status === 'transcribed' || status === 'completed';

  const barColor = isError
    ? 'bg-red-500'
    : isSuccess
    ? 'bg-green-500'
    : 'bg-blue-500';

  return (
    <div className="w-full">
      {message && (
        <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400 mb-1 pt-1">
          <span className="truncate">{message}</span>
          {!isIndeterminate && <span>{percent}%</span>}
        </div>
      )}
      <div className="w-full h-2 bg-gray-200 dark:bg-slate-700 rounded-full overflow-hidden">
        {isIndeterminate ? (
          <div className="h-full bg-blue-500 animate-pulse w-1/3" />
        ) : (
          <div
            className={`h-full ${barColor} transition-all duration-300 ease-out`}
            style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
          />
        )}
      </div>
    </div>
  );
}
