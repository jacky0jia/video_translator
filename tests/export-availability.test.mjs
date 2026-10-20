import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontend = fileURLToPath(new URL('../app/frontend/', import.meta.url));
const requireFrontend = createRequire(path.join(frontend, 'package.json'));
const { buildSync } = requireFrontend('esbuild');
const React = requireFrontend('react');
const { renderToStaticMarkup } = requireFrontend('react-dom/server');
const compiled = buildSync({
  stdin: { contents: `export { default as ExportPanel } from './src/components/ExportPanel.jsx';
    export { I18nProvider } from './src/contexts/I18nContext.jsx';`, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', external: ['react', 'react-dom/*'],
});
const component = new Module(path.join(frontend, '__export-test.cjs'));
component.filename = path.join(frontend, '__export-test.cjs');
component.paths = Module._nodeModulePaths(frontend);
component._compile(compiled.outputFiles[0].text, component.filename);
const { ExportPanel, I18nProvider } = component.exports;

function render(task, language = 'en') {
  globalThis.window = { location: { origin: 'http://localhost' } };
  globalThis.localStorage = { getItem: () => language };
  try {
    return renderToStaticMarkup(React.createElement(I18nProvider, null, React.createElement(ExportPanel, { task })));
  } finally { delete globalThis.window; delete globalThis.localStorage; }
}
const task = {
  task_id: 'A', last_process_route: 'dubbing', target_lang: 'Chinese',
  subtitle_outputs: { srt: '/static/output/target.srt' },
  dubbing_audio_path: '/static/output/audio.wav', dubbing_video_path: '/static/output/video.mp4',
};
const links = html => (html.match(/<a\b[^>]*\bdownload=/g) || []).length;
const disabled = html => (html.match(/<button\b[^>]*\bdisabled=/g) || []).length;

test('unavailable outputs remain visible but have no actionable preview or download', () => {
  const html = render({ ...task, output_availability: {
    '/static/output/target.srt': false, '/static/output/audio.wav': false, '/static/output/video.mp4': false,
  } });
  assert.equal(links(html), 0);
  assert.equal(disabled(html), 6);
  assert.match(html, /0 available files/);
  assert.equal((html.match(/<p\b[^>]*role="status"[^>]*>File missing, empty or inaccessible/g) || []).length, 3);
  assert.match(html, /Dubbed video/);
});

test('one missing output does not disable other usable files or discard stale-output warning', () => {
  const html = render({ ...task, outputs_stale: true, output_availability: {
    '/static/output/target.srt': true, '/static/output/audio.wav': false, '/static/output/video.mp4': true,
  } });
  assert.equal(links(html), 2);
  assert.equal(disabled(html), 2);
  assert.match(html, /2 available files/);
  assert.match(html, /created before the subtitle edit/);
});

test('old task metadata cannot authorize a different output path', () => {
  const html = render({ ...task, output_availability: { '/static/output/old.wav': true } });
  assert.equal(links(html), 0);
  assert.equal(disabled(html), 6);
  assert.match(html, /File availability has not been checked/);
});

test('legacy metadata is unknown, not a successful file check', () => {
  const html = render(task, 'zh');
  assert.equal(links(html), 0);
  assert.equal(disabled(html), 6);
  assert.match(html, /尚未检查文件可用性/);
});

test('subtitle-only burn uses its own file availability and normal files remain enabled', () => {
  const html = render({ ...task, last_process_route: 'subtitles', last_burn_enabled: true,
    burn_path: '/static/output/burn.mp4', output_availability: {
      '/static/output/target.srt': true, '/static/output/burn.mp4': true,
    } });
  assert.equal(links(html), 2);
  assert.equal(disabled(html), 0);
  assert.match(html, /2 available files/);
});

test('unsafe output URL cannot be enabled by availability metadata', () => {
  const html = render({ task_id: 'A', last_process_route: 'dubbing', dubbing_video_path: 'https://example.invalid/video.mp4',
    output_availability: { 'https://example.invalid/video.mp4': true } });
  assert.equal(links(html), 0);
  assert.equal(disabled(html), 2);
  assert.match(html, /0 available files/);
});

test('literal URL fragments do not enable a preview of a different file', () => {
  const html = render({ task_id: 'A', last_process_route: 'dubbing', dubbing_video_path: '/static/output/part#1.mp4',
    output_availability: { '/static/output/part#1.mp4': true } });
  assert.equal(links(html), 0);
  assert.equal(disabled(html), 2);
});
