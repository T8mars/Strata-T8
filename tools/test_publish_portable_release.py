"""Real local Git tags and ZIP audits; mock only the GitHub command boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import publish_portable_release as publish
from tools.portable_version import archive_name
from tools.portable_build_provenance import ENGINE_ASSETS, build_identity
from tools.portable_weights import weight_declaration


def metadata(commit):
    return {'repository': 'T8mars/Strata-T8', 'version': '0.1.40-t8.1', 'revision': 1,
            'upstream_repository': 'Niko1221/Strata', 'upstream_version': '0.1.40',
            'upstream_release_tag': 'v0.1.40.1', 'upstream_commit': commit,
            'engine_version': '0.1.40', 'python_version': '3.12.10', 'protocol_version': 1,
            'engine_assets_sha256': {name: digit*64 for name, digit in zip(ENGINE_ASSETS.values(), ('a','b'))}}


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='Strata-release-test-')
        self.root = Path(self.tmp.name)/'checkout'
        self.remote = Path(self.tmp.name)/'origin.git'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True)
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', str(self.remote))
        (self.root/'CMakeLists.txt').write_text('project(strata VERSION 0.1.40)')
        (self.root/'.gitignore').write_text('dist/\nroadmap.md\n')
        (self.root/'RELEASE-NOTES.md').write_text('Release fixture notes')
        self.commit('upstream A')
        self.old = self.git('rev-parse', 'HEAD')
        self.meta = metadata(self.old)
        (self.root/'meta.json').write_text(json.dumps(self.meta))
        self.commit('tested B')
        self.head = self.git('rev-parse', 'HEAD')
        self.tag = 'v'+self.meta['version']
        self.assets = [self.root/'dist'/name for edition in publish.EDITIONS for name in
                       (archive_name(self.meta['version'], edition), archive_name(self.meta['version'], edition)+'.sha256')]
        for asset in self.assets:
            asset.parent.mkdir(exist_ok=True)
            asset.write_bytes(b'validated fixture artifact')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, encoding='utf-8', stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def push_tag(self, sha, annotated=False):
        args = ['tag', '-a', self.tag, sha, '-m', 'fixture tag'] if annotated else ['tag', self.tag, sha]
        self.git(*args)
        self.git('push', '-q', 'origin', 'refs/tags/'+self.tag)


class FourPartNativeCheckout(GitFixture):
    def test_exact_native_hotfix_can_publish_three_part_package_family(self):
        (self.root/'CMakeLists.txt').write_text('project(strata VERSION 0.1.40.2)')
        self.meta.update(upstream_version='0.1.40.2', engine_version='0.1.40.2',
                         upstream_release_tag='v0.1.40.2', version='0.1.40-t8.2', revision=2)
        (self.root/'meta.json').write_text(json.dumps(self.meta))
        self.commit('exact native hotfix source')
        meta, head = publish.checkout_identity(self.root)
        self.assertEqual(meta['version'], '0.1.40-t8.2')
        self.assertEqual(meta['engine_version'], '0.1.40.2')
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.meta['engine_version'] = '0.1.40'
        (self.root/'meta.json').write_text(json.dumps(self.meta))
        self.commit('incorrect old engine')
        with self.assertRaisesRegex(ValueError, 'versions differ'):
            publish.checkout_identity(self.root)


class GithubBoundary:
    """A GitHub API/CLI transport fake; every Git operation remains real."""
    def __init__(self, fixture, release=None, *, status=None, refuse_target=False, fail_upload=False):
        self.fixture, self.release = fixture, release
        self.status, self.refuse_target, self.fail_upload = status, refuse_target, fail_upload
        self.calls = []
        self.run = subprocess.run

    def __call__(self, args, **kwargs):
        if args[0] != 'gh':
            return self.run(args, **kwargs)
        self.calls.append(list(args))
        if args[1] == 'api':
            endpoint = args[3]
            if endpoint == 'graphql':
                node = None if self.release is None else {'databaseId':42,'isDraft':self.release['draft'],'tagName':self.release['tag_name']}
                code, payload = 200, {'data':{'repository':{'release':node}}}
            elif '/releases/tags/' in endpoint:
                code = 404 if self.release is None or self.release['draft'] else 200
                payload = self.release if code == 200 else {'message':'Not Found'}
            else:
                code = 404 if self.release is None else 200
                payload = self.release if code == 200 else {'message':'Not Found'}
            if self.status:
                code, payload = self.status, {'message':'HTTP fixture error'}
            return subprocess.CompletedProcess(args, 0 if code == 200 else 1,
                f'HTTP/2.0 {code} Fixture\nContent-Type: application/json\n\n'+json.dumps(payload), 'API error' if code != 200 else '')
        action = args[2]
        if action == 'create':
            self.release = {'tag_name': self.fixture.tag, 'draft': True, 'prerelease': False, 'assets': [],
                            'target_commitish': args[args.index('--target')+1]}
        elif action == 'edit' and '--draft=true' in args:
            if not self.refuse_target:
                self.release['target_commitish'] = args[args.index('--target')+1]
        elif action == 'edit' and '--draft=false' in args:
            self.release['draft'] = False
            if publish.remote_tag_commit(self.fixture.root, self.fixture.tag) is None:
                self.fixture.push_tag(self.fixture.head)
        elif action == 'upload' and self.fail_upload:
            return subprocess.CompletedProcess(args, 1, '', 'upload failure')
        elif action == 'upload':
            self.release['assets'] = [{'name':p.name,'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest(),
                                      'size':p.stat().st_size,'state':'uploaded'} for p in self.fixture.assets]
        return subprocess.CompletedProcess(args, 0, 'fixture success', '')


class DraftCommitBinding(GitFixture):
    def draft(self, target=None, draft=True):
        return {'tag_name': self.tag, 'draft': draft, 'prerelease': False, 'assets': [],
                'target_commitish': target or self.old}

    def invoke(self, boundary):
        with mock.patch.object(publish.subprocess, 'run', side_effect=boundary):
            return publish._publish_verified_assets(self.root, self.meta, self.head, self.assets)

    def test_old_lightweight_remote_tag_blocks_all_github_mutations(self):
        self.push_tag(self.old)
        boundary = GithubBoundary(self, self.draft())
        with self.assertRaisesRegex(ValueError, 'tag differs'):
            self.invoke(boundary)
        self.assertEqual(boundary.calls, [])

    def test_old_annotated_remote_tag_is_peeled_and_rejected(self):
        self.push_tag(self.old, annotated=True)
        self.assertEqual(publish.remote_tag_commit(self.root, self.tag), self.old)
        boundary = GithubBoundary(self, self.draft())
        with self.assertRaisesRegex(ValueError, 'tag differs'):
            self.invoke(boundary)
        self.assertEqual(boundary.calls, [])

    def test_draft_without_tag_rebinds_A_to_tested_B_before_upload_and_publish(self):
        boundary = GithubBoundary(self, self.draft())
        result = self.invoke(boundary)
        self.assertTrue(result['published'])
        self.assertEqual(publish.remote_tag_commit(self.root, self.tag), self.head)
        actions = [args[2] for args in boundary.calls if args[1] == 'release']
        self.assertEqual(actions, ['edit', 'upload', 'edit'])
        rebinding = next(args for args in boundary.calls if '--draft=true' in args)
        self.assertEqual(rebinding[rebinding.index('--target')+1], self.head)
        upload = next(args for args in boundary.calls if args[1:3] == ['release','upload'])
        self.assertEqual(upload[6:-1], list(map(str, self.assets)))
        self.assertEqual(boundary.release['target_commitish'], self.head)

    def test_matching_annotated_tag_and_old_draft_target_can_be_corrected(self):
        self.push_tag(self.head, annotated=True)
        result = self.invoke(GithubBoundary(self, self.draft()))
        self.assertTrue(result['published'])

    def test_matching_tag_is_authority_when_api_ignores_target_update(self):
        self.push_tag(self.head, annotated=True)
        boundary = GithubBoundary(self, self.draft(), refuse_target=True)
        self.assertTrue(self.invoke(boundary)['published'])
        self.assertEqual(boundary.release['target_commitish'], self.old)

    def test_pending_draft_lookup_uses_graphql_and_rest_id_without_creating_duplicate(self):
        boundary = GithubBoundary(self, self.draft())
        self.assertTrue(self.invoke(boundary)['published'])
        self.assertTrue(any(args[3]=='graphql' for args in boundary.calls if args[1]=='api'))
        self.assertTrue(any(args[3].endswith('/releases/42') for args in boundary.calls if args[1]=='api'))
        self.assertFalse(any(args[1:3]==['release','create'] for args in boundary.calls))

    def test_graphql_errors_never_create_a_replacement_draft(self):
        boundary = GithubBoundary(self, self.draft())
        def failed_lookup(args, **kwargs):
            result = boundary(args, **kwargs)
            if args[:4] == ['gh','api','--include','graphql']:
                return subprocess.CompletedProcess(args, 0, 'HTTP/2.0 200 Fixture\n\n'+json.dumps(
                    {'data':{'repository':{'release':None}},'errors':[{'message':'Permission denied'}]}), '')
            return result
        with mock.patch.object(publish.subprocess,'run',side_effect=failed_lookup), self.assertRaisesRegex(RuntimeError, 'Draft release lookup failed'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertFalse(any(args[1]=='release' for args in boundary.calls))

    def test_disappearing_draft_by_id_never_creates_a_replacement(self):
        boundary = GithubBoundary(self, self.draft())
        def missing_draft(args, **kwargs):
            result = boundary(args, **kwargs)
            if args[:3] == ['gh','api','--include'] and args[3].endswith('/releases/42'):
                return subprocess.CompletedProcess(args, 1, 'HTTP/2.0 404 Fixture\n\n'+json.dumps({'message':'Not Found'}), 'API error')
            return result
        with mock.patch.object(publish.subprocess,'run',side_effect=missing_draft), self.assertRaisesRegex(RuntimeError, 'Draft disappeared'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertFalse(any(args[1]=='release' for args in boundary.calls))

    def test_draft_target_must_be_confirmed_before_upload(self):
        boundary = GithubBoundary(self, self.draft(), refuse_target=True)
        with self.assertRaisesRegex(ValueError, 'Draft did not confirm'):
            self.invoke(boundary)
        self.assertFalse(any(args[1:3] == ['release','upload'] for args in boundary.calls))
        self.assertFalse(any('--draft=false' in args for args in boundary.calls))

    def test_published_release_with_correct_tag_remains_immutable(self):
        self.push_tag(self.head)
        boundary = GithubBoundary(self, self.draft(draft=False))
        self.assertTrue(self.invoke(boundary)['unchanged'])
        self.assertEqual(len(boundary.calls), 1)
        self.assertEqual(boundary.calls[0][1], 'api')

    def test_only_confirmed_404_can_create_release(self):
        for code in (401, 403, 429, 500):
            boundary = GithubBoundary(self, status=code)
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, f'HTTP {code}'):
                self.invoke(boundary)
            self.assertFalse(any(args[1] == 'release' for args in boundary.calls))
        boundary = GithubBoundary(self)
        self.assertTrue(self.invoke(boundary)['published'])
        creation = next(args for args in boundary.calls if args[1:3] == ['release','create'])
        self.assertEqual(creation[creation.index('--target')+1], self.head)

    def test_origin_network_error_prevents_all_api_calls(self):
        self.git('remote','set-url','origin', str(self.remote/'missing-repository'))
        boundary = GithubBoundary(self, self.draft())
        with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
            self.invoke(boundary)
        self.assertEqual(boundary.calls, [])

    def test_upload_failure_never_publishes(self):
        boundary = GithubBoundary(self, self.draft(), fail_upload=True)
        with self.assertRaisesRegex(RuntimeError, 'upload failure'):
            self.invoke(boundary)
        self.assertFalse(any('--draft=false' in args for args in boundary.calls))

    def test_draft_with_old_or_model_assets_is_not_modified_or_published(self):
        draft = self.draft()
        draft['assets'] = [{'name':'old-version-models.zip'}]
        boundary = GithubBoundary(self, draft)
        with self.assertRaisesRegex(ValueError, 'outside the four'):
            self.invoke(boundary)
        self.assertFalse(any(args[1]=='release' for args in boundary.calls))

    def test_wrong_remote_upload_digest_keeps_release_draft(self):
        boundary = GithubBoundary(self, self.draft())
        def wrong_digest(args, **kwargs):
            result = boundary(args, **kwargs)
            if args[:3] == ['gh','release','upload']:
                boundary.release['assets'][0]['digest'] = 'sha256:'+'0'*64
            return result
        with mock.patch.object(publish.subprocess,'run',side_effect=wrong_digest), self.assertRaisesRegex(ValueError, 'audited size and SHA256'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertTrue(boundary.release['draft'])
        self.assertFalse(any('--draft=false' in args for args in boundary.calls))

    def test_asset_changed_after_audit_cannot_be_uploaded(self):
        boundary = GithubBoundary(self, self.draft())
        reads = 0
        def change_asset(args, **kwargs):
            nonlocal reads
            result = boundary(args, **kwargs)
            if args[:2] == ['gh','api']:
                reads += 1
                if reads == 2: self.assets[0].write_bytes(b'changed after audit')
            return result
        with mock.patch.object(publish.subprocess,'run',side_effect=change_asset), self.assertRaisesRegex(ValueError, 'changed before upload'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertFalse(any(args[1:3]==['release','upload'] for args in boundary.calls))

    def test_checkout_changed_during_draft_confirmation_cannot_be_uploaded(self):
        boundary = GithubBoundary(self, self.draft())
        reads = 0
        def change_source(args, **kwargs):
            nonlocal reads
            result = boundary(args, **kwargs)
            if args[:2] == ['gh','api']:
                reads += 1
                if reads == 2: (self.root/'RELEASE-NOTES.md').write_text('untested notes')
            return result
        with mock.patch.object(publish.subprocess,'run',side_effect=change_source), self.assertRaisesRegex(ValueError, 'clean tested'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertFalse(any(args[1:3]==['release','upload'] for args in boundary.calls))

    def test_extra_local_old_version_asset_is_never_an_upload_candidate(self):
        boundary = GithubBoundary(self, self.draft())
        old = self.root/'dist/old-release.zip'
        old.write_bytes(b'old version')
        with mock.patch.object(publish.subprocess,'run',side_effect=boundary), self.assertRaisesRegex(ValueError, 'exactly the four'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets+[old])
        self.assertEqual(boundary.calls, [])

    def test_transport_failure_without_http_status_cannot_create_draft(self):
        calls = []
        original = subprocess.run
        def network_error(args, **kwargs):
            if args[0] != 'gh': return original(args, **kwargs)
            calls.append(args)
            return subprocess.CompletedProcess(args,1,'','transport unavailable')
        with mock.patch.object(publish.subprocess,'run',side_effect=network_error), self.assertRaisesRegex(RuntimeError, 'HTTP status'):
            publish._publish_verified_assets(self.root,self.meta,self.head,self.assets)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],'api')

    def test_clean_checkout_and_tracked_private_plan_are_checked_before_assets(self):
        self.assertEqual(publish.checkout_identity(self.root)[1], self.head)
        (self.root/'RELEASE-NOTES.md').write_text('untested change')
        with self.assertRaisesRegex(ValueError, 'clean tested'):
            publish.publish(self.root)
        self.git('restore', 'RELEASE-NOTES.md')
        (self.root/'roadmap.md').write_text('local secret plan')
        self.git('add','-f','roadmap.md')
        self.git('commit','-qm','incorrectly tracked plan')
        with self.assertRaisesRegex(ValueError, 'Private planning'):
            publish.publish(self.root)


class ArchiveAudit(GitFixture):
    def build_archive(self, *, extra=None, edit_manifest=None, edit_application=None, edit_build=None, corrupt_member=False):
        edition = 'Portable-NoModels'
        application = dict(self.meta, edition=edition, weights=weight_declaration(edition), models_included=False)
        if edit_application: edit_application(application)
        files = {'meta.json': json.dumps(application).encode(), 'app.py': b'tested fixture source'}
        engines = {}
        for directory, name in ENGINE_ASSETS.items():
            binary = b'fixture native executable '+directory.encode()
            after = hashlib.sha256(binary).hexdigest()
            build = {'version': '0.1.40', 'vision': 'none', 'upstream_asset':
                     {'repository': self.meta['upstream_repository'], 'release_tag': self.meta['upstream_release_tag'],
                      'name': name, 'sha256': self.meta['engine_assets_sha256'][name], 'upstream_commit': self.old},
                     'portable_patches': {'engine_utf8': {'sha256_before':'1'*64, 'sha256_after': after, 'manifest':'activeCodePage=UTF-8'}}}
            if edit_build: edit_build(build)
            engines[directory] = build
            files[directory+'/BUILD.json'] = json.dumps(build).encode()
            files[directory+'/strata.exe'] = binary
        if extra: files.update(extra)
        manifest = {'version':self.meta['version'], **build_identity(self.meta,'0.1.40'),
                    'source_commit':self.head, 'edition':edition, 'weights':weight_declaration(edition),
                    'models_included':False, 'engines':engines,
                    'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()]}
        if edit_manifest: edit_manifest(manifest)
        archive = self.root/'dist'/archive_name(self.meta['version'], edition)
        prefix = archive.stem
        with zipfile.ZipFile(archive,'w', compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr(prefix+'/PACKAGE-MANIFEST.json', json.dumps(manifest))
            for name, data in files.items():
                package.writestr(prefix+'/'+name, b'modified after manifest' if corrupt_member and name=='app.py' else data)
        checksum = archive.with_name(archive.name+'.sha256')
        checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n', encoding='ascii')
        return archive, checksum, edition

    def audit(self, **kwargs):
        archive, checksum, edition = self.build_archive(**kwargs)
        return publish.audit_archive(archive, checksum, self.meta, self.head, edition)

    def test_real_zip_member_hashes_and_source_provenance_pass(self):
        self.assertEqual(self.audit()['source_commit'], self.head)

    def test_wrong_source_commit_or_edition_stops_release(self):
        for key, value in (('source_commit',self.old), ('edition','VisionReady-NoMainModel'), ('upstream_release_tag','v0.1.40')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.audit(edit_manifest=lambda value_map:value_map.update({key:value}))

    def test_private_roadmap_and_main_mtp_tensor_files_are_rejected(self):
        for name in ('ROADMAP.MD','tools/RoadMap.Md','main.gguf','mtp.safetensors','models/other.txt'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.audit(extra={name:b'forbidden payload'})

    def test_weight_roles_and_packaged_metadata_cannot_disagree(self):
        with self.assertRaises(ValueError):
            self.audit(edit_manifest=lambda value:value['weights'].update(main=True))
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            self.audit(edit_application=lambda value:value.update(engine_version='0.1.39'))

    def test_engine_release_digest_and_utf8_after_hash_are_checked(self):
        for change in (lambda b:b['upstream_asset'].update(release_tag='v0.1.40'),
                       lambda b:b['upstream_asset'].update(sha256='c'*64),
                       lambda b:b['portable_patches']['engine_utf8'].update(sha256_after='d'*64)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.audit(edit_build=change)

    def test_member_tamper_is_detected_despite_valid_archive_checksum(self):
        with self.assertRaisesRegex(ValueError, 'member'):
            self.audit(corrupt_member=True)

    def test_archive_tamper_and_wrong_checksum_filename_are_rejected(self):
        archive, checksum, edition = self.build_archive()
        checksum.write_text('0'*64+'  '+archive.name+'\n')
        with self.assertRaisesRegex(ValueError, 'ZIP checksum'):
            publish.audit_archive(archive,checksum,self.meta,self.head,edition)
        checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  old-version.zip\n')
        with self.assertRaisesRegex(ValueError, 'Checksum file'):
            publish.audit_archive(archive,checksum,self.meta,self.head,edition)

    def test_unlisted_duplicate_or_wrong_root_members_are_rejected(self):
        for name in ('unlisted.txt','App.PY','../outside.txt'):
            archive, checksum, edition = self.build_archive()
            with zipfile.ZipFile(archive,'a') as package:
                package.writestr(archive.stem+'/'+name, b'unknown member')
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
            with self.subTest(name=name), self.assertRaises(ValueError):
                publish.audit_archive(archive,checksum,self.meta,self.head,edition)


if __name__ == '__main__': unittest.main()
