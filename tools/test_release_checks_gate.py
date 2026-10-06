"""Exercise the release policy through real unittest discovery and CLI exits."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT/'tools/run_release_checks.py'


class ReleaseCheckGate(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='Strata-release-gate-')
        self.addCleanup(self.temporary.cleanup)
        self.tests = Path(self.temporary.name)/'node tests 中文'
        self.tests.mkdir()

    def write_suite(self, methods):
        (self.tests/'test_policy_fixture.py').write_text(
            'import os\nimport unittest\n\nclass ActualSuite(unittest.TestCase):\n'+methods,
            encoding='utf-8')

    def invoke(self, *, strict=False, args=None, environment=None):
        parameters = ['--group','nodes','--node-tests',str(self.tests)] if args is None else args
        if strict:
            parameters = [*parameters, '--strict']
        result = subprocess.run([sys.executable, '-X','utf8',str(RUNNER),*parameters],
                                cwd=ROOT, env=environment, capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        summary = next((row for row in rows if 'tests' in row), None)
        return result, summary

    def test_strict_pass_reports_actual_discovered_count(self):
        self.write_suite('    def test_first(self): self.assertEqual(2+2,4)\n'
                         '    def test_second(self): self.assertTrue("node".startswith("n"))\n')
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(summary['tests'],2)
        self.assertEqual((summary['failures'],summary['errors'],summary['skipped']),(0,0,0))
        self.assertIs(summary['strict'],True)
        self.assertIs(summary['passed'],True)

    def test_strict_accepts_a_different_nonzero_suite_size(self):
        self.write_suite(''.join(f'    def test_number_{n}(self): self.assertGreater({n}+1,0)\n' for n in range(7)))
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(summary['tests'],7)

    def test_strict_rejects_an_actually_skipped_test(self):
        self.write_suite('    @unittest.skip("dependency deliberately unavailable")\n'
                         '    def test_skipped(self): self.fail("must not execute")\n')
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual((summary['tests'],summary['skipped']),(1,1))
        self.assertIs(summary['passed'],False)

    def test_lightweight_mode_continues_to_allow_explained_skips(self):
        self.write_suite('    @unittest.skip("packaging runtime absent in lightweight CI")\n'
                         '    def test_skipped(self): self.fail("must not execute")\n')
        result, summary = self.invoke()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(summary['skipped'],1)
        self.assertIs(summary['strict'],False)
        self.assertIs(summary['passed'],True)

    def test_real_assertion_failure_blocks_both_modes(self):
        self.write_suite('    def test_failure(self): self.assertEqual("actual","expected")\n')
        for strict in (False,True):
            with self.subTest(strict=strict):
                result, summary = self.invoke(strict=strict)
                self.assertEqual(result.returncode,1,result.stderr)
                self.assertEqual((summary['tests'],summary['failures'],summary['errors']),(1,1,0))
                self.assertIs(summary['passed'],False)

    def test_real_test_error_blocks_both_modes(self):
        self.write_suite('    def test_error(self): raise RuntimeError("actual test error")\n')
        for strict in (False,True):
            with self.subTest(strict=strict):
                result, summary = self.invoke(strict=strict)
                self.assertEqual(result.returncode,1,result.stderr)
                self.assertEqual((summary['tests'],summary['failures'],summary['errors']),(1,0,1))

    def test_strict_empty_test_directory_cannot_report_success(self):
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(summary['tests'],0)
        self.assertIs(summary['passed'],False)

    def test_node_group_requires_an_explicit_test_directory(self):
        result, summary = self.invoke(strict=True,args=['--group','nodes'])
        self.assertEqual(result.returncode,2)
        self.assertIsNone(summary)
        self.assertIn('existing test directory',result.stderr)

    def test_missing_node_directory_is_a_configuration_error(self):
        result, summary = self.invoke(strict=True,args=['--group','nodes','--node-tests',str(self.tests/'missing')])
        self.assertEqual(result.returncode,2)
        self.assertIsNone(summary)
        self.assertIn('existing test directory',result.stderr)

    def test_file_cannot_be_mistaken_for_a_node_test_directory(self):
        path = self.tests/'test_policy_fixture.py'
        path.write_text('import unittest\n',encoding='utf-8')
        result, summary = self.invoke(strict=True,args=['--group','nodes','--node-tests',str(path)])
        self.assertEqual(result.returncode,2)
        self.assertIsNone(summary)

    def test_discovery_import_error_is_not_a_passing_nonempty_suite(self):
        (self.tests/'test_policy_fixture.py').write_text('raise RuntimeError("actual import failure")\n',encoding='utf-8')
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual((summary['tests'],summary['errors']),(1,1))
        self.assertIn('actual import failure',result.stderr)

    def test_node_tests_receive_the_existing_strata_source_environment(self):
        source = str(ROOT.resolve())
        self.write_suite('    def test_source(self):\n'
                         f'        self.assertEqual(os.environ["STRATA_SOURCE_DIR"],{source!r})\n')
        environment = dict(os.environ,STRATA_SOURCE_DIR=source)
        result, summary = self.invoke(strict=True,environment=environment)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(summary['tests'],1)

    def test_node_path_cannot_silently_select_the_setup_group(self):
        result, summary = self.invoke(strict=True,args=['--group','setup','--node-tests',str(self.tests)])
        self.assertEqual(result.returncode,2)
        self.assertIsNone(summary)
        self.assertIn('--node-tests requires --group nodes',result.stderr)

    def test_mixed_suite_completes_and_reports_failure_and_skip(self):
        self.write_suite('    def test_pass(self): self.assertTrue(True)\n'
                         '    def test_failure(self): self.assertTrue(False)\n'
                         '    @unittest.skip("optional condition")\n'
                         '    def test_skip(self): self.fail("must not execute")\n')
        result, summary = self.invoke(strict=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual((summary['tests'],summary['failures'],summary['errors'],summary['skipped']),(3,1,0,1))


if __name__ == '__main__':
    unittest.main()
