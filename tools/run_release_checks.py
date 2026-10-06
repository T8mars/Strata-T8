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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['application', 'setup', 'tokenizer', 'nodes'], default='application')
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
    else:
        modules = ['serve.'+path.stem for path in sorted((ROOT/'serve').glob('test_*.py'))
                   if path.stem != 'test_detok']
        modules += ['tools.'+path.stem for path in sorted((ROOT/'tools').glob('test_*.py'))
                    if not path.stem.startswith('test_setup_')]
    if args.group != 'nodes':
        print(json.dumps({'group': args.group, 'modules': modules, 'strict': args.strict}), flush=True)
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    passed = result.wasSuccessful() and (not args.strict or (result.testsRun > 0 and not result.skipped))
    print(json.dumps({'group': args.group, 'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'seconds': round(time.monotonic()-started, 3),
                      'strict': args.strict, 'passed': passed}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
