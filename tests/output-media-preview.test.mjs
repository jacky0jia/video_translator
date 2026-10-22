import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../app/frontend/', import.meta.url));
const requireFrontend = createRequire(path.join(frontend, 'package.json'));
const React = requireFrontend('react');
const { renderToStaticMarkup } = requireFrontend('react-dom/server');
const { buildSync } = requireFrontend('esbuild');
const compiled = buildSync({
  entryPoints: [path.join(frontend, 'src/components/OutputMediaPreview.jsx')],
  bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', external: ['react', 'react-dom/*'],
});
const component = new Module(path.join(frontend, '__media-preview-test.cjs'));
component.filename = path.join(frontend, '__media-preview-test.cjs');
component.paths = Module._nodeModulePaths(frontend);
component._compile(compiled.outputFiles[0].text, component.filename);
const { default: OutputMediaPreview, formatPreviewTime } = component.exports;

test('preview timestamps retain hours for long outputs without wrapping minutes', () => {
  for (const [value, expected] of [[0, '0:00'], [9.9, '0:09'], [65, '1:05'], [3601, '1:00:01'], [45065, '12:31:05']]) {
    assert.equal(formatPreviewTime(value), expected);
  }
});

test('unknown or invalid media duration never displays NaN or negative timestamps', () => {
  for (const value of [NaN, Infinity, -5, undefined]) assert.equal(formatPreviewTime(value), '0:00');
});

for (const type of ['audio', 'video']) {
  test(`${type} preview keeps native controls and disables separate seeking until metadata arrives`, () => {
    const html = renderToStaticMarkup(React.createElement(OutputMediaPreview, {
      type, src: `/static/output/file.${type === 'audio' ? 'wav' : 'mp4'}`, label: 'Result', t: key => key,
    }));
    assert.match(html, new RegExp(`<${type}[^>]*controls=""[^>]*preload="metadata"`));
    assert.match(html, /<input[^>]*type="range"[^>]*disabled=""/);
    assert.match(html, /aria-label="previewPosition Result"/);
    assert.match(html, /aria-valuetext="0:00 \/ 0:00"/);
  });
}
