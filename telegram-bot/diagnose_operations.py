"""Read-only diagnostics. Bot must be stopped; Google is strictly opt-in."""
import argparse
import json
from pathlib import Path
from operation_diagnostics import list_unresolved, diagnose
from runtime_guard import runtime_guard, OWNER_REF

ROOT = Path(__file__).resolve().parents[1]


def load_config():
    config = {}
    for line in (ROOT/'.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, separator, value = line.partition('=')
            if separator:
                config[key.strip()] = value.strip()
    if not config.get('POSTGRES_PASSWORD'):
        raise ValueError('Configuration unavailable')
    return config


def positive_id(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('ID positivo richiesto')
    return number


def bounded_limit(value):
    number = int(value)
    if not 1 <= number <= 200:
        raise argparse.ArgumentTypeError('Limite tra 1 e 200')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    listing = commands.add_parser('list',help='Elenca PENDING/UNKNOWN, senza contattare Google')
    listing.add_argument('--limit',type=bounded_limit,default=50)
    detail = commands.add_parser('show',help='Mostra dati persistiti ed evidenze')
    detail.add_argument('kind',choices=('creation','reschedule','deletion'))
    detail.add_argument('id',type=positive_id)
    detail.add_argument('--check-google',action='store_true')
    args = parser.parse_args(argv)
    try:
        with runtime_guard():
            config = load_config()
            if args.command == 'list':
                result = {'limit':args.limit,'operations':list_unresolved(config['POSTGRES_PASSWORD'],args.limit)}
            else:
                result = diagnose(config['POSTGRES_PASSWORD'],args.kind,args.id,
                    config.get('GOOGLE_TEST_CALENDAR_ID'),OWNER_REF,args.check_google)
            # JSON escapes control characters. No automatic output files or payload logs.
            print(json.dumps(result,ensure_ascii=False,default=str,indent=2))
            return 1 if 'error' in result or 'reread_error' in result else 0
    except Exception:
        # Never expose exception strings, SQL parameters, DSNs or token contents.
        print('Diagnostica non eseguita: verificare bot fermo, lock, configurazione e PostgreSQL.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
