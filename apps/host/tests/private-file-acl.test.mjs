import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { makePrivate } from '../server/private-file.mjs';
import { assertPrivate } from './helpers/assert-private.mjs';

test('owner-only ACL removes explicit Windows principals and survives repeated writes', { skip: process.platform !== 'win32' }, t => {
  const directory = mkdtempSync(join(tmpdir(), 'strata-private-acl-'));
  assert.equal(dirname(resolve(directory)), resolve(tmpdir()));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const file = join(directory, "密钥 ' $literal.json");
  writeFileSync(file, 'private test data', { mode: 0o600 });
  execFileSync(join(process.env.SystemRoot, 'System32', 'icacls.exe'), [file, '/grant:r', '*S-1-5-18:F', '*S-1-5-32-544:F', '/inheritance:r'],
    { stdio: 'ignore', timeout: 15000, windowsHide: true });
  makePrivate(file);
  assertPrivate(file);
  makePrivate(file);
  assertPrivate(file);
  assert.equal(readFileSync(file, 'utf8'), 'private test data');
});
