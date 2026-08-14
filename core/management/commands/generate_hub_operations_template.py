from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.management.base import BaseCommand, CommandError

from core.services.operations_import import HEADERS, ORDER, SHEETS


EXAMPLES = {
    'purchases': ['SKIP', 'غير مراجع', 'PUR-EXAMPLE-001', '2026-01-15', '', 'اسم المورد', 'INV-001', '0', 'مثال فقط'],
    'purchase_items': ['SKIP', 'LINE-EXAMPLE-001', 'PUR-EXAMPLE-001', 'ING-COFFEE', 'بن', '1', 'kg', '10000', ''],
    'order_payments': ['SKIP', 'مراجع', 'PAY-EXAMPLE-001', '2026-01-15', '00000000-0000-0000-0000-000000000000', '#00001', '5000', 'cash', '', 'مثال فقط'],
    'inventory': ['SKIP', 'غير مراجع', 'ING-EXAMPLE', 'مادة مثالية', 'Example', 'ingredient', 'kg', '1', '0', '', ''],
    'recipes': ['SKIP', 'غير مراجع', 'PRODUCT-KEY', 'اسم المنتج', 'ING-EXAMPLE', 'مادة مثالية', '10', 'g', '0', ''],
}


def column_name(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def worksheet(section):
    data = [HEADERS[section], EXAMPLES[section]]
    xml_rows = []
    for row_number, values in enumerate(data, 1):
        cells = ''.join(
            f'<c r="{column_name(index)}{row_number}" t="inlineStr" s="{1 if row_number == 1 else 0}"><is><t>{escape(str(value))}</t></is></c>'
            for index, value in enumerate(values, 1)
        )
        xml_rows.append(f'<row r="{row_number}">{cells}</row>')
    last = column_name(len(HEADERS[section]))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="A1:{last}2"/><sheetViews><sheetView workbookViewId="0" rightToLeft="1"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="18"/><cols><col min="1" max="30" width="22" customWidth="1"/></cols>'
            f'<sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:{last}2"/></worksheet>')


def generate_workbook(target):
    """Write the canonical operations workbook to a path or binary stream."""
    content_types = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, 6))
    sheets = ''.join(f'<sheet name="{escape(SHEETS[section])}" sheetId="{i}" r:id="rId{i}"/>' for i, section in enumerate(ORDER, 1))
    rels = ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, 6))
    instructions = 'الإجراءات: SKIP / CREATE_DRAFT / CREATE_AND_RECEIVE / CREATE / COLLECT / MATCH_ONLY / CREATE_INACTIVE / UPSERT_INACTIVE / UPSERT_ACTIVE. طرق الدفع: cash, manual_transfer. دفعات الموردين غير مدعومة عمداً لحين اعتماد سياسة D07–D11.'
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + content_types + '</Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><fileVersion appName="Hub operations"/><sheets>' + sheets + f'</sheets><definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">\'{SHEETS["purchases"]}\'!$1:$1</definedName></definedNames><extLst><ext uri="hub-instructions"><instructions>{escape(instructions)}</instructions></ext></extLst></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font/><font><b/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')
        for index, section in enumerate(ORDER, 1): archive.writestr(f'xl/worksheets/sheet{index}.xml', worksheet(section))

def workbook_bytes():
    output = BytesIO()
    generate_workbook(output)
    return output.getvalue()


class Command(BaseCommand):
    help = 'Generate the safe Hub operations import workbook template.'

    def add_arguments(self, parser): parser.add_argument('output')

    def handle(self, *args, **options):
        output = Path(options['output'])
        if output.exists(): raise CommandError(f'Refusing to overwrite existing file: {output}')
        output.parent.mkdir(parents=True, exist_ok=True)
        generate_workbook(output)
        self.stdout.write(f'Created operations template: {output}')
