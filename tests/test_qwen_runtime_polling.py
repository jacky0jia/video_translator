"""Exercise the actual frontend polling code with deterministic requests/timers."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_runtime_polling_serializes_recovers_and_cleans_up():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend regression tests")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const source = fs.readFileSync('app/frontend/src/settings/pollRuntimeStatus.js', 'utf8');
  const {pollRuntimeStatus} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  const timers = new Map();
  let nextId = 0;
  global.setTimeout = (callback, delay) => { timers.set(++nextId, {callback, delay}); return nextId; };
  global.clearTimeout = id => timers.delete(id);
  const requests = [], statuses = [];
  global.fetch = (url, options) => new Promise((resolve, reject) => {
    assert.equal(url, '/api/qwen-tts/runtime-status');
    requests.push({resolve, reject, signal: options.signal});
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  });
  const flush = async () => { for (let i=0; i<8; i++) await Promise.resolve(); };
  const fire = delay => {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, `Missing ${delay} ms timer`);
    timers.delete(entry[0]); entry[1].callback();
  };
  let stop = pollRuntimeStatus(value => statuses.push(value));
  assert.equal(requests.length, 1);
  assert.deepEqual([...timers.values()].map(x => x.delay), [10000], 'No overlapping refresh while pending');
  requests[0].resolve({ok:true, json:async () => ({state:'running'})});
  await flush();
  assert.deepEqual(statuses, [{state:'running'}]);
  fire(3000);
  fire(10000);
  await flush();
  assert.ok(requests[1].signal.aborted, 'Hung requests must time out');
  assert.equal(statuses.at(-1), null);
  fire(3000);
  requests[2].resolve({ok:false});
  await flush();
  assert.equal(statuses.at(-1), null, 'HTTP failure clears stale status');
  fire(3000);
  requests[3].resolve({ok:true, json:async () => ({state:'succeeded'})});
  await flush();
  assert.equal(statuses.at(-1).state, 'succeeded', 'Polling recovers after failure');
  stop();
  assert.equal(timers.size, 0, 'Cleanup removes scheduled refresh');
  stop = pollRuntimeStatus(value => statuses.push(value));
  const count = statuses.length;
  stop();
  await flush();
  assert.ok(requests.at(-1).signal.aborted);
  assert.equal(statuses.length, count, 'Cleanup abort must not update unmounted state');
  assert.equal(timers.size, 0);
  // A response body can finish even after abort; it must also be ignored.
  let finishBody;
  stop = pollRuntimeStatus(value => statuses.push(value));
  requests.at(-1).resolve({ok:true, json:() => new Promise(resolve => { finishBody = resolve; })});
  await flush();
  stop();
  finishBody({state:'old'});
  await flush();
  assert.equal(statuses.length, count);
  assert.equal(timers.size, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
