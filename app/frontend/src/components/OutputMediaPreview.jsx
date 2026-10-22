import { useRef, useState } from 'react';

export function formatPreviewTime(value) {
  const seconds = Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    : `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function OutputMediaPreview({ type, src, label, t, onError }) {
  const mediaRef = useRef(null);
  const dragging = useRef(false);
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0);
  const Media = type === 'audio' ? 'audio' : 'video';
  const readDuration = event => {
    const value = event.currentTarget.duration;
    setDuration(Number.isFinite(value) && value > 0 ? value : 0);
  };
  const readPosition = () => {
    if (!dragging.current) setPosition(mediaRef.current?.currentTime || 0);
  };
  const seek = event => {
    const media = mediaRef.current;
    if (!media || !duration) return;
    const value = Math.max(0, Math.min(duration, Number(event.target.value)));
    if (!Number.isFinite(value)) return;
    try {
      media.currentTime = value;
      setPosition(value);
    } catch { readPosition(); }
  };
  const endDrag = () => { dragging.current = false; readPosition(); };

  return <div>
    <Media ref={mediaRef} controls preload="metadata" src={src} onError={onError}
      onLoadedMetadata={readDuration} onDurationChange={readDuration}
      onTimeUpdate={readPosition} onSeeked={readPosition}
      onEmptied={() => { dragging.current = false; setDuration(0); setPosition(0); }}
      className={type === 'audio' ? 'w-full' : 'max-h-80 w-full rounded-lg bg-black'}
      aria-label={`${t('preview')} ${label}`} />
    <div className="mt-2">
      <input type="range" min="0" max={duration || 0} step="1" value={Math.min(position, duration)}
        disabled={!duration} onChange={seek}
        onPointerDown={() => { dragging.current = true; }} onPointerUp={endDrag} onPointerCancel={endDrag}
        onLostPointerCapture={endDrag} onBlur={endDrag}
        aria-label={`${t('previewPosition')} ${label}`}
        aria-valuetext={`${formatPreviewTime(position)} / ${formatPreviewTime(duration)}`}
        className="block h-6 w-full cursor-pointer accent-indigo-600 disabled:cursor-wait" />
      <div className="flex justify-between text-xs tabular-nums text-slate-500 dark:text-slate-400" aria-hidden="true">
        <span>{formatPreviewTime(position)}</span><span>{formatPreviewTime(duration)}</span>
      </div>
    </div>
  </div>;
}
