"""The strict gate runs platform cases on the right host without forgiving skipped tests."""
import unittest
from tools import run_release_checks as checks


class HostRouting(unittest.TestCase):
    def suite(self):
        cases=[]
        for identity in (*checks.POSIX_CASES, 'serve.test_video.RegularCase.test_upload'):
            case=unittest.FunctionTestCase(lambda: None)
            case.id=lambda identity=identity: identity
            cases.append(case)
        return unittest.TestSuite([unittest.TestSuite(cases[:2]),unittest.TestSuite(cases[2:])])

    def test_windows_routes_only_declared_posix_cases(self):
        suite,routed=checks.applicable_suite(self.suite(),windows=True)
        self.assertEqual(routed,list(checks.POSIX_CASES))
        self.assertEqual(suite.countTestCases(),1)
        self.assertEqual(next(iter(suite)).id(),'serve.test_video.RegularCase.test_upload')

    def test_posix_keeps_all_cases(self):
        suite,routed=checks.applicable_suite(self.suite(),windows=False)
        self.assertEqual(routed,[])
        self.assertEqual(suite.countTestCases(),len(checks.POSIX_CASES)+1)
