"""Keep local planning files out of every branch sent to GitHub."""
import argparse
from pathlib import Path, PurePosixPath
import subprocess


def is_private_path(name):
    return any(part.casefold() == 'roadmap.md' for part in PurePosixPath(name).parts)


def private_paths(root, ref=None):
    args = ['git', 'ls-tree', '-r', '--name-only', '-z', ref] if ref else ['git', 'ls-files', '-z']
    names = subprocess.check_output(args, cwd=root).decode('utf-8').split('\0')
    return [name for name in names if name and is_private_path(name)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Check this commit/branch instead of the current index')
    args = parser.parse_args()
    forbidden = private_paths(Path(__file__).resolve().parents[1], args.ref)
    if forbidden:
        parser.exit(1, 'Private planning files must not be tracked or pushed: '+', '.join(forbidden)+'\n')
    print('Private planning files are excluded from Git.', flush=True)


if __name__ == '__main__': main()
