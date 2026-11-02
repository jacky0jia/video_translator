const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('../app/frontend/node_modules/esbuild');

function harness() {
  const connections = [], effects = [], timers = new Map();
  const ref = { current: null };
  class Stream {
    constructor(url) { this.url = url; this.listeners = {}; connections.push(this); }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    close() { this.closed = true; }
    message(value) { this.onmessage({ data: JSON.stringify(value) }); }
  }
  const mod = { exports: {} };
  const versions = { exports: {} };
  vm.runInNewContext(esbuild.transformSync(fs.readFileSync(path.resolve(__dirname, '../app/frontend/src/utils/taskStateVersion.js'), 'utf8'), { format: 'cjs' }).code,
    { module: versions, exports: versions.exports });
  const source = fs.readFileSync(path.resolve(__dirname, '../app/frontend/src/hooks/useTaskSSE.js'), 'utf8');
  vm.runInNewContext(esbuild.transformSync(source, { format: 'cjs' }).code, {
    module: mod, exports: mod.exports, EventSource: Stream,
    require: name => name === 'react' ? { useRef: () => ref, useEffect: effect => effects.push(effect) } : versions.exports,
    setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
  });
  return { hook: mod.exports.useTaskSSE, connections, effects, timers };
}

test('late messages and errors after task switch cannot affect the new task', () => {
  const h = harness(), received = [];
  h.hook('A', event => received.push(['A', event.status]));
  const cleanup = h.effects.pop()(), old = h.connections[0];
  old.message({ status: 'processing' });
  cleanup();
  h.hook('B', event => received.push(['B', event.status]));
  const finish = h.effects.pop()(), current = h.connections[1];
  old.message({ status: 'failed' });
  old.onerror();
  old.listeners.not_found();
  current.message({ status: 'completed' });
  assert.deepEqual(received, [['A', 'processing'], ['B', 'completed']]);
  assert.equal(h.timers.size, 0);
  assert.ok(!current.closed);
  finish();
});

test('disconnect invalidates a connection before reconnect and rejects all its later callbacks', () => {
  const h = harness(), received = [];
  h.hook('A', event => received.push(event.status));
  const cleanup = h.effects.pop()(), old = h.connections[0];
  old.onerror();
  old.message({ status: 'failed' });
  old.onerror();
  assert.equal(h.timers.size, 1);
  const [id, reconnect] = [...h.timers][0]; h.timers.delete(id); reconnect();
  const current = h.connections[1];
  old.listeners.not_found();
  old.message({ status: 'completed' });
  current.message({ status: 'dubbing' });
  assert.deepEqual(received, ['dubbing']);
  assert.ok(!current.closed);
  cleanup();
  current.message({ status: 'failed' });
  assert.deepEqual(received, ['dubbing']);
});

test('old stream cannot invoke the new task callback before effect cleanup', () => {
  const h = harness(), received = [];
  h.hook('A', event => received.push(['A', event.status]));
  const cleanup = h.effects.pop()(), old = h.connections[0];
  h.hook('B', event => received.push(['B', event.status]));
  old.message({ status: 'failed' });
  old.onerror();
  old.listeners.not_found();
  assert.deepEqual(received, []);
  assert.equal(h.timers.size, 0);
  cleanup();
  const finish = h.effects.pop()(), current = h.connections[1];
  current.message({ status: 'dubbing' });
  assert.deepEqual(received, [['B', 'dubbing']]);
  finish();
});

test('not_found and unmount clear retry timers and do not reconnect', () => {
  const h = harness();
  h.hook('A', () => assert.fail('Unexpected event'));
  const cleanup = h.effects.pop()(), stream = h.connections[0];
  stream.listeners.not_found();
  stream.message({ status: 'failed' });
  stream.onerror();
  assert.equal(h.timers.size, 0);
  cleanup();
  h.hook('B', () => {});
  const finish = h.effects.pop()(), next = h.connections[1];
  next.onerror();
  finish();
  assert.equal(h.timers.size, 0);
});

test('older progress cannot undo a newer snapshot, including after reconnect', () => {
  const h = harness(), received = [];
  h.hook('A', event => received.push(event.state_revision), 10);
  const cleanup = h.effects.pop()(), old = h.connections[0];
  old.message({ status: 'dubbing', state_revision: 9 });
  old.message({ status: 'dubbing', state_revision: 12 });
  old.message({ status: 'dubbing', state_revision: 11 });
  old.onerror();
  const [id, reconnect] = [...h.timers][0]; h.timers.delete(id); reconnect();
  const current = h.connections[1];
  current.message({ status: 'dubbing', state_revision: 10 });
  current.message({ status: 'pipeline_cancelling', state_revision: 13 });
  current.message({ status: 'completed', state_revision: 12 });
  assert.deepEqual(received, [12, 13]);
  cleanup();
});
