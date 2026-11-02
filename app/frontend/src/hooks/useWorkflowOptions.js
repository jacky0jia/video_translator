import { useCallback, useLayoutEffect, useMemo, useState } from 'react';

export const DEFAULT_SUBTITLE_STYLE = { offsetY: 0, fontSize: 18, sourceColor: '#FFFFFF', targetColor: '#FDE047', bold: false, fontFamily: 'Arial' };
const DEFAULT_OUTPUT = { policy: 'balanced', codec: 'hevc', resolution: '1440', bitrate_mbps: 10 };

export function restoreWorkflowOptions(task) {
  const saved = task?.workflow_options || {};
  const route = saved.route || task?.last_process_route || 'subtitles';
  return {
    route,
    subtitle_format: saved.subtitle_format || task?.last_subtitle_format || 'srt',
    burn_subtitles: saved.burn_subtitles ?? (route === 'dubbing' ? task?.dubbing_burn_subtitles : task?.last_burn_enabled) ?? false,
    end_note_enabled: saved.end_note_enabled ?? task?.last_end_note_enabled ?? true,
    subtitle_content: saved.subtitle_content || 'translated',
    voice: saved.voice ?? task?.dubbing_voice ?? '',
    speed: saved.speed ?? task?.dubbing_speed ?? 1,
    output_options: { ...DEFAULT_OUTPUT, ...task?.video_output_options, ...saved.output_options },
    subtitle_style: { ...DEFAULT_SUBTITLE_STYLE, ...saved.subtitle_style },
    show_source: saved.show_source ?? Boolean(task?.transcription_path),
    show_target: saved.show_target ?? true,
  };
}

// A task's saved request is the refresh fallback, not a reason to overwrite
// current form edits. Derive the new task synchronously so no frame uses A's
// options with B's identity. Late callbacks retain the old inactive scope.
export function useWorkflowOptions(scope, task, stale = false) {
  const restored = restoreWorkflowOptions(task);
  const fingerprint = JSON.stringify(restored);
  const [draft, setDraft] = useState(() => ({ scope, fingerprint, options: restored, dirty: false }));
  const effective = draft.scope !== scope || (!stale && !draft.dirty && draft.fingerprint !== fingerprint)
    ? { scope, fingerprint, options: restored, dirty: false } : draft;
  useLayoutEffect(() => { if (draft !== effective) setDraft(effective); }, [effective]);
  const setOption = useCallback((key, next, userEdit = true) => {
    if (!scope.active) return;
    setDraft(previous => {
      if (previous.scope !== scope) return previous;
      const value = typeof next === 'function' ? next(previous.options[key]) : next;
      if (Object.is(value, previous.options[key])) return previous;
      return { ...previous, dirty: previous.dirty || userEdit, options: { ...previous.options, [key]: value } };
    });
  }, [scope]);
  const setters = useMemo(() => Object.fromEntries(Object.keys(restored).map(key => [key, next => setOption(key, next)])), [setOption]);
  return { options: effective.options, setters, setOption };
}
