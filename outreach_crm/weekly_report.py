"""Weekly Excel coverage report with native OOXML pivots and editable lookups."""
from collections import defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.pivot.cache import CacheDefinition, CacheField, CacheSource, SharedItems, WorksheetSource
from openpyxl.pivot.fields import Index, Number, Text
from openpyxl.pivot.record import Record, RecordList
from openpyxl.pivot.table import DataField, FieldItem, Location, PivotField, RowColField, RowColItem, TableDefinition

from .target_audit import folded


def append_literal(sheet, values):
    """Exported company names and notes are text, including names starting with '='."""
    sheet.append(values)
    for cell, value in zip(sheet[sheet.max_row], values):
        if isinstance(value, str):
            cell.data_type = 's'


def pivot(wb, source, rows, destination, start, group, measures, name):
    """Build a real worksheet-backed pivot cache, records, definition and saved view.

    openpyxl's low-level OOXML classes are isolated here because its high-level
    worksheet API does not create PivotTables. CI refreshes these in LibreOffice.
    """
    headers = [c.value for c in wb[source][1]]
    strings = {i: sorted({str(r[i]) for r in rows}) for i in range(len(headers))
               if i not in measures}
    fields = [CacheField(name=h, sharedItems=SharedItems(
        _fields=[Text(v=v) for v in strings[i]], containsString=True, containsNumber=False))
        if i in strings else CacheField(name=h, sharedItems=SharedItems(containsNumber=True, containsString=False))
        for i, h in enumerate(headers)]
    cache = CacheDefinition(cacheSource=CacheSource(type='worksheet', worksheetSource=WorksheetSource(
        sheet=source, ref=f'A1:{wb[source].cell(1,len(headers)).column_letter}{len(rows)+1}')),
        cacheFields=fields, recordCount=len(rows), refreshOnLoad=True, enableRefresh=True, saveData=True,
        createdVersion=6, refreshedVersion=6, minRefreshableVersion=3, id='rId1')
    cache.records = RecordList(r=[Record(_fields=[Index(v=strings[i].index(str(v))) if i in strings
                                                else Number(v=float(v)) for i, v in enumerate(row)]) for row in rows])
    groups = strings[group]
    totals = defaultdict(lambda: [0] * len(measures))
    for row in rows:
        for j, measure in enumerate(measures):
            totals[str(row[group])][j] += row[measure]
    dest = wb[destination]
    dest.cell(start, 2, 'Values')
    for j, value in enumerate([headers[group], *[headers[m]+' total' for m in measures]], 1):
        dest.cell(start+1, j, value)
    for offset, value in enumerate(groups, 2):
        for j, v in enumerate([value, *totals[value]], 1):
            cell = dest.cell(start+offset, j, v)
            if isinstance(v, str):
                cell.data_type = 's'
    for j, value in enumerate(['Grand Total', *[sum(r[m] for r in rows) for m in measures]], 1):
        dest.cell(start+len(groups)+2, j, value)
    pfields = [PivotField(axis='axisRow', defaultSubtotal=False,
                         items=[FieldItem(x=i) for i in range(len(groups))]) if i == group
               else PivotField(dataField=i in measures, defaultSubtotal=False) for i in range(len(headers))]
    previous = [p for ws in wb for p in ws._pivots]
    shared = next((p for p in previous if p.cache == cache), None)
    cache_id = shared.cacheId if shared else len({p.cacheId for p in previous}) + 1
    if shared:
        cache = shared.cache
    # openpyxl hashes cache definitions when deduplicating them, then writes
    # records relationships before assigning the record part ID. Preseed both
    # identities so hashing stays stable and each relationship reaches its cache.
    cache.records._id = cache_id
    table = TableDefinition(name=name, cacheId=cache_id, dataCaption='Values',
        location=Location(ref=f'A{start}:{dest.cell(1,len(measures)+1).column_letter}{start+len(groups)+2}',
                          firstHeaderRow=1, firstDataRow=2, firstDataCol=1),
        pivotFields=pfields, rowFields=[RowColField(x=group)],
        rowItems=[RowColItem(x=[Index(v=i)]) for i in range(len(groups))] + [RowColItem(t='grand', x=[Index(v=0)])],
        colFields=[RowColField(x=-2)],
        colItems=[RowColItem(i=i, x=[Index(v=i)]) for i in range(len(measures))],
        dataFields=[DataField(name=headers[m]+' total', fld=m, subtotal='sum') for m in measures],
        rowGrandTotals=True, colGrandTotals=True, compact=False, compactData=False, gridDropZones=False)
    table.cache = cache
    dest.add_pivot(table)
    return start + len(groups) + 5


def lookup(row, output, end, modern=False, key_col='D'):
    keys = f"Reference!$A$2:$A${end}"
    values = f"Reference!${output}$2:${output}${end}"
    expression = (f'_xlfn.XLOOKUP({key_col}{row},{keys},{values},"MISSING",0)' if modern
                  else f'INDEX({values},MATCH({key_col}{row},{keys},0))')
    return (f'=IF(COUNTIF({keys},{key_col}{row})=0,"MISSING",IF(COUNTIF({keys},{key_col}{row})>1,"DUPLICATE",'
            f'IF({expression}="","MISSING",{expression})))')


def export(book, output, start, end, goal):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or goal <= 0:
        raise ValueError('Use an ordered reporting window and a positive funnel-add goal')
    rows = book.rows(end)
    # All snapshot targets are included; dates bound funnel adds and stage history.
    territories = book.snapshot()['territories']
    refs = [[folded(t['territory']), t['territory'], t.get('sales') or 'MISSING', t.get('architect') or 'MISSING'] for t in territories]
    if not refs:
        refs = [['_no_territories_', 'MISSING', 'MISSING', 'MISSING']]
    ref_map = defaultdict(list)
    for r in refs:
        ref_map[r[0]].append(r)
    wb = Workbook()
    summary = wb.active
    summary.title = 'Weekly'
    targets = wb.create_sheet('Targets')
    funnel = wb.create_sheet('Funnel')
    reference = wb.create_sheet('Reference')
    append_literal(reference, ['Territory key', 'Territory', 'Sales', 'Architect'])
    for r in refs:
        append_literal(reference, r)
    append_literal(targets, ['Target ID', 'Company', 'Segment', 'Territory key', 'Territory', 'Sales', 'Architect',
                             'Match', 'Status', 'Covered', 'Engaged', 'Funnel adds', 'Funnel amount', 'Flags'])
    append_literal(funnel, ['Opportunity ID', 'Account ID', 'Opportunity', 'Created', 'Amount', 'Stage',
                           'Target ID', 'Segment', 'Sales', 'Weekly adds', 'Weekly amount',
                           'Territory key', 'Territory', 'Architect'])
    linked = defaultdict(list)
    for r in rows:
        if r.get('account_id'):
            linked[r['account_id']].append(r)
    adds = defaultdict(list)
    fdata = []
    for o in book.funnel():
        choices = linked[o['account_id']]
        target = choices[0] if len(choices) == 1 else None
        tid = target['target']['target_id'] if target else 'UNLINKED'
        segment = target['target'].get('segment') or 'Unspecified' if target else 'UNLINKED'
        territory_key = folded(target['target']['territory']) if target else '__unlinked__'
        assignments = ref_map[territory_key]
        assignment = assignments[0][1:] if len(assignments) == 1 else ['MISSING' if not assignments else 'DUPLICATE'] * 3
        territory, sales, architect = assignment
        in_week = int(first <= date.fromisoformat(o['created']) <= last)
        if in_week and target:
            adds[tid].append(o)
        data = [o['id'], o['account_id'], o['name'], o['created'], o['amount'], o['stage'],
                tid, segment, sales, in_week, o['amount'] * in_week, territory_key, territory, architect]
        fdata.append(data)
        append_literal(funnel, data)
        n = len(fdata) + 1
        for col, refcol, modern in [('I', 'C', True), ('M', 'B', False), ('N', 'D', False)]:
            funnel[f'{col}{n}'] = lookup(n, refcol, len(refs)+1, modern, 'L')
    # A zero-value placeholder preserves a refreshable empty-funnel schema.
    if not fdata:
        fdata = [['No opportunities', '', '', '', 0, '', 'UNLINKED', 'UNLINKED', 'MISSING', 0, 0, '__unlinked__', 'MISSING', 'MISSING']]
        append_literal(funnel, fdata[0])
    tdata = []
    for rownum, r in enumerate(rows, 2):
        t = r['target']
        values = ref_map[folded(t['territory'])]
        assignment = values[0][1:] if len(values) == 1 else ['MISSING' if not values else 'DUPLICATE'] * 3
        data = [t['target_id'], t['company'], t.get('segment') or 'Unspecified', folded(t['territory']), *assignment,
                r['match'], r['status'], int(bool(r.get('account_id')) and 'unassigned' not in r['flags']),
                int(book.STAGES.index(r['status']) >= book.STAGES.index('engaged')),
                len(adds[t['target_id']]), sum(o['amount'] for o in adds[t['target_id']]), ', '.join(r['flags'])]
        tdata.append(data)
        append_literal(targets, data)
        for col, refcol, modern in [('E', 'B', False), ('F', 'C', True), ('G', 'D', False)]:
            targets[f'{col}{rownum}'] = lookup(rownum, refcol, len(refs)+1, modern)
    summary['A2'] = 'Weekly target coverage'
    summary['A3'] = f'{start} to {end}. Coverage uses the current imported target list.'
    summary.append([])
    for cell, value in {'A5': 'Targets', 'B5': f'=COUNTA(Targets!A2:A{len(rows)+1})',
                        'A6': 'Covered', 'B6': f'=SUM(Targets!J2:J{len(rows)+1})',
                        'A7': 'Engaged', 'B7': f'=SUM(Targets!K2:K{len(rows)+1})',
                        'A8': 'Funnel adds', 'B8': f'=SUM(Funnel!J2:J{len(fdata)+1})',
                        'C8': 'Goal', 'D8': goal, 'E8': '=IF(D8>0,B8/D8,"SET POSITIVE GOAL")',
                        'A9': 'Unlinked funnel adds', 'B9': f'=SUMIF(Funnel!G2:G{len(fdata)+1},"UNLINKED",Funnel!J2:J{len(fdata)+1})',
                        'A10': 'Refresh', 'B10': 'Data > Refresh All after edits. Regenerate to include new rows.'}.items():
        summary[cell] = value
    summary['E8'].number_format = '0.0%'
    summary['D8'].fill = PatternFill('solid', fgColor='FFF2CC')
    validation = DataValidation(type='whole', operator='greaterThan', formula1=0)
    validation.showErrorMessage = True
    validation.error = 'Enter a positive whole-number funnel-add goal.'
    summary.add_data_validation(validation)
    validation.add(summary['D8'])
    start2 = pivot(wb, 'Targets', tdata, 'Weekly', 13, 2, [9, 10, 11, 12], 'CoverageBySegment')
    start3 = pivot(wb, 'Targets', tdata, 'Weekly', start2, 5, [9, 10, 11, 12], 'CoverageByOwner')
    pivot(wb, 'Funnel', fdata, 'Weekly', start3, 8, [9, 10], 'FunnelByOwner')
    chart = BarChart()
    chart.title = 'Covered and engaged targets by segment'
    groups = len({r[2] for r in tdata})
    chart.add_data(Reference(summary, min_col=2, max_col=3, min_row=14, max_row=14+groups), titles_from_data=True)
    chart.set_categories(Reference(summary, min_col=1, min_row=15, max_row=14+groups))
    for series, title in zip(chart.series, ('Covered', 'Engaged')):
        series.tx = SeriesLabel(v=title)
    chart.y_axis.title = 'Targets'
    summary.add_chart(chart, 'G12')
    for ws in wb:
        ws.sheet_view.showGridLines = False
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 21
        for row in ws:
            for c in row:
                c.font = Font(name='Arial', size=10, color='243247')
                c.alignment = Alignment(vertical='center')
        if ws != summary:
            ws.freeze_panes = 'C2'
            for c in ws[1]:
                c.fill = PatternFill('solid', fgColor='243247')
                c.font = Font(name='Arial', size=10, color='FFFFFF', bold=True)
                c.alignment = Alignment(wrap_text=True, horizontal='center', vertical='center')
            ws.row_dimensions[1].height = 32
            table = Table(displayName=ws.title+'Data', ref=ws.dimensions)
            table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
            ws.add_table(table)
    summary.column_dimensions['A'].width = 29
    for p in summary._pivots:
        from openpyxl.utils.cell import range_boundaries
        left, top, right, bottom = range_boundaries(p.location.ref)
        for cells in summary.iter_rows(min_row=top+1, max_row=top+1, min_col=left, max_col=right):
            for c in cells:
                c.fill = PatternFill('solid', fgColor='243247')
                c.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
                c.alignment = Alignment(wrap_text=True, horizontal='center', vertical='center')
        summary.row_dimensions[top+1].height = 30
        for cells in summary.iter_rows(min_row=bottom, max_row=bottom, min_col=left, max_col=right):
            for c in cells:
                c.font = Font(name='Arial', size=10, bold=True, color='243247')
    summary['A2'].font = Font(name='Arial', size=16, bold=True, color='243247')
    targets.column_dimensions['B'].width = 33
    targets.column_dimensions['N'].width = 55
    for row in targets.iter_rows(min_row=2):
        row[13].alignment = Alignment(wrap_text=True, vertical='center')
        targets.row_dimensions[row[0].row].height = 32
    for col in ('E', 'F', 'G'):
        for text in ('MISSING', 'DUPLICATE'):
            targets.conditional_formatting.add(f'{col}2:{col}{len(rows)+1}', CellIsRule(
                operator='equal', formula=['"'+text+'"'], fill=PatternFill('solid', fgColor='FCE4D6')))
    for row in funnel.iter_rows(min_row=2):
        if row[3].value:
            row[3].value = date.fromisoformat(row[3].value)
            row[3].number_format = 'yyyy-mm-dd'
        row[4].number_format = '#,##0.00'
        row[10].number_format = '#,##0.00'
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)
