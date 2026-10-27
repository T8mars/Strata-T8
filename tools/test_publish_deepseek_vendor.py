"""Verify the actual DeepSeek entrypoint with a real signed vendor manifest."""
import base64
import copy
import hashlib
import unittest
from unittest import mock

from strata_control import engine_component, signing
from tools import publish_portable_release as publish


class DeepSeekVendorAudit(unittest.TestCase):
    def setUp(self):
        self.key = signing.generate_key()
        self.descriptor = {'name': 'strata-dsv41-0.1.51-windows-x86_64-cuda-cuda13.tar.gz',
                           'version': '0.1.51', 'backend': 'cuda', 'variant': 'cuda13', 'sha256': 'a'*64}
        self.manifest = {'schema_version': 2, 'id': 'strata-engine', 'name': 'strata-engine',
                         'version': '0.1.51', 'platform': 'windows-x86_64', 'backend': 'cuda',
                         'variant': 'cuda13', 'build_id': 'fixture', 'archs': ['sm_86'],
                         'entrypoint': 'strata-dsv41.exe', 'files': []}
        self.listed = {}
        for name in ('strata-dsv41.exe', 'cublas64_13.dll', 'BUILD.json'):
            content = name.encode()
            digest = hashlib.sha256(content).hexdigest()
            self.manifest['files'].append({'path': name, 'bytes': len(content), 'sha256': digest})
            self.listed['engine-dsv41/'+name] = {'size': len(content), 'sha256': digest}
        self.trusted = {self.key['key_id']: base64.b64decode(self.key['public'])}

    def receipt(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['signature'] = signing.sign_bytes(self.key, engine_component.signing_payload(manifest))
        return {'archive': dict(self.descriptor), 'manifest': manifest}

    def audit(self, receipt=None):
        with mock.patch.object(signing, 'load_trusted_keys', return_value=self.trusted):
            publish.audit_deepseek_vendor(receipt or self.receipt(), self.descriptor, self.listed)

    def test_deepseek_name_and_unmodified_vendor_libraries_pass(self):
        self.listed['engine-dsv41/BUILD.json'] = {'size': 123, 'sha256': 'b'*64}
        self.audit()

    def test_missing_or_modified_executable_and_library_are_rejected(self):
        for name in ('strata-dsv41.exe', 'cublas64_13.dll'):
            original = self.listed.pop('engine-dsv41/'+name)
            with self.subTest(name=name, change='missing'), self.assertRaisesRegex(ValueError, 'signed vendor'):
                self.audit()
            self.listed['engine-dsv41/'+name] = dict(original, sha256='c'*64)
            with self.subTest(name=name, change='modified'), self.assertRaisesRegex(ValueError, 'signed vendor'):
                self.audit()
            self.listed['engine-dsv41/'+name] = original

    def test_wrong_entrypoint_is_rejected_even_with_a_valid_signature(self):
        self.manifest['entrypoint'] = 'strata.exe'
        self.manifest['files'][0]['path'] = 'strata.exe'
        with self.assertRaisesRegex(ValueError, 'entrypoint'):
            self.audit()

    def test_forged_signature_and_untrusted_key_are_rejected(self):
        receipt = self.receipt()
        receipt['manifest']['files'][0]['sha256'] = 'd'*64
        with self.assertRaises(signing.SignatureError):
            self.audit(receipt)
        self.trusted = {}
        with self.assertRaises(signing.SignatureError):
            self.audit()

    def test_different_component_receipt_is_rejected(self):
        receipt = self.receipt()
        receipt['archive']['sha256'] = 'e'*64
        with self.assertRaisesRegex(ValueError, 'pinned component'):
            self.audit(receipt)


if __name__ == '__main__':
    unittest.main()
