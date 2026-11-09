"""Conservative target matching against offline Salesforce-shaped report exports."""
import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

ALIASES = {
    'targets': {'target_id': ['Target ID', 'Partner ID'], 'company': ['Company', 'Company Name', 'Organization'],
                'territory': ['Territory', 'Region'], 'contact': ['Contact Email', 'Email'], 'segment': ['Segment', 'Industry']},
    'accounts': {'id': ['Account ID'], 'name': ['Account Name'], 'territory': ['Territory', 'Billing State/Province'],
                 'sales': ['Account Owner', 'Account Owner: Full Name'], 'architect': ['Solution Architect'],
                 'last_activity': ['Last Activity', 'Last Activity Date']},
    'contacts': {'id': ['Contact ID'], 'account_id': ['Account ID'], 'email': ['Email', 'Contact Email']},
    'opportunities': {'id': ['Opportunity ID'], 'account_id': ['Account ID'], 'name': ['Opportunity Name'],
                      'created': ['Created Date'], 'amount': ['Amount'], 'stage': ['Stage']},
    'territories': {'territory': ['Territory', 'Region'], 'sales': ['Sales Owner'], 'architect': ['Solution Architect']},
}
REQUIRED = {kind: set(fields) for kind, fields in ALIASES.items()}


def key(value):
    value = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().casefold()
    words = re.findall(r'[a-z0-9]+', value.replace('&', ' and '))
    while words and words[-1] in {'inc', 'incorporated', 'llc', 'ltd', 'limited', 'corp', 'corporation', 'co', 'company'}:
        words.pop()
    return ' '.join(words)


def folded(value):
    return (value or '').strip().casefold()


def read_rows(path, kind=None, columns=None, sheet=None, header_row=1):
    """Map canonical field -> source header. Titles and sheets are explicit, never guessed."""
    if header_row < 1:
        raise ValueError('header_row must be at least 1')
    path = Path(path)
    if path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet and sheet not in wb.sheetnames:
                raise ValueError('Unknown worksheet: ' + sheet)
            ws = wb[sheet] if sheet else wb.active
            raw = list(ws.iter_rows(values_only=True))
        finally:
            wb.close()
    elif path.suffix.lower() == '.csv':
        with path.open(newline='', encoding='utf-8-sig') as f:
            raw = list(csv.reader(f))
    else:
        raise ValueError('Use a .csv or .xlsx report export')
    if len(raw) < header_row:
        raise ValueError('Missing header row')
    headers = [str(v).strip() if v is not None else '' for v in raw[header_row - 1]]
    named = [folded(h) for h in headers if h]
    if len(named) != len(set(named)):
        raise ValueError('Duplicate column headers')
    mapping = {}
    for field, aliases in ALIASES.get(kind, {}).items():
        names = [columns[field]] if columns and field in columns else [field, *aliases]
        indices = [i for i, h in enumerate(headers) if folded(h) in {folded(n) for n in names}]
        if len(indices) > 1:
            raise ValueError('Ambiguous columns for ' + field)
        if indices:
            mapping[field] = indices[0]
    if kind and not REQUIRED[kind].issubset(mapping):
        raise ValueError('Missing columns: ' + ', '.join(sorted(REQUIRED[kind] - mapping.keys())))
    if not kind:
        mapping = {h: i for i, h in enumerate(headers) if h}

    def cell(value):
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value).strip() if value is not None else ''

    rows = []
    for line, values in enumerate(raw[header_row:], header_row + 1):
        if not any(v is not None and str(v).strip() for v in values):
            continue
        if any(v is not None and str(v).strip() for v in values[len(headers):]):
            raise ValueError(f'Extra cells at line {line}')
        row = {f: cell(values[i]) if i < len(values) else '' for f, i in mapping.items()}
        row['_source'] = f'{path.name}:{line}'
        rows.append(row)
    return rows


def audit(targets, accounts, contacts, opportunities, territories, today, stale_days):
    if stale_days < 0:
        raise ValueError('stale days must be nonnegative')
    counts = Counter(key(t.get('company')) for t in targets)
    account_ids = Counter(a['id'] for a in accounts)
    result = []
    for target in targets:
        name = key(target.get('company'))
        if not name:
            raise ValueError('target company is required')
        exact = [a for a in accounts if key(a.get('name')) == name]
        scored = sorted(((SequenceMatcher(None, name, key(a.get('name'))).ratio(), a)
                         for a in accounts if key(a.get('name'))), key=lambda p: (-p[0], p[1]['id']))
        near = [(score, a) for score, a in scored if score >= .80]
        if len(exact) == 1:
            match, candidates = 'exact', exact
        elif len(exact) > 1:
            match, candidates = 'review', exact
        elif len(near) == 1 and near[0][0] >= .92:
            match, candidates = 'fuzzy', [near[0][1]]
        elif near:
            match, candidates = 'review', [a for _, a in near]
        else:
            match, candidates = 'unmatched', []
        flags = []
        if counts[name] > 1:
            match = 'review'
            flags.append('duplicate target')
        ids = [a['id'] for a in candidates]
        if any(account_ids[ident] > 1 for ident in ids):
            match = 'review'
            flags.append('duplicate account ID')
        row = {'target': target, 'match': match, 'candidates': sorted(set(ids)), 'flags': flags,
               'scores': {a['id']: round(score, 4) for score, a in scored if a['id'] in ids}}
        territory = folded(target.get('territory'))
        assignments = [t for t in territories if folded(t.get('territory')) == territory and territory]
        if len(assignments) == 1:
            row['sales'] = assignments[0].get('sales', '')
            row['architect'] = assignments[0].get('architect', '')
        if len(assignments) != 1 or not row.get('sales') or not row.get('architect'):
            flags.append('unassigned')
        if match in ('exact', 'fuzzy'):
            account = candidates[0]
            row['account_id'] = account['id']
            if folded(account.get('territory')) != territory:
                flags.append('territory conflict')
            for field in ('sales', 'architect'):
                if not account.get(field):
                    flags.append('CRM ' + field + ' missing')
                elif folded(account.get(field)) != folded(row.get(field)):
                    flags.append(field + ' conflict')
            email = folded(target.get('contact'))
            if not email:
                flags.append('target contact missing')
            found = [c for c in contacts if folded(c.get('email')) == email and email]
            if email and not any(c.get('account_id') == account['id'] for c in found):
                flags.append('contact missing')
            if any(c.get('account_id') != account['id'] for c in found):
                flags.append('contact conflict')
            if len(found) > 1:
                flags.append('duplicate contact')
            activity = account.get('last_activity', '').strip()
            try:
                activity_date = date.fromisoformat(activity) if activity else None
                if activity_date and activity_date > today:
                    flags.append('future activity')
                elif not activity_date or (today - activity_date).days > stale_days:
                    flags.append('stalled')
            except ValueError:
                flags.append('invalid activity date')
            row['opportunities'] = [o for o in opportunities if o.get('account_id') == account['id']]
        elif match == 'unmatched':
            flags.append('stalled')
        else:
            flags.append('activity unverified')
        result.append(row)
    links = Counter(r.get('account_id') for r in result if r.get('account_id'))
    for row in result:
        if links[row.get('account_id')] > 1:
            row['match'] = 'review'
            row['flags'].append('account linked to multiple targets')
            row.pop('account_id', None)
            row.pop('opportunities', None)
    return result


def main(argv=None):
    from .target_book import TargetBook
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import')
    imp.add_argument('--targets', action='append', required=True)
    for name in ('accounts', 'contacts', 'opportunities', 'territories'):
        imp.add_argument('--' + name, required=True)
    imp.add_argument('--layout', help='JSON keyed by input filename: columns, sheet, header_row')
    imp.add_argument('--today', default=date.today().isoformat())
    imp.add_argument('--stale-days', type=int, default=30)
    sub.add_parser('list')
    sub.add_parser('review')
    resolve = sub.add_parser('resolve')
    resolve.add_argument('target_id')
    resolve.add_argument('--account', required=True)
    resolve.add_argument('--note', required=True)
    status = sub.add_parser('status')
    status.add_argument('target_id')
    status.add_argument('stage', choices=TargetBook.STAGES)
    status.add_argument('--on', default=date.today().isoformat())
    status.add_argument('--note', required=True)
    history = sub.add_parser('history')
    history.add_argument('target_id')
    report = sub.add_parser('report')
    report.add_argument('--start', required=True)
    report.add_argument('--end', required=True)
    report.add_argument('--goal', type=int, required=True)
    report.add_argument('--output', required=True)
    args = p.parse_args(argv)
    try:
        with TargetBook(args.db) as book:
            if args.command == 'import':
                layouts = json.loads(Path(args.layout).read_text()) if args.layout else {}
                def read(path, kind):
                    return read_rows(path, kind, **layouts.get(Path(path).name, {}))
                targets = [r for path in args.targets for r in read(path, 'targets')]
                sources = [read(getattr(args, n), n) for n in ('accounts', 'contacts', 'opportunities', 'territories')]
                result = book.import_reports(targets, *sources, date.fromisoformat(args.today), args.stale_days)
            elif args.command in ('list', 'review'):
                result = book.rows()
                if args.command == 'review':
                    result = [r for r in result if r['match'] == 'review']
            elif args.command == 'resolve':
                result = book.resolve(args.target_id, args.account, args.note)
            elif args.command == 'status':
                result = book.advance(args.target_id, args.stage, args.on, args.note)
            elif args.command == 'history':
                result = {'activities': book.history(args.target_id), 'reviews': book.review_history(args.target_id)}
            else:
                from .weekly_report import export
                export(book, args.output, args.start, args.end, args.goal)
                result = {'workbook': args.output}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
