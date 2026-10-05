"""Создать пакет выпуска или проверить восстановление в новой папке."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.release import build_release,restore_release


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build')
    build.add_argument('--output',type=Path,required=True)
    restore=sub.add_parser('restore')
    restore.add_argument('--package',type=Path,required=True)
    restore.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='build':
        result=build_release(Path(__file__).resolve().parents[1],args.output)
        print(json.dumps(dict(files=len(result['files']),output=str(args.output))))
    else:
        print(json.dumps(restore_release(args.package,args.destination)))


if __name__=='__main__':
    main()
