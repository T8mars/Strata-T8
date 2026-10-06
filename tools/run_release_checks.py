"""Run all CPU regression gates, including newly added upstream test modules.

Application checks need the pinned gguf-py source (bootstrap_portable.py --gguf-only).
Tokenizer checks additionally need an existing pack; they never fetch a model.
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
    parser.add_argument('--group', choices=['application', 'setup', 'tokenizer'], default='application')
    parser.add_argument('--tokenizer', type=Path, help='Existing pack tokenizer for the tokenizer group')
    args = parser.parse_args()
    from check_private_paths import private_paths
    forbidden = private_paths(ROOT)
    if forbidden:
        parser.error('Private planning files in Git index: '+', '.join(forbidden))
    if args.group == 'tokenizer':
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
    print(json.dumps({'group': args.group, 'modules': modules}), flush=True)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(modules))
    print(json.dumps({'group': args.group, 'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'seconds': round(time.monotonic()-started, 3)}), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
