"""Campaign cohort reporting from a manually maintained, anonymous send ledger."""
import argparse
import csv
import math
from datetime import date
from pathlib import Path

METRICS = ('open', 'click', 'reply', 'conversion')
BENCHMARKS = {'open': .40, 'click': .10, 'reply': .15, 'conversion': .05}


def interval(successes, total):
    """Two-sided 95% Wilson score interval for a binomial proportion."""
    if not total:
        return None, None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z*z/total
    centre = (p + z*z/(2*total))/denominator
    margin = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total))/denominator
    return max(0, centre-margin), min(1, centre+margin)


def read_ledger(path):
    rows, ids = [], set()
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'send_id', 'sent_date', 'template', 'segment', *METRICS}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('Ledger needs columns: ' + ', '.join(sorted(required)))
        for line, row in enumerate(reader, 2):
            try:
                for field in ('send_id', 'template', 'segment'):
                    row[field] = row[field].strip()
                    if not row[field]:
                        raise ValueError(field + ' is empty')
                if row['send_id'] in ids:
                    raise ValueError('duplicate send_id')
                ids.add(row['send_id'])
                row['sent_date'] = date.fromisoformat(row['sent_date'])
                for metric in METRICS:
                    if row[metric] not in ('0', '1'):
                        raise ValueError(metric + ' must be 0 or 1')
                    row[metric] = int(row[metric])
                rows.append(row)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f'Line {line}: {exc}') from exc
    return rows


def aggregate(rows):
    n = len(rows)
    counts = {m: sum(r[m] for r in rows) for m in METRICS}
    return {'sends': n, 'counts': counts,
            'rates': {m: counts[m]/n if n else None for m in METRICS},
            'intervals': {m: interval(counts[m], n) for m in METRICS}}


def summarize(rows, start, end, previous_start, previous_end):
    start, end, previous_start, previous_end = map(date.fromisoformat, (start, end, previous_start, previous_end))
    if previous_start > previous_end or start > end or previous_end >= start:
        raise ValueError('Periods must be valid, ordered and non-overlapping.')
    current = [r for r in rows if start <= r['sent_date'] <= end]
    previous = [r for r in rows if previous_start <= r['sent_date'] <= previous_end]
    keys = sorted({(r['template'], r['segment']) for r in current + previous})
    groups = [{'template': t, 'segment': s,
               'current': aggregate([r for r in current if (r['template'], r['segment']) == (t, s)]),
               'previous': aggregate([r for r in previous if (r['template'], r['segment']) == (t, s)])} for t, s in keys]
    return {'current': aggregate(current), 'previous': aggregate(previous), 'groups': groups,
            'periods': (str(previous_start), str(previous_end), str(start), str(end))}


def explanation(report):
    a, b = report['previous'], report['current']
    if not a['sends'] or not b['sends']:
        return 'A period has no sends, so there is not enough data to compare rates.'
    changes = {m: b['rates'][m] - a['rates'][m] for m in METRICS}
    metric = max(METRICS, key=lambda m: abs(changes[m]))
    direction = 'rose' if changes[metric] > 0 else 'fell' if changes[metric] < 0 else 'was unchanged'
    return (f"{metric.title()} rate {direction} by {abs(changes[metric])*100:.1f} percentage points. "
            'Changes in template or audience mix and small samples may contribute; these results do not establish a cause.')


def export(report, output, benchmarks=BENCHMARKS):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Summary'
    ws.append(['Metric', 'Previous rate', 'Current rate', 'Benchmark', 'Change (percentage points)', '95% low', '95% high', 'Previous sends', 'Current sends'])
    for m in METRICS:
        old, new = report['previous']['rates'][m], report['current']['rates'][m]
        low, high = report['current']['intervals'][m]
        ws.append([m.title(), old, new, benchmarks[m], (new-old)*100 if old is not None and new is not None else None, low, high, report['previous']['sends'], report['current']['sends']])
    for row in ws.iter_rows(min_row=2, max_row=5):
        for col in (1, 2, 3, 5, 6):
            row[col].number_format = '0.0%'
    chart = BarChart()
    chart.title = 'Rates by send cohort'
    chart.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=5), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
    ws.add_chart(chart, 'A9')
    ws['A26'] = explanation(report)
    ws['A27'] = '95% Wilson intervals describe sampling uncertainty; all rates use sends as denominator.'
    ws['A28'] = 'Periods: ' + ' to '.join(report['periods'][:2]) + ' vs ' + ' to '.join(report['periods'][2:])
    details = wb.create_sheet('Template and segment')
    details.append(['Template', 'Segment', 'Period', 'Sends', 'Metric', 'Count', 'Rate', '95% low', '95% high', 'Benchmark'])
    for group in report['groups']:
        for period in ('previous', 'current'):
            data = group[period]
            for m in METRICS:
                details.append([group['template'], group['segment'], period, data['sends'], m, data['counts'][m], data['rates'][m], *data['intervals'][m], benchmarks[m]])
    for sheet in wb:
        sheet.freeze_panes = 'A2'
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 22
    wb.save(output / 'campaign-summary.xlsx')
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    title = slide.shapes.add_textbox(Inches(.5), Inches(.2), Inches(9), Inches(.5))
    title.text_frame.text = 'Campaign rates by send cohort'
    chart_data = CategoryChartData()
    chart_data.categories = [m.title() for m in METRICS]
    for name, values in [('Previous', report['previous']['rates']), ('Current', report['current']['rates']), ('Benchmark', benchmarks)]:
        chart_data.add_series(name, [values[m] for m in METRICS])
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(.5), Inches(.9), Inches(8.8), Inches(4.5), chart_data)
    box = slide.shapes.add_textbox(Inches(.5), Inches(5.55), Inches(8.8), Inches(1.5))
    box.text_frame.word_wrap = True
    box.text_frame.text = explanation(report) + f" Sends: {report['previous']['sends']} previous, {report['current']['sends']} current."
    box.text_frame.paragraphs[0].font.size = Pt(16)
    prs.save(output / 'campaign-summary.pptx')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('ledger')
    for flag in ('start', 'end', 'previous-start', 'previous-end'):
        p.add_argument('--' + flag, required=True)
    p.add_argument('--output', required=True)
    for metric in METRICS:
        p.add_argument('--benchmark-' + metric, type=float, default=BENCHMARKS[metric], help='Fraction between 0 and 1')
    args = p.parse_args()
    benchmarks = {m: getattr(args, 'benchmark_' + m) for m in METRICS}
    if any(not 0 <= b <= 1 for b in benchmarks.values()):
        p.error('Benchmarks must be between 0 and 1')
    try:
        export(summarize(read_ledger(args.ledger), args.start, args.end, args.previous_start, args.previous_end), args.output, benchmarks)
    except ValueError as exc:
        p.error(str(exc))
    print('Saved campaign-summary.xlsx and campaign-summary.pptx in', args.output)


if __name__ == '__main__':
    main()
