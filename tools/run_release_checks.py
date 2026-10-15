"""Run all CPU regression gates, including newly added upstream test modules.

Application checks need the pinned gguf-py source (bootstrap_portable.py --gguf-only).
Tokenizer checks additionally need an existing pack; they never fetch a model.
Formal releases use --strict to reject skipped tests or an empty suite.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]

POSIX_CASES = (
    'serve.test_cache_drop.CacheDrop.test_on',
    'serve.test_server.StartCrashReported.test_sigill_is_reported_not_retried',
    'serve.test_server.StartCrashReported.test_a_silent_exit_is_retried_without_claiming_the_gpu_runtime',
    'serve.test_server.StartCrashReported.test_the_runtimes_own_failure_is_named',
    'tools.test_setup_mac.RepackGuard.test_a_live_lock_is_seen_a_stale_one_is_dropped',
    'tools.test_setup_mac.RepackGuard.test_start_refuses_while_a_repack_runs',
)


def applicable_suite(suite, *, windows):
    """Route the named POSIX cases to their actual host; do not mask any other skip."""
    selected, routed = unittest.TestSuite(), []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            children, omitted = applicable_suite(item, windows=windows)
            selected.addTests(children)
            routed.extend(omitted)
        elif windows and item.id() in POSIX_CASES:
            routed.append(item.id())
        else:
            selected.addTest(item)
    return selected, routed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['application', 'setup', 'tokenizer', 'nodes', 'posix'], default='application')
    parser.add_argument('--tokenizer', type=Path, help='Existing pack tokenizer for the tokenizer group')
    parser.add_argument('--node-tests', type=Path, help='Independent node test directory for the nodes group')
    parser.add_argument('--strict', action='store_true', help='Require executed tests with no failures, errors or skips')
    args = parser.parse_args()
    if args.node_tests is not None and args.group != 'nodes':
        parser.error('--node-tests requires --group nodes')
    from check_private_paths import private_paths
    forbidden = private_paths(ROOT)
    if forbidden:
        parser.error('Private planning files in Git index: '+', '.join(forbidden))
    if args.group == 'nodes':
        if args.node_tests is None or not args.node_tests.is_dir():
            parser.error('nodes checks require --node-tests pointing to an existing test directory')
        directory = args.node_tests.resolve()
        print(json.dumps({'group': args.group, 'node_tests': str(directory), 'strict': args.strict}), flush=True)
        suite = unittest.defaultTestLoader.discover(str(directory))
    elif args.group == 'tokenizer':
        if args.tokenizer is None or not (args.tokenizer/'vocab.json').is_file():
            parser.error('tokenizer checks require --tokenizer pointing to an existing pack tokenizer')
        os.environ['STRATA_TOKENIZER'] = str(args.tokenizer.resolve())
        modules = ['serve.test_detok']
    elif args.group == 'setup':
        modules = ['tools.'+path.stem for path in sorted((ROOT/'tools').glob('test_setup_*.py'))]
    elif args.group == 'posix':
        if os.name == 'nt':
            parser.error('posix checks require a POSIX host')
        modules = ['tools.test_docker_entrypoint', *POSIX_CASES]
    else:
        modules = ['serve.'+path.stem for path in sorted((ROOT/'serve').glob('test_*.py'))
                   if path.stem != 'test_detok']
        modules += ['tools.'+path.stem for path in sorted((ROOT/'tools').glob('test_*.py'))
                    if not path.stem.startswith('test_setup_')
                    and not (os.name == 'nt' and path.stem == 'test_docker_entrypoint')]
    if args.group != 'nodes':
        print(json.dumps({'group': args.group, 'modules': modules, 'strict': args.strict}), flush=True)
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    suite, routed = applicable_suite(suite, windows=os.name == 'nt')
    if routed:
        print(json.dumps({'routed_to_posix_gate': routed}), flush=True)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    passed = result.wasSuccessful() and (not args.strict or (result.testsRun > 0 and not result.skipped))
    print(json.dumps({'group': args.group, 'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'skipped_cases': [{'id': case.id(), 'reason': reason} for case, reason in result.skipped],
                      'seconds': round(time.monotonic()-started, 3),
                      'strict': args.strict, 'passed': passed}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
