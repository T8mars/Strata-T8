import assert from 'node:assert/strict';
import { statSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { currentUserSid } from '../../server/private-file.mjs';

/** The effective protection of a file: mode 0600 on POSIX, the exact DACL (one protected entry for this user) on NTFS. */
export function assertPrivate(path) {
  if (process.platform !== 'win32') { assert.equal(statSync(path).mode & 0o777, 0o600); return; }
  const script = `$acl=Get-Acl -LiteralPath '${path.replace(/'/g, "''")}'; @{protected=$acl.AreAccessRulesProtected;rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]) | ForEach-Object { @{sid=$_.IdentityReference.Value;rights=[int]$_.FileSystemRights;allow=($_.AccessControlType -eq 'Allow');inherited=$_.IsInherited} })} | ConvertTo-Json -Compress -Depth 4`;
  const raw = execFileSync(join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'), ['-NoProfile', '-NonInteractive', '-Command', script],
    { encoding: 'utf8', timeout: 30000, windowsHide: true,
      env: { ...process.env, PSModulePath: join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'Modules') } }).trim();
  // SDDL shortens the built-in Administrator's SID to LA on some Windows versions.
  // Ask NTFS for SecurityIdentifier rules so aliases cannot change the comparison.
  assert.deepEqual(JSON.parse(raw), { protected: true, rules: [
    { sid: currentUserSid(), rights: 2032127, allow: true, inherited: false },
  ] }, `ACL is not owner-only: ${raw}`);
}
