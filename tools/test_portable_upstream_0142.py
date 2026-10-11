"""Portable delivery compatibility with the upstream 0.1.42 model catalog."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable
from tools import test_portable_delivery_round7 as delivery_fixtures


class UpstreamCatalogCompatibility(unittest.TestCase):
    def test_default_catalog_families_import_offline_and_reject_other_family(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model, _ = delivery_fixtures.ImportedShardContract().fixture(root)
            self.assertNotIn('families', portable.upstream.MODELS['IQ3_S'])
            for family in ('qwen', 'swift', 'coder'):
                with self.subTest(family=family), mock.patch('urllib.request.urlopen',
                                                          side_effect=AssertionError('unexpected download')):
                    model['family'] = family
                    portable.save_json(root/'portable-model.json', model)
                    if family == 'coder':
                        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
                            portable.model_delivery(root)
                    else:
                        self.assertEqual(portable.model_delivery(root)['family'], family)

    def test_explicit_catalog_family_still_rejects_an_incompatible_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model, _ = delivery_fixtures.ImportedShardContract().fixture(root)
            model.update(model='IQ1_M', family='qwen')
            portable.save_json(root/'portable-model.json', model)
            with self.assertRaisesRegex(RuntimeError, 'does not belong'):
                portable.model_delivery(root)


if __name__ == '__main__':
    unittest.main()
