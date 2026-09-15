import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('navigation stage uses local nebula images', async () => {
  const html = await readFile(new URL('../../index.html', import.meta.url), 'utf8');
  assert.match(html, /\.\/assets\/kg-crab\.jpg/);
  assert.match(html, /\.\/assets\/kg-trifid\.jpg/);
});
