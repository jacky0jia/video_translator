const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('../app/frontend/node_modules/esbuild');

// Execute the real App callbacks with deterministic network ordering. Child
// components are inert so these tests cannot touch a running backend or GPU.
function harness() {
  const cells = [], requests = [], toasts = [];
  let index = 0, tree, stream;
  const react = {
    useState(initial) {
      const slot = index++;
      if (!(slot in cells)) cells[slot] = typeof initial === 'function' ? initial() : initial;
      return [cells[slot], value => { cells[slot] = typeof value === 'function' ? value(cells[slot]) : value; }];
    },
    useRef(initial) { const slot = index++; return cells[slot] ||= { current: initial }; },
    useCallback: fn => fn, useEffect() {},
  };
  const jsx = (type, props) => ({ type, props });
  const root = path.resolve(__dirname, '../app/frontend/src');
  function compile(file, requireModule) {
    const mod = { exports: {} };
    vm.runInNewContext(esbuild.transformSync(fs.readFileSync(file, 'utf8'), {
      format: 'cjs', loader: file.endsWith('.jsx') ? 'jsx' : 'js', jsx: 'automatic',
    }).code, {
      module: mod, exports: mod.exports, require: requireModule, console,
      fetch: (url, options) => new Promise(resolve => requests.push({ url, options,
        respond(data, ok = true) { resolve({ ok, json: async () => data }); },
      })),
    });
    return mod.exports;
  }
  const scope = compile(path.join(root, 'utils/taskRequestScope.js'));
  const toast = (...args) => toasts.push(args);
  const App = compile(path.join(root, 'App.jsx'), name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name.includes('taskRequestScope')) return scope;
    if (name.includes('useTaskSSE')) return { useTaskSSE: (id, callback) => { stream = { id, callback }; } };
    if (name.includes('ToastContext')) return { useToast: () => toast };
    if (name.includes('I18nContext')) return { useI18n: () => ({ t: key => key, lang: 'en' }) };
    if (name.includes('ThemeContext')) return { useTheme: () => ({ theme: 'light' }) };
    return { __esModule: true, default: name.split('/').pop() };
  }).default;
  function render() { index = 0; tree = App(); }
  function props(name, node = tree) {
    if (!node || typeof node !== 'object') return;
    if (node.type === name) return node.props;
    for (const child of [node.props?.children].flat(Infinity)) {
      if (!child || typeof child !== 'object') continue;
      const found = props(name, child); if (found) return found;
    }
  }
  render();
  return { render, props, requests, toasts, event: data => stream.callback(data) };
}
const task = (id, status = 'transcribed') => ({ task_id: id, filename: `${id}.mp4`, status, transcription_path: `${id}.json` });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
async function seed(h) {
  const promise = h.props('HistoryList').onRefresh();
  h.requests.at(-1).respond({ tasks: [task('A'), task('B')] });
  await promise; h.render();
}
async function select(h, id) {
  const promise = h.props('HistoryList').onSelect(id);
  h.requests.at(-1).respond({ task: task(id) });
  await promise; h.render();
}

test('late A selection cannot replace B, including A to B to A', async () => {
  const h = harness(); await seed(h);
  const old = h.props('HistoryList').onSelect('A'), oldRequest = h.requests.at(-1);
  await select(h, 'B'); await select(h, 'A');
  oldRequest.respond({ task: task('A', 'failed') }); await old; h.render();
  assert.equal(h.props('ControlPanel').task.status, 'transcribed');
});

test('background completion refreshes history without selecting its old task', async () => {
  const h = harness(); await seed(h); await select(h, 'A');
  const oldCallback = h.props('ControlPanel').onTaskRefresh;
  await select(h, 'B');
  const start = h.requests.length, refresh = oldCallback('A');
  assert.equal(h.requests.length, start + 1); // list only, no A detail request
  h.requests.at(-1).respond({ tasks: [task('A', 'completed'), task('B')] });
  await refresh; h.render();
  assert.equal(h.props('ControlPanel').task.task_id, 'B');
});

test('A detail arriving after B detail cannot change the selection', async () => {
  const h = harness(); await seed(h);
  const pending = h.props('HistoryList').onSelect('A'), detail = h.requests.at(-1);
  await select(h, 'B'); detail.respond({ task: task('A') }); await pending; h.render();
  assert.equal(h.props('ControlPanel').task.task_id, 'B');
});

test('refresh started before switching tasks cannot restore the old task', async () => {
  const h = harness(); await seed(h); await select(h, 'A');
  const pending = h.props('ControlPanel').onTaskRefresh('A');
  const list = h.requests.at(-2), detail = h.requests.at(-1);
  await select(h, 'B');
  list.respond({ tasks: [task('A', 'completed'), task('B')] });
  detail.respond({ task: task('A', 'completed') }); await pending; h.render();
  assert.equal(h.props('ControlPanel').task.task_id, 'B');
});

test('newer same-task detail wins even if older refresh finishes later', async () => {
  const h = harness(); await seed(h); await select(h, 'A');
  const refresh = h.props('ControlPanel').onTaskRefresh;
  const first = refresh('A'), oldList = h.requests.at(-2), oldDetail = h.requests.at(-1);
  const second = refresh('A');
  h.requests.at(-2).respond({ tasks: [task('A', 'completed')] });
  h.requests.at(-1).respond({ task: task('A', 'completed') }); await second;
  oldList.respond({ tasks: [task('A', 'failed')] }); oldDetail.respond({ task: task('A', 'failed') }); await first;
  h.render(); assert.equal(h.props('ControlPanel').task.status, 'completed');
  assert.equal(h.props('HistoryList').tasks[0].status, 'completed');
});

test('list snapshot does not overwrite newer selected detail', async () => {
  const h = harness(); await seed(h);
  const list = h.props('HistoryList').onRefresh(), request = h.requests.at(-1);
  const selected = h.props('HistoryList').onSelect('A');
  h.requests.at(-1).respond({ task: task('A', 'completed') }); await selected;
  request.respond({ tasks: [task('A', 'failed')] }); await list; h.render();
  assert.equal(h.props('ControlPanel').task.status, 'completed');
});

for (const all of [false, true]) test(`deleting ${all ? 'all tasks' : 'selected task'} invalidates pending detail and list`, async () => {
  const h = harness(); await seed(h);
  const pendingList = h.props('HistoryList').onRefresh(), list = h.requests.at(-1);
  const pending = h.props('HistoryList').onSelect('A'), detail = h.requests.at(-1);
  h.render();
  if (all) h.props('HistoryList').onDeleteAll(); else h.props('HistoryList').onDelete(['A']);
  list.respond({ tasks: [task('A'), task('B')] }); detail.respond({ task: task('A') });
  await Promise.all([pendingList, pending]); h.render();
  assert.equal(h.props('ControlPanel').task, null);
  assert.ok(!h.props('HistoryList').tasks.some(item => item.task_id === 'A'));
});

test('obsolete selection errors do not toast over the newly selected task', async () => {
  const h = harness(); await seed(h);
  const pending = h.props('HistoryList').onSelect('A'), request = h.requests.at(-1);
  await select(h, 'B'); request.respond({ detail: 'old A failed' }, false); await pending;
  assert.equal(h.toasts.length, 0);
});

test('background refresh preserves user subtitle visibility', async () => {
  const h = harness(); await seed(h); await select(h, 'A');
  h.props('VideoPreview').onShowSourceChange(false); h.render();
  const pending = h.props('ControlPanel').onTaskRefresh('A');
  h.requests.at(-2).respond({ tasks: [task('A')] }); h.requests.at(-1).respond({ task: task('A') });
  await pending; h.render(); assert.equal(h.props('VideoPreview').showSource, false);
});

test('upload completion does not steal selection after user navigates away', async () => {
  const h = harness(); await seed(h);
  h.props('UploadForm').onUploadStart('upload.mp4', 'Chinese'); h.render();
  await select(h, 'B'); h.props('UploadForm').onUpload('new');
  assert.equal(h.requests.at(-1).url, '/api/tasks');
  h.requests.at(-1).respond({ tasks: [task('new'), task('B')] }); await flush(); h.render();
  assert.equal(h.props('ControlPanel').task.task_id, 'B');
});

test('upload still selects its real task when placeholder remains selected', async () => {
  const h = harness(); await seed(h);
  h.props('UploadForm').onUploadStart('upload.mp4', 'Chinese'); h.render();
  h.props('UploadForm').onUpload('new');
  const detail = h.requests.find(item => item.url === '/api/tasks/new');
  detail.respond({ task: task('new') }); h.requests.at(-1).respond({ tasks: [task('new')] });
  await flush(); h.render(); assert.equal(h.props('ControlPanel').task.task_id, 'new');
  assert.equal(h.props('VideoPreview').showSource, true);
});

test('incoming live event invalidates an older detail snapshot', async () => {
  const h = harness(); await seed(h);
  h.props('UploadForm').onUploadStart('upload.mp4', 'Chinese'); h.render();
  h.props('UploadForm').onUpload('A');
  h.requests.find(item => item.url === '/api/tasks/A').respond({ task: task('A') });
  h.requests.at(-1).respond({ tasks: [task('A')] }); await flush(); h.render();
  const pending = h.props('ControlPanel').onTaskRefresh('A');
  const list = h.requests.at(-2), detail = h.requests.at(-1);
  h.event({ status: 'transcribing', progress_percent: 65 });
  list.respond({ tasks: [task('A', 'pending')] }); detail.respond({ task: task('A', 'pending') });
  await pending; h.render(); assert.equal(h.props('ControlPanel').task.status, 'transcribing');
});

test('initial upload event cannot discard the only detail load', async () => {
  const h = harness(); await seed(h);
  h.props('UploadForm').onUploadStart('upload.mp4', 'Chinese'); h.render();
  h.props('UploadForm').onUpload('new');
  const detail = h.requests.find(item => item.url === '/api/tasks/new');
  h.render(); h.event({ status: 'transcribing' });
  detail.respond({ task: task('new', 'transcribing') });
  h.requests.at(-1).respond({ tasks: [task('new')] }); await flush(); h.render();
  assert.equal(h.props('ControlPanel').task.task_id, 'new');
  assert.equal(h.props('ControlPanel').task.transcription_path, 'new.json');
});

if (harness().props('HistoryList').onCancelImport) {
  test('URL import cancellation refreshes selected detail without navigating', async () => {
    const h = harness(); await seed(h); await select(h, 'A');
    const pending = h.props('HistoryList').onCancelImport('A');
    assert.equal(h.requests.at(-1).options.method, 'POST');
    h.requests.at(-1).respond({ status: 'cancelled' }); await flush();
    assert.equal(h.requests.at(-1).url, '/api/tasks/A');
    h.requests.at(-2).respond({ tasks: [task('A', 'cancelled')] });
    h.requests.at(-1).respond({ task: task('A', 'cancelled') }); await pending; h.render();
    assert.equal(h.props('ControlPanel').task.status, 'cancelled');
  });
}
