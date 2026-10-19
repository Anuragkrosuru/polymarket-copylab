"""Optional Word exporter; run with a Python that provides python-docx."""
import json
import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from copylab.report import dollars, timestamp


def clean(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))


def table(document, headings, rows, widths):
    grid = document.add_table(rows=1, cols=len(headings))
    grid.alignment = WD_TABLE_ALIGNMENT.CENTER
    grid.autofit = False
    for column, width in zip(grid.columns, widths):
        column.width = Inches(width)
    properties = grid._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for side in ('top','left','bottom','right','insideH','insideV'):
        border = OxmlElement('w:'+side)
        for key, value in (('val','single'),('sz','4'),('color','D9D9D9')):
            border.set(qn('w:'+key), value)
        borders.append(border)
    properties.append(borders)
    for i, heading in enumerate(headings):
        grid.rows[0].cells[i].text = heading
    repeat = OxmlElement('w:tblHeader')
    grid.rows[0]._tr.get_or_add_trPr().append(repeat)
    for values in rows:
        cells = grid.add_row().cells
        for cell, value in zip(cells, values):
            cell.text = clean(value)
    for row_index, row in enumerate(grid.rows):
        no_split = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(no_split)
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for side in ('top','left','bottom','right'):
                element = OxmlElement('w:'+side)
                element.set(qn('w:w'), '85')
                element.set(qn('w:type'), 'dxa')
                margins.append(element)
            tcpr.append(margins)
            if row_index == 0:
                shade = OxmlElement('w:shd')
                shade.set(qn('w:fill'), 'EAEAEA')
                tcpr.append(shade)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.08
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    run.font.bold = row_index == 0
    document.add_paragraph().paragraph_format.space_after = Pt(1)
    return grid


def combined_body(document, report, output):
    from copylab.combined_report import price_text
    document.core_properties.title='All Three Paper Bots Trade and Profit Report'
    document.core_properties.author='CopyLab'
    document.add_paragraph('All Three Paper Bots Trade and Profit Report','Title')
    document.add_paragraph('Updated '+report['generated'])
    document.add_paragraph(report['scope'])
    total=report['combined']
    document.add_paragraph('Combined account value is '+dollars(total['equity'])+' with total profit or loss of '+dollars(total['total_pnl'])+'. Realized profit or loss is '+dollars(total['realized_pnl'])+'. All amounts are simulated.')
    table(document,['Measure','Amount'],[(
        label,dollars(total[key])) for label,key in [('Starting balances','initial_cash'),('Available cash','cash'),
        ('Estimated combined equity','equity'),('Total profit or loss','total_pnl'),('Realized profit or loss','realized_pnl'),
        ('Estimated open profit or loss','unrealized_pnl')]], [4.7,2.3])
    document.add_paragraph(report['explanation'])
    document.add_paragraph('Results by bot','Heading 1')
    for bot in report['bots']:
        document.add_paragraph(bot['name'],'Heading 2')
        table(document,['Cash','Estimated equity','Realized profit or loss','Estimated open profit or loss'],
              [(dollars(bot['cash']),dollars(bot['equity']),dollars(bot['realized_pnl']),dollars(bot['unrealized_pnl']))],
              [1.3,1.5,2.0,2.2])
        document.add_paragraph('Entries: %d. Exits or settlements: %d. Last source update %s. %s%s' %
            (bot['entries'],bot['exits'],timestamp(bot['source_updated']),
             'Source status is stale or unavailable. ' if bot['stale'] else '',bot['status']))
        if not bot['entries']:
            document.add_paragraph('No entries have been recorded for this bot.')
    opened=[(b,p) for b in report['bots'] for p in b['positions']]
    if opened:
        document.add_paragraph('Open positions','Heading 1')
        for bot,position in opened:
            document.add_paragraph(bot['name']+' '+clean(position['title']),'Heading 2')
            document.add_paragraph('Outcome '+clean(position['outcome']))
            table(document,['Entry cost','Estimated current value','Estimated profit or loss'],
                  [(dollars(position['cost']),dollars(position['value']),dollars(position['pnl']))],[2.2,2.4,2.4])
            if 'expected_payout' in position:
                document.add_paragraph('Expected settlement payout '+dollars(position['expected_payout'])+'. This is not current equity or realized profit.')
            if position.get('target_proceeds') is not None:
                document.add_paragraph('Five percent net profit target proceeds '+dollars(position['target_proceeds'])+'. A target is not a guaranteed fill.')
    document.add_paragraph('Complete trade history','Heading 1')
    if not report['trades']:
        document.add_paragraph('No trades have been recorded by any bot.')
    for event in report['trades']:
        document.add_paragraph('%d %s %s'%(event['number'],event['bot'],event['type']),'Heading 2')
        paragraph=document.add_paragraph(clean(event['market'])+' | '+clean(event['outcome']))
        paragraph.paragraph_format.keep_with_next=True
        paragraph=document.add_paragraph(event['time']+' | '+event['time_basis'])
        paragraph.paragraph_format.keep_with_next=True
        table(document,['Price','Entry cost basis','Cash change','Realized profit or loss'],
              [(price_text(event),dollars(event['cost_basis']),dollars(event['cash_change']),
                'On exit' if event['realized_pnl'] is None else dollars(event['realized_pnl']))],[1.7,1.7,1.7,1.9])
        detail=''
        if event.get('exit_reason'):detail+=' | Exit: '+event['exit_reason']
        if event.get('net_return_percent') is not None:detail+=' | Net return %.2f%%'%event['net_return_percent']
        if detail:document.add_paragraph(clean(detail))
    output=Path(output);temporary=output.with_name(output.stem+'.tmp.docx')
    for root in (document._element, document.styles.element):
        for border in list(root.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    document.save(temporary);os.replace(temporary,output)


def build(report, output):
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.7)
    section.left_margin = section.right_margin = Inches(.75)
    normal = document.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.12
    for name, size in (('Title',22),('Heading 1',14),('Heading 2',11)):
        style = document.styles[name]
        style.font.name = 'Arial'
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0,0,0)
        style.font.bold = name != 'Title'
        style.paragraph_format.space_before = Pt(12 if name != 'Title' else 0)
        style.paragraph_format.space_after = Pt(7)
    if report.get('bots'):
        combined_body(document,report,output)
        return
    document.core_properties.title = 'Paper Trading Trade and Profit Report'
    document.core_properties.author = 'CopyLab'
    document.add_paragraph('Paper Trading Trade and Profit Report', 'Title')
    document.add_paragraph('Updated '+report['generated'])
    account = report['account']
    if report['trades']:
        document.add_paragraph('The paper account has recorded %d %s, %d copied %s, and %d settled %s. Total profit or loss is %s. This report lists every recorded trade and separates realized gains from estimated open gains.' %
                               (account['fills'],'buy' if account['fills']==1 else 'buys',
                                account['sales'],'sale' if account['sales']==1 else 'sales',
                                account['settled_positions'],'position' if account['settled_positions']==1 else 'positions',
                                dollars(account['total_pnl'])))
    else:
        document.add_paragraph('No qualifying copied trades have been recorded yet. The simulated account remains at $40 with $0 profit or loss. This report will update as the running bot records trades.')
    document.add_paragraph('This is a simulated account. No real money is traded. Synthetic test scenarios are excluded.')
    document.add_paragraph('Account summary','Heading 1')
    table(document, ['Measure','Amount'], [
        ('Starting balance','$40.00'),('Available cash',dollars(account['cash'])),
        ('Estimated account equity',dollars(account['equity'])),('Total profit or loss',dollars(account['total_pnl'])),
        ('Realized profit or loss',dollars(account['realized_pnl'])),
        ('Estimated open profit or loss',dollars(account['unrealized_pnl'])),
        ('Entry and exit fee reserves',dollars(report['fee_reserve_total']))], [4.7,2.3])
    document.add_paragraph('Fees use fixed simulation reserves of 2 percent on entry and exit, not verified exchange charges. Open values use available bids after the exit reserve. Missing or stale quotes make total profit unknown. Realized gains come from copied sales and confirmed paper settlements. Buys have no realized gain on entry; their profit is recorded on exit or settlement.')
    if account['positions']:
        document.add_paragraph('Open positions','Heading 1')
        for position in account['positions']:
            document.add_paragraph(clean(position['title'])+' '+clean(position['outcome']), 'Heading 2')
            table(document, ['Remaining cost','Estimated value','Open profit or loss'],
                  [(dollars(position['cost']),dollars(position['value']),dollars(position['pnl']))],
                  [2.3,2.3,2.4])
    document.add_paragraph('Complete trade history','Heading 1')
    if not report['trades']:
        document.add_paragraph('There are no copied buys, sells, or settlements to list yet. The CSV export will contain every event as activity arrives.')
    for event in report['trades']:
        document.add_paragraph('%d %s %s' % (event['number'],event['type'],clean(event['market'])), 'Heading 2')
        document.add_paragraph(event['time']+' | '+event['time_basis']+' | Outcome '+clean(event['outcome']))
        table(document, ['Price','Fee reserve','Cash change','Realized gain or loss'],
              [('$%.4f'%event['price'],dollars(event['fee_reserve']),
                dollars(event['cash_change']), 'On exit' if event['realized_pnl'] is None else dollars(event['realized_pnl']))],
              [1.5,1.5,1.7,2.3])
        document.add_paragraph('Allocated entry cost '+dollars(event['cost_basis'])+'. Source wallet '+clean(event['wallet'])+'.')
        if event['source_transaction']:
            paragraph = document.add_paragraph('Source transaction '+clean(event['source_transaction']))
            for run in paragraph.runs:
                run.font.size = Pt(8)
    screening = report.get('screening')
    if screening:
        document.add_paragraph('Trader selection','Heading 1')
        policy = screening['policy']
        if screening.get('stale'):
            document.add_paragraph('This screening is stale. New screened entries are paused; the list below shows the last selection.')
        document.add_paragraph('%d distinct wallets were assessed across %d daily, weekly, monthly, and all time leaderboard slots. The current selection has %d core wallets and %d experimental wallets. Last screening completed %s.' %
            (screening['assessed_count'],sum(c['leaderboard_slots'] for c in screening['coverage']),screening['selected_core'],screening['selected_experimental'],timestamp(screening['generated'])))
        document.add_paragraph('Core screening requires at least %d distinct trade transactions, %d closed markets, and %d observed trading days in the sampled %d day window. Sampled profit must remain positive after excluding the largest winning market; that market may contribute at most %.0f percent of positive market profit. The minimum profit factor is %.1f.' %
            (policy['min_transactions'],policy['min_closed_markets'],policy['min_active_days'],policy['lookback_days'],policy['max_largest_win_share']*100,policy['min_profit_factor']))
        document.add_paragraph('Experimental wallets may have concentrated wins and weaker evidence. At most %d are selected, with a $%.2f copied position cap each, a $1 daily cap per experimental wallet, and a $2 daily cap for the experimental group. The $40 total purchase cap still applies; there is no overall daily purchase cap. Trial in the list below means experimental. The full assessed list and exclusion reasons are saved in trader-screening.csv.' %
            (policy['max_experimental_wallets'],policy['experimental_max_cost']))
        document.add_paragraph(screening['caveat'])
        document.add_paragraph('Open losses, deposits, off platform hedges and related wallets are not assessed. Passing this screen does not establish a copyable trading advantage or low risk.')
        selected = screening['selected']
        if selected:
            table(document,['Trader','Group','Trades','Markets','Sample profit','Ex big win','Top win'],
                [(r['name'],'Core' if r['classification']=='core' else 'Trial',str(r['transactions']),str(r['closed_markets']),dollars(r['sampled_realized_pnl']),dollars(r['profit_ex_largest_winner']),('%.0f%%'%(100*r['largest_winner_share'])) if r['largest_winner_share'] is not None else 'Unknown') for r in selected],
                [1.7,.6,.6,.6,1.2,1.3,1.0])
        else:
            document.add_paragraph('No wallets currently satisfy the selection rules. New screened entries remain paused rather than relaxing the rules automatically.')
        if screening['errors']:
            document.add_paragraph('%d candidates could not be assessed because of API or data errors; they were not selected.'%screening['errors'])
    if account['scans']:
        document.add_paragraph('Monitoring progress','Heading 1')
        scans = account['scans']
        if len(scans) > 5:
            document.add_paragraph('%d source wallets monitored. Latest scan %s. Details are saved in trade-report.json.' %
                (len(scans), timestamp(max(s['checked'] for s in scans))))
            scans = [s for s in scans if s['error'] or report['generated_timestamp']-s['checked'] > 120]
            if not scans:
                document.paragraphs[-1].add_run(' All scans are fresh without reported API errors.')
        for scan in scans:
            state = scan['error'] or ('Polling normally' if report['generated_timestamp']-scan['checked'] <= 120 else 'Last scan is stale')
            paragraph = document.add_paragraph(clean(scan['wallet']))
            paragraph.paragraph_format.space_after = Pt(2)
            for run in paragraph.runs:
                run.font.size = Pt(8)
            document.add_paragraph('Last scan '+timestamp(scan['checked'])+'. '+clean(state)+'.')
    output = Path(output)
    temporary = output.with_name(output.stem+'.tmp.docx')
    for root in (document._element, document.styles.element):
        for border in list(root.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    document.save(temporary)
    os.replace(temporary,output)


if __name__ == '__main__':
    build(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
