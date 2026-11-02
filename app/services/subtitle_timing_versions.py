"""Copy-on-write subtitle timing versions, published through one history CAS.

Files already referenced by a task are never overwritten. A failed staging or
publication may leave unreferenced UUID files, but cannot publish half a track
set. This is single-process workflow exclusion, not external-editor locking.
"""
import copy
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from app.core.file_security import contained_file
from app.core.schemas import TranscriptionResult
from app.services.subtitle_timing import plan_subtitle_timing
from app.services.translation_review_service import translation_busy, translation_fingerprint


class TimingVersionConflict(RuntimeError):
    pass


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def timing_summary(plan, *, status='applied'):
    repaired = {change.index + 1 for change in plan.changes}
    remaining = [pair for pair in plan.overlaps if pair[0] not in repaired]
    return {'status': status, 'changed_rows': sorted(repaired),
            'remaining_overlaps': len(remaining),
            'remaining_rows': sorted({row for pair in remaining for row in pair})}


def record_transcription_timing(task, original_path, output_path, plan):
    """Prepare publication metadata; the caller still owns the ASR run/write."""
    records = copy.deepcopy(task.get('subtitle_timing_versions') or [])
    for record in records:
        if record['id'] == task.get('subtitle_timing_active'):
            record['status'] = 'superseded'
    updates = {'subtitle_timing_active': None, 'subtitle_timing_versions': records,
               'subtitle_timing_summary': timing_summary(plan)}
    if plan.changes:
        operation = uuid4().hex
        records.append({'id': operation, 'status': 'applied', 'automatic': True,
            'before': {'source': str(original_path)}, 'after': {'source': str(output_path)},
            'before_hashes': {'source': _hash(Path(original_path).read_bytes())},
            'after_hashes': {'source': _hash(Path(output_path).read_bytes())},
            'changes': [asdict(change) for change in plan.changes]})
        updates.update(subtitle_timing_active=operation,
                       subtitle_timing_revision=task.get('subtitle_timing_revision', 0) + 1)
    return updates


def write_subtitle_snapshot(root: Path, data: bytes) -> str:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'subtitle-version-{uuid4().hex}.json'
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return str(path.resolve())


def subtitle_version_paths(task):
    """Only app-created snapshot references, for existing task deletion paths."""
    values = [task.get('transcription_original_path')]
    values.extend(task.get('transcription_original_versions') or [])
    for record in task.get('subtitle_timing_versions') or []:
        for key in ('before', 'after', 'restored'):
            values.extend((record.get(key) or {}).values())
    return {value for value in values if isinstance(value, str)
            and Path(value).name.startswith('subtitle-version-') and Path(value).suffix == '.json'}


class SubtitleTimingVersions:
    def __init__(self, history, output_dir, busy=lambda _task: False):
        self.history, self.root, self.busy = history, Path(output_dir), busy

    def _task(self, task_id):
        task = self.history.get_task(task_id)
        if task is None:
            raise FileNotFoundError('Task not found')
        if (self.busy(task_id) or translation_busy(task_id) or task.get('pipeline_cleanup_pending')
                or task.get('pipeline_status') in {'processing', 'cancelling', 'awaiting_compression'}
                or task.get('status') in {'pending', 'uploading', 'downloading', 'extracting_audio', 'transcribing',
                    'translating', 'waiting_for_gpu', 'switching_model', 'pipeline_starting', 'pipeline_transcribing',
                    'pipeline_translating', 'pipeline_dubbing', 'burning', 'dubbing'}
                or task.get('dubbing_status') == 'processing' or task.get('burn_status') == 'processing'):
            raise TimingVersionConflict('Task is still processing or stopping')
        return task

    def _read(self, value):
        path = contained_file(self.root, value or '')
        if path is None or path.suffix.lower() != '.json':
            raise ValueError('Subtitle file is unavailable or outside the output directory')
        data = path.read_bytes()
        document = json.loads(data.decode('utf-8-sig'))
        # Preserve unknown metadata by editing the original dict, not a model
        # serialization that silently drops legacy/vendor fields.
        model = TranscriptionResult.model_validate(document)
        json.dumps(document, allow_nan=False)
        return str(path), data, document, model

    @staticmethod
    def _bindings(task):
        bindings = {'source': task.get('transcription_path')}
        selected = task.get('translation_path') or (task.get('translations') or {}).get(task.get('target_lang'))
        if selected:
            bindings['selected'] = selected
        for language, path in sorted((task.get('translations') or {}).items()):
            if not isinstance(language, str) or not path:
                raise ValueError('Invalid translation binding')
            bindings[f'language:{language}'] = path
        return bindings

    def _load(self, task):
        bindings = self._bindings(task)
        loaded = {role: self._read(value) for role, value in bindings.items()}
        if any(value[0] == loaded['source'][0] for role, value in loaded.items() if role != 'source'):
            raise ValueError('Source and translation cannot share the same file')
        models = [value[3] for value in loaded.values()]
        duration = task.get('transcription_duration')
        if duration is not None:
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                raise ValueError('Invalid media duration')
            if any(row.start < 0 or row.end > duration + .001 for model in models for row in model.segments):
                raise ValueError('Subtitle timestamps exceed media duration')
        digest = _hash(json.dumps({'task_id': task['task_id'], 'tracks': {
            role: [value[0], _hash(value[1])] for role, value in loaded.items()
        }}, sort_keys=True).encode())
        plan = plan_subtitle_timing(models[0], models[1:])
        return loaded, digest, plan

    def analyze(self, task_id):
        task = self._task(task_id)
        _, digest, plan = self._load(task)
        return {'state_revision': task.get('state_revision', 0), 'fingerprint': digest,
                'changes': [dict(row=change.index + 1, start=change.start, end=change.end,
                                 new_end=change.new_end) for change in plan.changes],
                'overlaps': plan.overlaps, 'issues': [asdict(issue) for issue in plan.issues],
                'active_version': task.get('subtitle_timing_active')}

    def include_generated_translation(self, task, language, output_path, data):
        """Extend an untouched automatic undo point before publishing a translation.

        Generated text is retained when reverting timing. Never rebase a point
        over any human/file edits, or a changed set of previously bound tracks.
        The translation caller owns task exclusion; it publishes these records
        together with the new translation binding.
        """
        records = copy.deepcopy(task.get('subtitle_timing_versions') or [])
        record = next((r for r in records if r['id'] == task.get('subtitle_timing_active')), None)
        if not record or not record.get('automatic') or record['status'] != 'applied':
            return {}
        try:
            loaded, _digest, _plan = self._load(task)
        except (OSError, ValueError):
            # An unavailable old track must not prevent regenerating it.
            # Keep the old checkpoint untouched; undo still verifies all bytes.
            return {}
        if set(loaded) != set(record['after']) or any(
                value[0] != record['after'][role] or _hash(value[1]) != record['after_hashes'][role]
                for role, value in loaded.items()):
            return {}
        document = json.loads(data)
        source = loaded['source'][2]['segments']
        rows = document['segments']
        if len(rows) != len(source) or any((row['start'], row['end']) != (src['start'], src['end'])
                                          for row, src in zip(rows, source)):
            return {}
        restored = copy.deepcopy(document)
        for change in record['changes']:
            restored['segments'][change['index']]['end'] = change['end']
        encoded = json.dumps(restored, ensure_ascii=False, indent=4, allow_nan=False).encode('utf-8')
        before_path = write_subtitle_snapshot(self.root, encoded)
        for role in ('selected', f'language:{language}'):
            record['before'][role] = before_path
            record['after'][role] = str(output_path)
            record['before_hashes'][role] = _hash(encoded)
            record['after_hashes'][role] = _hash(data)
        return {'subtitle_timing_versions': records}

    @staticmethod
    def _point(task, paths):
        task = copy.deepcopy(task)
        task['transcription_path'] = paths['source']
        if 'selected' in paths:
            task['translation_path'] = paths['selected']
        task['translations'] = {key[9:]: value for key, value in paths.items() if key.startswith('language:')}
        return task

    def _invalidate(self, task, paths):
        projected = self._point(task, paths)
        reviews = {}
        languages = set(projected['translations'])
        if projected.get('target_lang') and projected.get('translation_path'):
            languages.add(projected['target_lang'])
        for language in languages:
            reviews[language] = {'status': 'pending', 'review_id': uuid4().hex,
                                'fingerprint': translation_fingerprint(projected, language)}
        target = task.get('target_lang')
        waiting = target in reviews
        updates = {key: projected[key] for key in ('transcription_path', 'translations')}
        if 'selected' in paths:
            updates['translation_path'] = projected['translation_path']
        updates.update(translation_reviews=reviews, subtitle_outputs={},
            status='awaiting_review' if waiting else 'transcribed',
            pipeline_status='awaiting_review' if waiting else None,
            pipeline_stage=None, gpu_stage=None, progress_percent=0, failed_stage=None, cancelled_stage=None,
            message='字幕时间轴已更新，请检查字幕后继续。',
            outputs_stale=bool(any(task.get(key) for key in
                ('dubbing_audio_path', 'dubbing_video_path', 'dubbing_raw_video_path', 'burn_path'))),
            dubbing_status='stale', burn_status='stale')
        if waiting:
            updates['translation_review_language'] = target
        if task.get('dubbing_review'):
            updates['dubbing_review'] = {**task['dubbing_review'], 'needs_recheck': True}
        return updates

    def _publish(self, task, expected_digest, updates):
        latest = self._task(task['task_id'])
        if latest.get('state_revision', 0) != task.get('state_revision', 0) or self._load(latest)[1] != expected_digest:
            raise TimingVersionConflict('Subtitles changed; analyze again')
        state = self.history.compare_and_update_task(task['task_id'], task.get('state_revision', 0), updates)
        if not state:
            raise TimingVersionConflict('Task changed before timing publication')
        return state

    def apply(self, task_id, revision, fingerprint):
        task = self._task(task_id)
        loaded, digest, plan = self._load(task)
        if task.get('state_revision', 0) != revision or digest != fingerprint:
            raise TimingVersionConflict('Timing analysis is no longer current')
        if not plan.changes:
            return task
        before, after, original_hashes, new_hashes, staged = {}, {}, {}, {}, {}
        for role, (path, data, document, _model) in loaded.items():
            if path not in staged:
                updated = copy.deepcopy(document)
                for change in plan.changes:
                    updated['segments'][change.index]['end'] = change.new_end
                encoded = json.dumps(updated, ensure_ascii=False, indent=4, allow_nan=False).encode('utf-8')
                staged[path] = (write_subtitle_snapshot(self.root, data), write_subtitle_snapshot(self.root, encoded), _hash(encoded))
            before[role], after[role], new_hashes[role] = staged[path]
            original_hashes[role] = _hash(data)
        operation = uuid4().hex
        record = {'id': operation, 'status': 'applied', 'before': before, 'after': after,
                  'before_hashes': original_hashes, 'after_hashes': new_hashes,
                  'changes': [asdict(change) for change in plan.changes]}
        records = copy.deepcopy(task.get('subtitle_timing_versions') or [])
        for previous in records:
            if previous['id'] == task.get('subtitle_timing_active'):
                previous['status'] = 'superseded'
        updates = self._invalidate(task, after)
        updates.update(subtitle_timing_active=operation,
            subtitle_timing_revision=task.get('subtitle_timing_revision', 0) + 1,
            subtitle_timing_versions=[*records, record], subtitle_timing_summary=timing_summary(plan))
        return self._publish(task, digest, updates)

    def undo(self, task_id, revision, operation):
        task = self._task(task_id)
        loaded, digest, _plan = self._load(task)
        if task.get('state_revision', 0) != revision or not operation or task.get('subtitle_timing_active') != operation:
            raise TimingVersionConflict('Timing version is no longer current')
        records = copy.deepcopy(task.get('subtitle_timing_versions') or [])
        record = next((item for item in records if item['id'] == operation), None)
        if not record or record['status'] != 'applied':
            raise TimingVersionConflict('Timing version is unavailable')
        if set(loaded) != set(record['after']) or any(value[0] != record['after'][role]
                or _hash(value[1]) != record['after_hashes'][role] for role, value in loaded.items()):
            raise TimingVersionConflict('Subtitles were edited after timing repair; undo would overwrite edits')
        restored, staged = {}, {}
        for role, path in record['before'].items():
            safe, data, _document, _model = self._read(path)
            if _hash(data) != record['before_hashes'][role]:
                raise TimingVersionConflict('Timing backup changed; original data was not overwritten')
            if safe not in staged:
                staged[safe] = write_subtitle_snapshot(self.root, data)
            restored[role] = staged[safe]
        record.update(status='undone', restored=restored)
        updates = self._invalidate(task, restored)
        updates.update(subtitle_timing_active=None,
            subtitle_timing_revision=task.get('subtitle_timing_revision', 0) + 1, subtitle_timing_versions=records,
            subtitle_timing_summary={**(task.get('subtitle_timing_summary') or {}), 'status': 'undone'})
        return self._publish(task, digest, updates)
