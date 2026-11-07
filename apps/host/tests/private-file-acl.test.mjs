import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { currentUserSid, makePrivate, makePrivateDir } from '../server/private-file.mjs';
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

test('private directories remove explicit groups and new nested files inherit only the user rule', { skip: process.platform !== 'win32' }, t => {
  const directory = mkdtempSync(join(tmpdir(), "strata-private-folder-中文-'-$-"));
  assert.equal(dirname(resolve(directory)), resolve(tmpdir()));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  execFileSync(join(process.env.SystemRoot, 'System32', 'icacls.exe'), [directory, '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '/inheritance:r'],
    { stdio: 'ignore', timeout: 15000, windowsHide: true });
  const acl = path => {
    const script = `$acl=Get-Acl -LiteralPath '${path.replaceAll("'", "''")}'; @{protected=$acl.AreAccessRulesProtected;rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]) | ForEach-Object { @{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');inherited=$_.IsInherited} })} | ConvertTo-Json -Compress -Depth 4`;
    return JSON.parse(execFileSync(join(process.env.SystemRoot, 'System32', 'WindowsPowerShell/v1.0/powershell.exe'), ['-NoProfile', '-NonInteractive', '-Command', script],
      { encoding: 'utf8', timeout: 30000, windowsHide: true,
        env: { ...process.env, PSModulePath: join(process.env.SystemRoot, 'System32', 'WindowsPowerShell/v1.0/Modules') } }));
  };
  makePrivateDir(directory);
  assert.deepEqual(acl(directory), { protected: true, rules: [{ sid: currentUserSid(), allow: true, inherited: false }] });
  const nested = join(directory, 'nested'); mkdirSync(nested);
  const file = join(nested, 'chat.json'); writeFileSync(file, 'private chat');
  assert.deepEqual(acl(file), { protected: false, rules: [{ sid: currentUserSid(), allow: true, inherited: true }] });
  makePrivateDir(directory);
  assert.deepEqual(acl(directory), { protected: true, rules: [{ sid: currentUserSid(), allow: true, inherited: false }] });
  assert.equal(readFileSync(file, 'utf8'), 'private chat');
});
