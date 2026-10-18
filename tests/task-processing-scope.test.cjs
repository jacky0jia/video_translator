const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('../app/frontend/node_modules/esbuild');

// Run the actual hook, including dependency-aware effect cleanup. All network,
// timers and settings are local fakes; no backend or model is invoked.
function harness(initialTask, { automaticVoices = true } = {}) {
  const cells = [], requests = [], toasts = [], refreshes = [], timers = new Map();
  const listeners = new Map();
  let index = 0, dirty = false, view, selected = initialTask, stream;
  let layout = [], effects = [], timerId = 0, stateWrites = 0, refreshHold;
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, i) => !Object.is(value, b[i]));
  function effect(queue, fn, deps) {
    const slot = index++, previous = cells[slot];
    if (changed(previous?.deps, deps)) {
      cells[slot] = { deps, cleanup: previous?.cleanup, fn };
      queue.push(() => { cells[slot].cleanup?.(); cells[slot].cleanup = fn(); });
    }
  }
  const react = {
    useState(initial) {
      const slot = index++;
      if (!(slot in cells)) cells[slot] = { value: typeof initial === 'function' ? initial() : initial };
      return [cells[slot].value, next => {
        stateWrites++;
        const value = typeof next === 'function' ? next(cells[slot].value) : next;
        if (!Object.is(value, cells[slot].value)) { cells[slot].value = value; dirty = true; }
      }];
    },
    useRef(initial) { const slot = index++; return cells[slot] ||= { current: initial }; },
    useMemo(fn, deps) {
      const slot = index++;
      if (changed(cells[slot]?.deps, deps)) cells[slot] = { value: fn(), deps };
      return cells[slot].value;
    },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) { effect(effects, fn, deps); },
    useLayoutEffect(fn, deps) { effect(layout, fn, deps); },
  };
  const root = path.resolve(__dirname, '../app/frontend/src');
  function compile(name, requireModule) {
    const mod = { exports: {} };
    vm.runInNewContext(esbuild.transformSync(fs.readFileSync(path.join(root, name), 'utf8'), { format: 'cjs' }).code, {
      module: mod, exports: mod.exports, require: requireModule,
      URLSearchParams, AbortController, console,
      window: { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) },
      setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
      clearTimeout: id => timers.delete(id),
      fetch: (url, options) => {
        const response = (data, ok = true, status = ok ? 200 : 500) => ({ ok, status, json: async () => data });
        if (url === '/api/dubbing/voices' && automaticVoices) {
          return Promise.resolve(response({ voices: [{ id: 'voice', language: 'zh' }], tts_mode: 'qwen' }));
        }
        return new Promise(resolve => requests.push({ url, options,
          respond: (data, ok, status) => resolve(response(data, ok, status)),
        }));
      },
    });
    return mod.exports;
  }
  const scope = compile('hooks/useTaskScope.js', () => react);
  const state = compile('hooks/taskProcessingState.js');
  let t = key => key;
  const notify = (...args) => toasts.push(args);
  const onTaskRefresh = id => {
    refreshes.push(id);
    if (refreshHold) { const held = refreshHold; refreshHold = null; return held.promise; }
    return Promise.resolve();
  };
  const hook = compile('hooks/useTaskProcessing.js', name => {
    if (name === 'react') return react;
    if (name.endsWith('useTaskScope')) return scope;
    if (name.endsWith('taskProcessingState')) return state;
    if (name.endsWith('useTaskSSE')) return { useTaskSSE: (id, callback) => { stream = { id, callback }; } };
    if (name.endsWith('ToastContext')) return { useToast: () => notify };
    if (name.endsWith('I18nContext')) return { useI18n: () => ({ t }) };
    if (name.endsWith('VideoOutputOptions')) return { DEFAULT_VIDEO_OUTPUT: {} };
    throw new Error(`Unexpected import: ${name}`);
  }).useTaskProcessing;
  function render(task = selected) {
    selected = task;
    for (let turn = 0; turn < 30; turn++) {
      index = 0; dirty = false; layout = []; effects = [];
      view = hook({ task: selected, onTaskRefresh, showSource: true, showTarget: true });
      layout.forEach(fn => fn()); effects.forEach(fn => fn());
      if (!dirty) return;
    }
    throw new Error('Hook did not settle');
  }
  async function settle() {
    for (let i = 0; i < 20; i++) { await Promise.resolve(); if (dirty) render(); }
  }
  function tick(delay) {
    const timer = [...timers].find(([, item]) => item.delay === delay);
    assert.ok(timer, `Missing ${delay}ms timer`);
    timers.delete(timer[0]); timer[1].fn();
  }
  function holdRefresh() {
    let release;
    refreshHold = { promise: new Promise(resolve => { release = resolve; }) };
    return release;
  }
  render();
  return {
    render, settle, requests, refreshes, toasts, tick, holdRefresh,
    get view() { return view; }, get stateWrites() { return stateWrites; },
    changeLanguage() { t = key => `localized:${key}`; render(); },
    event: data => stream.callback(data),
    unmount: () => cells.forEach(cell => cell?.cleanup?.()),
    replayEffects() { cells.forEach(cell => { if (cell?.fn) { cell.cleanup?.(); cell.cleanup = cell.fn(); } }); },
  };
}
const task = (id, extra = {}) => ({
  task_id: id, status: 'transcribed', transcription_path: `${id}.json`,
  translations: { Chinese: `${id}-zh.json` }, target_lang: 'Chinese', last_process_route: 'dubbing', ...extra,
});
const activeB = () => task('B', { status: 'dubbing', pipeline_status: 'processing', pipeline_stage: 'pipeline_dubbing', progress_percent: 42 });
const snapshot = h => JSON.stringify(Object.fromEntries([
  'running', 'startPending', 'progress', 'currentStage', 'message', 'status', 'videoPlan', 'videoPlanLoading', 'videoPlanError',
].map(key => [key, h.view[key]])));
async function ready(taskData = task('A'), options) { const h = harness(taskData, options); await h.settle(); return h; }
async function reachPipeline(h) {
  const pending = h.view.run();
  h.requests.at(-1).respond({ encoder: 'test' }); await h.settle();
  assert.equal(h.requests.at(-1).url, '/api/pipeline');
  return { pending, request: h.requests.at(-1) };
}

test('late preflight failure cannot change B state, notification or startup lock', async () => {
  const h = await ready();
  const a = h.view.run(), preflightA = h.requests.at(-1); await h.settle();
  h.render(task('B')); await h.settle();
  assert.equal(h.view.startPending, false);
  const b = h.view.run(), preflightB = h.requests.at(-1); await h.settle();
  const before = snapshot(h);
  preflightA.respond({ detail: 'A invalid' }, false); await a; await h.settle();
  assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
  const count = h.requests.length; await h.view.run(); assert.equal(h.requests.length, count);
  preflightB.respond({}, false); await b; await h.settle();
  assert.equal(h.view.status, 'failed'); assert.equal(h.toasts.length, 1);
});

test('submitted A preflight may finish A pipeline, never write to B or release its lock', async () => {
  const h = await ready();
  const a = h.view.run(), planA = h.requests.at(-1); await h.settle();
  h.render(task('B')); await h.settle();
  const b = h.view.run(), planB = h.requests.at(-1); await h.settle(); const before = snapshot(h);
  planA.respond({ encoder: 'A' }); await h.settle();
  const postA = h.requests.at(-1);
  assert.equal(JSON.parse(postA.options.body).task_id, 'A');
  postA.respond({ status: 'started' }); await a; await h.settle();
  assert.equal(snapshot(h), before); assert.ok(h.refreshes.includes('A'));
  const count = h.requests.length; await h.view.run(); assert.equal(h.requests.length, count);
  planB.respond({ detail: 'B invalid' }, false); await b;
});

for (const status of [500, 409]) test(`late A pipeline ${status} does not change active B`, async () => {
  const h = await ready(), { pending, request } = await reachPipeline(h);
  h.render(activeB()); await h.settle(); const before = snapshot(h);
  request.respond({ detail: status === 409 ? 'Pipeline already running' : 'A failed' }, false, status);
  await pending; await h.settle();
  assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
});

test('A to B to A uses a fresh startup scope, old finally cannot unlock the new A start', async () => {
  const h = await ready();
  const old = h.view.run(), oldPlan = h.requests.at(-1); await h.settle();
  h.render(task('B')); await h.settle(); h.render(task('A')); await h.settle();
  const current = h.view.run(), newPlan = h.requests.at(-1); await h.settle();
  assert.notEqual(oldPlan, newPlan); const before = snapshot(h);
  oldPlan.respond({ detail: 'old A failed' }, false); await old; await h.settle();
  assert.equal(snapshot(h), before);
  const count = h.requests.length; await h.view.run(); assert.equal(h.requests.length, count);
  newPlan.respond({ detail: 'new A failed' }, false); await current; await h.settle();
  assert.equal(h.toasts.length, 1); assert.equal(h.toasts[0][0], 'new A failed');
});

for (const ok of [true, false]) test(`old subtitle translation/export ${ok ? 'success' : 'failure'} stays with A`, async () => {
  const h = await ready(task('A', { last_process_route: 'subtitles', translations: {} }));
  const pending = h.view.run(), translate = h.requests.at(-1);
  assert.equal(translate.url, '/api/translate'); await h.settle();
  h.render(activeB()); await h.settle(); const before = snapshot(h);
  translate.respond({ detail: 'translation failed' }, ok); await h.settle();
  if (ok) {
    const exported = h.requests.at(-1); assert.equal(exported.url, '/api/export');
    assert.equal(exported.options.body.get('task_id'), 'A');
    exported.respond({});
  }
  await pending; await h.settle();
  assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
});

for (const burnStatus of ['completed', 'failed']) test(`old burn polling ${burnStatus} cannot alter B`, async () => {
  const h = await ready(task('A', { last_process_route: 'subtitles' }));
  h.view.setBurn(true); h.render();
  const pending = h.view.run(); await h.settle();
  assert.equal(h.requests.at(-1).url, '/api/export'); h.requests.at(-1).respond({}); await h.settle();
  assert.equal(h.requests.at(-1).url, '/api/burn');
  assert.equal(JSON.parse(h.requests.at(-1).options.body).task_id, 'A');
  h.requests.at(-1).respond({ status: 'started' }); await h.settle();
  h.render(activeB()); await h.settle(); const before = snapshot(h);
  h.tick(900); await h.settle(); assert.equal(h.requests.at(-1).url, '/api/burn/A');
  h.requests.at(-1).respond({ burn_status: burnStatus, burn_error: 'A render failed' });
  await pending; await h.settle();
  assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
});

for (const decision of ['approve', 'decline', 'error']) test(`late compression ${decision} cannot alter B`, async () => {
  const h = await ready(task('A', { pipeline_status: 'awaiting_compression' }));
  const pending = h.view.decideCompression(decision === 'approve'), request = h.requests.at(-1);
  h.render(activeB()); await h.settle(); const before = snapshot(h);
  request.respond({ detail: 'A decision error' }, decision !== 'error'); await pending; await h.settle();
  assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
});

test('late cancel failure does not notify B, current-task cancel failure still does', async () => {
  const h = await ready();
  const a = h.view.cancel(), requestA = h.requests.at(-1);
  h.render(activeB()); await h.settle();
  requestA.respond({}, false); await a; assert.equal(h.toasts.length, 0);
  const b = h.view.cancel(); h.requests.at(-1).respond({}, false); await b;
  assert.equal(h.toasts.length, 1);
});

test('completion notification waiting on refresh is suppressed after switching tasks', async () => {
  const h = await ready(task('A', { status: 'dubbing', pipeline_status: 'processing' }));
  const release = h.holdRefresh(); h.event({ status: 'completed', pipeline_status: 'completed' });
  h.render(activeB()); await h.settle(); const before = snapshot(h);
  release(); await h.settle(); assert.equal(snapshot(h), before); assert.equal(h.toasts.length, 0);
});

test('unmount suppresses late state writes and notifications, old actions cannot restart', async () => {
  const h = await ready(), { pending, request } = await reachPipeline(h);
  const oldRun = h.view.run, oldCancel = h.view.cancel, oldDecision = h.view.decideCompression;
  h.unmount(); const writes = h.stateWrites;
  request.respond({ detail: 'late error' }, false); await pending;
  assert.equal(h.stateWrites, writes); assert.equal(h.toasts.length, 0);
  const count = h.requests.length;
  await oldRun(); await oldCancel(); await oldDecision(true);
  assert.equal(h.requests.length, count);
});

test('settings voice catalog response remains usable after a task switch', async () => {
  const h = await ready(task('A'), { automaticVoices: false });
  const catalog = h.requests.find(item => item.url === '/api/dubbing/voices');
  h.render(task('B')); await h.settle();
  catalog.respond({ voices: [{ id: 'voice-B', language: 'zh' }], tts_mode: 'qwen' }); await h.settle();
  assert.equal(h.view.voice, 'voice-B'); assert.equal(h.view.matchingVoices.length, 1);
});

test('effect cleanup/setup replay keeps current task scope active', async () => {
  const h = await ready(); h.replayEffects(); await h.settle();
  const pending = h.view.run(); assert.ok(h.requests.at(-1).url.endsWith('/A/video-plan'));
  h.requests.at(-1).respond({ detail: 'current validation failed' }, false); await pending; await h.settle();
  assert.equal(h.view.status, 'failed'); assert.equal(h.toasts.length, 1);
});

test('language change during a pending start cannot restore old terminal state or unlock SSE', async () => {
  const h = await ready();
  const pending = h.view.run(), plan = h.requests.at(-1); await h.settle();
  h.changeLanguage(); await h.settle();
  assert.equal(h.view.startPending, true); assert.equal(h.view.running, true);
  const count = h.requests.length; await h.view.run(); assert.equal(h.requests.length, count);
  plan.respond({ detail: 'invalid output' }, false); await pending; await h.settle();
  assert.equal(h.view.startPending, false); assert.equal(h.view.status, 'failed');
});

test('cancel request and persisted worker cleanup keep UI locked until final cancellation', async () => {
  const h = await ready(activeB());
  const pending = h.view.cancel(), request = h.requests.at(-1); await h.settle();
  assert.equal(h.view.cancelPending, true);
  const count = h.requests.length; await h.view.cancel(); assert.equal(h.requests.length, count);
  const stopping = { ...activeB(), status: 'pipeline_cancelling', pipeline_status: 'cancelling', pipeline_cleanup_pending: true };
  h.event(stopping); h.render(stopping); await h.settle();
  assert.equal(h.view.status, 'cancelling'); assert.equal(h.view.running, true);
  assert.equal(h.view.cleanupWaiting, true); assert.equal(h.view.message, 'pipelineStopping');
  request.respond({ status: 'cancelling' }); await pending; await h.settle();
  assert.equal(h.view.running, true); assert.equal(h.view.cancelPending, false);
  const locked = h.requests.length; await h.view.run(); await h.view.cancel(); assert.equal(h.requests.length, locked);
  h.event({ ...stopping, status: 'cancelled', pipeline_status: 'cancelled', pipeline_cleanup_pending: false });
  h.render({ ...stopping, status: 'cancelled', pipeline_status: 'cancelled', pipeline_cleanup_pending: false }); await h.settle();
  assert.equal(h.view.running, false); assert.equal(h.view.cleanupWaiting, false);
});

test('reloading a pending cleanup restores stopping status without any HTTP start', async () => {
  const h = await ready(task('A', { pipeline_status: 'cancelling', pipeline_cleanup_pending: true, progress_percent: 57, cancelled_stage: 'pipeline_rendering' }));
  assert.equal(h.view.status, 'cancelling'); assert.equal(h.view.progress, 57);
  assert.equal(h.view.running, true); assert.equal(h.view.currentStage, 'render');
  assert.equal(h.requests.length, 0);
});

test('language change cannot restore older props over a live cancellation', async () => {
  const h = await ready({ ...activeB(), state_revision: 10 });
  h.event({ ...activeB(), state_revision: 11, status: 'pipeline_cancelling', pipeline_status: 'cancelling', pipeline_cleanup_pending: true });
  await h.settle(); h.changeLanguage(); await h.settle();
  assert.equal(h.view.status, 'cancelling'); assert.equal(h.view.cleanupWaiting, true);
  assert.equal(h.view.running, true);
});

test('language change preserves newer same-stage live progress', async () => {
  const h = await ready({ ...activeB(), state_revision: 10 });
  h.event({ ...activeB(), state_revision: 11, progress: 83 }); await h.settle();
  h.changeLanguage(); await h.settle();
  assert.equal(h.view.progress, 83); assert.equal(h.view.running, true);
});

test('newer authoritative run can replace an old stopping display', async () => {
  const stopping = { ...activeB(), state_revision: 11, pipeline_status: 'cancelling', pipeline_cleanup_pending: true };
  const h = await ready(stopping);
  const next = { ...activeB(), state_revision: 12, pipeline_run_id: 'new', pipeline_cleanup_pending: false };
  h.event(next); h.render(next); await h.settle();
  assert.equal(h.view.status, 'processing'); assert.equal(h.view.cleanupWaiting, false);
  assert.equal(h.view.running, true);
});
