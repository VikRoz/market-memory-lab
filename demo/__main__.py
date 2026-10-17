"""One current example: replay offline or explicitly verify against a fresh DB."""
import argparse
import json
from . import prepare, replay


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('replay', 'prepare', 'verify', 'stop'))
    p.add_argument('--output')
    p.add_argument('--confirm-stop-own-db', action='store_true')
    args = p.parse_args()
    if args.command == 'replay':
        replay.display(replay.verify())
        return 0
    if not args.output:
        p.error('--output is required')
    if args.command == 'stop':
        from .stop import stop
        result = stop(args.output, args.confirm_stop_own_db)
    elif args.command == 'prepare':
        result = prepare.prepare(args.output)
    else:
        result = prepare.verify_prepared(args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
