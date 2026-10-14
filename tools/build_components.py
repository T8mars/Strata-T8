"""Create component update assets from a complete, model-free portable build."""
import argparse
from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from component_updates import build

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=build(args.package,args.output)
    print(f"Verified {len(result['components'])} components for {result['version']}; retain the full offline package for initial installation.")

if __name__=='__main__':main()
