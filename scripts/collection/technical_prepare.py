"""Prepare explicit source-reviewed tables and bounded UCI dataset documentation."""
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from zipfile import ZipFile
from html import escape

from scripts.collection.technical_extract import VERSION, fragments, table_html, write_table

PREPARED_VERSION = 'prepared_v6'


def mark_unmapped_controls(text):
    """Expose unmapped PDF font glyphs without guessing mathematical brackets."""
    codes = sorted({ord(c) for c in text if ord(c) < 32 and c not in '\n\r\t'})
    for code in codes:
        text = text.replace(chr(code), f'【未映射字形U+{code:04X}】')
    return text, codes


def recover_pdf_encoding(result, source, output):
    """OCR pages with a demonstrably broken native character map; retain originals."""
    if source.suffix != '.pdf':
        return
    import fitz
    from app.loaders.ocr_router import NativeVisionOcr
    targets = set()
    for c in result['chunks']:
        text = c['content']; controls = sum(ord(x) < 32 and x not in '\n\r\t' for x in text)
        if controls >= 20 and controls / max(1, len(text)) > .025 and c['metadata'].get('page'):
            targets.add(c['metadata']['page'])
    if not targets:
        return
    replacement = {}
    with fitz.open(source) as pdf:
        for page in sorted(targets):
            preview = output / 'ocr' / f'encoding-page-{page}.png'
            pdf[page - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(preview)
            recognized = NativeVisionOcr(Path('data/runtime').resolve()).recognize(preview)
            if not recognized['text'].strip():
                raise ValueError('Native character mapping failed and OCR found no text')
            write_json(output / 'ocr' / f'encoding-page-{page}.json', recognized)
            replacement[page] = [{'content': part, 'metadata': {'page': page, 'section': f'Page {page}',
                'chunk_kind': 'technical_prose', 'evidence_kind': 'ocr_text', 'parse_route': 'ocr_native_encoding_failure',
                'structure_review': 'machine_ocr_requires_source_check'}} for part in fragments(recognized['text'])]
    chunks = []; emitted = set()
    for c in result['chunks']:
        page = c['metadata'].get('page')
        if page not in replacement:
            chunks.append(c)
        elif page not in emitted:
            chunks.extend(replacement[page]); emitted.add(page)
    result['chunks'] = chunks
    result['warnings'].append({'stage': 'native_encoding', 'pages': sorted(targets), 'fallback': 'local_ocr_with_original_retained'})


def office_table_structure(xml):
    """Keep actual Office cell spans; don't repeat merged cells and blank fill space."""
    from lxml import etree
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    w = '{' + ns['w'] + '}'
    tree = etree.fromstring(xml.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))
    grid = []; display = []; active = {}
    for tr in tree.findall('w:tr', ns):
        row = []; shown = []; col = 0
        for tc in tr.findall('w:tc', ns):
            span = tc.find('w:tcPr/w:gridSpan', ns)
            width = int(span.get(w + 'val')) if span is not None else 1
            if not 1 <= width <= 100:
                raise ValueError('Unexpected Office table span')
            merge = tc.find('w:tcPr/w:vMerge', ns)
            continuation = merge is not None and merge.get(w + 'val', 'continue') == 'continue'
            text = '\n'.join(''.join(p.xpath('.//w:t/text()', namespaces=ns)) for p in tc.findall('w:p', ns))
            text = re.sub(r'[ \t]{2,}', ' ', text).strip()
            if continuation and col in active:
                active[col]['rowspan'] += 1
                row.extend([None] * width)
            else:
                cell = {'text': text, 'colspan': width, 'rowspan': 1}
                shown.append(cell); row.extend([text] + [None] * (width - 1))
                if merge is not None:
                    active[col] = cell
                else:
                    active.pop(col, None)
            col += width
        grid.append(row); display.append(shown)
    markup = '<table>' + ''.join('<tr>' + ''.join(
        f'<td colspan="{c["colspan"]}" rowspan="{c["rowspan"]}">' + escape(c['text']).replace('\n', '<br>') + '</td>' for c in row) + '</tr>' for row in display) + '</table>'
    return grid, markup


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def append_table(result, output, identity, rows, attributes):
    item = write_table(output / 'tables', identity, rows, attributes)
    item['structure_review'] = attributes.get('structure_review', 'source_dictionary_parsed')
    write_json(output / 'tables' / (identity + '.json'), item)
    result['tables'].append(item)
    meta = {k: v for k, v in attributes.items() if k != 'context'}
    result['chunks'].append({'content': attributes.get('structured_html', table_html(rows)) + '\n\n【来源范围与说明】\n' + attributes.get('context', ''),
        'metadata': {**meta, 'table_id': identity, 'section': attributes.get('title', identity),
                     'chunk_kind': 'technical_table', 'chunk_type': 'table', 'evidence_kind': 'source_table',
                     'protected_structure': True, 'table_row_count': len(rows), 'table_col_count': max(map(len, rows))}})


def reviewed_tables(root, item, result, output, specs):
    """No generated factual tables: rows must come from an explicit source review record."""
    for spec in specs:
        if spec['source_id'] != item['id']:
            continue
        source = (root / spec['source_artifact']).resolve()
        if not source.is_relative_to(root.resolve()) or sha256(source.read_bytes()).hexdigest() != spec['artifact_sha256']:
            raise ValueError('Reviewed table source changed')
        append_table(result, output, spec['table_id'], spec['rows'], {
            k: v for k, v in spec.items() if k not in {'rows', 'table_id', 'source_id'}})


def dataset_extract(root, item, output):
    import numpy as np
    source = root / item['file']
    if sha256(source.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Dataset source changed')
    result = {'id': item['id'], 'source_sha256': item['sha256'], 'extractor': 'uci_source_dictionary_v1',
              'knowledge_base_id': item['knowledge_base_id'], 'pages': [], 'tables': [], 'figures': [], 'chunks': [], 'warnings': []}
    validation = {'raw_zip_retained': True, 'members': [], 'complete_numeric_data_indexed_as_qa': False}
    with ZipFile(source) as archive:
        if sum(m.file_size for m in archive.infolist()) > 1024**3:
            raise ValueError('Dataset expanded size exceeds 1 GiB review budget')
        if item['id'] == 'uci_steel_data':
            names = archive.read('Faults27x7_var').decode('ascii').splitlines()
            raw = archive.read('Faults.NNA')
            values = np.loadtxt(io.BytesIO(raw))
            if len(names) != 34 or values.shape != (1941, 34) or not np.isfinite(values).all():
                raise ValueError('Unexpected steel data schema or missing values')
            if not np.all(values[:, 27:].sum(axis=1) == 1) or not np.isin(values[:, 27:], [0, 1]).all():
                raise ValueError('Unexpected steel defect encoding')
            with (output / 'data' / 'steel_plates_1941.csv').open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f); writer.writerow(names)
                writer.writerows(line.split() for line in raw.decode('ascii').splitlines() if line.strip())
            rows = [['列序号', '原始字段名', '角色', '单位/编码']]
            rows += [[str(i), n, '输入特征' if i <= 27 else '缺陷标签', '源文件未声明，不推断单位' if i <= 27 else '0/1；7列单热编码'] for i, n in enumerate(names, 1)]
            append_table(result, output, 'steel_field_dictionary', rows, {'source_method': 'source_variable_file_and_numeric_validation',
                'source_member': 'Faults27x7_var', 'title': '钢板缺陷：27个特征与7类标签',
                'context': 'UCI Steel Plates Faults；1941条记录、34列。完整CSV按原始字段顺序保存。字段名 K_Scatch 按源文件保留，不擅自改成其他拼写。未提供物理单位的字段不臆测。训练/测试划分未生成。'})
            validation.update(rows=1941, input_features=27, label_columns=7, finite=True, one_hot_labels=True)
        elif item['id'] == 'uci_hydraulic_data':
            # The archive is Windows-1252, unlike the UTF-8 website with mojibake.
            raw_description = archive.read('description.txt')
            description = raw_description.decode('cp1252')
            if '°C' not in description:
                raise ValueError('Temperature unit not verified in original dataset description')
            for name in ('description.txt', 'documentation.txt'):
                raw = archive.read(name)
                (output / 'data' / name).write_bytes(raw)
                decoded = raw.decode('cp1252')
                (output / 'data' / (name + '.utf8.txt')).write_text(decoded, encoding='utf-8')
                for fragment in fragments(decoded):
                    result['chunks'].append({'content': fragment, 'metadata': {'section': name,
                        'source_member': name, 'source_encoding': 'windows-1252', 'chunk_kind': 'dataset_documentation', 'evidence_kind': 'source_text'}})
            block = description.split('Sensor\t', 1)[1].split('The target condition', 1)[0]
            rows = [['Sensor', 'Physical quantity', 'Unit', 'Sampling rate']]
            for line in block.splitlines()[1:]:
                cells = re.split(r'\t+', line.strip())
                if len(cells) == 4:
                    rows.append(cells)
            if len(rows) != 18:
                raise ValueError('Expected 17 sensor definitions')
            append_table(result, output, 'hydraulic_sensor_dictionary', rows, {'source_method': 'original_cp1252_description_tsv',
                'source_member': 'description.txt', 'title': '液压状态监测：17个传感器的单位与采样频率',
                'context': '原始ZIP内description.txt按Windows-1252解码，°C直接来自源字节。2205个60秒定负载试验周期；行=周期，列=该周期内采样点。CE/CP是虚拟传感量。试验台数据不代表任意生产设备。'})
            expected = {row[0] + '.txt': int(row[3].split()[0]) * 60 for row in rows[1:]}
            expected['profile.txt'] = 5
            for name, width in expected.items():
                count = 0; preview = []
                with archive.open(name) as handle:
                    for line in handle:
                        values = np.fromstring(line.decode('ascii'), sep='\t')
                        if len(values) != width or not np.isfinite(values).all():
                            raise ValueError('Invalid numeric matrix: ' + name)
                        count += 1
                        if count <= 3:
                            preview.append(line.decode('ascii'))
                if count != 2205:
                    raise ValueError('Unexpected cycle count: ' + name)
                validation['members'].append({'file': name, 'rows': count, 'columns': width, 'finite': True})
                (output / 'data' / (name + '.first3cycles.tsv')).write_text(''.join(preview))
            (output / 'data' / 'profile.txt').write_bytes(archive.read('profile.txt'))
            validation.update(cycles=2205, sensors=17, description_encoding='windows-1252', target_columns=5)
        else:
            raise ValueError('Dataset requires an explicit reviewed schema')
    result['dataset_validation'] = validation
    return result


def prepare(root, item, specs):
    root = Path(root); source_dir = root / 'sources' / item['id']; output = source_dir / PREPARED_VERSION
    receipt = output / 'prepared.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    for name in ('tables', 'data', 'ocr'):
        (output / name).mkdir(parents=True, exist_ok=True)
    if item['file'].endswith('.zip'):
        result = dataset_extract(root, item, output)
    else:
        extraction = source_dir / VERSION / 'extraction.json'
        result = json.loads(extraction.read_text())
        result['base_extraction'] = str(extraction.relative_to(root))
        result['base_extraction_sha256'] = sha256(extraction.read_bytes()).hexdigest()
        # PDF line detection can mistake diagrams for tables or split one column into
        # several cells. Keep every candidate artifact, but don't publish its guessed
        # grid as factual evidence. Index the original page text once until reviewed.
        source_pages = set(); filtered = []
        office_tables = {t['table_id']: t for t in result['tables'] if t.get('source_method') == 'python_docx_after_libreoffice_conversion'}
        for chunk in result['chunks']:
            meta = chunk['metadata']
            if Path(item['file']).suffix == '.pdf' and meta.get('evidence_kind') == 'source_table':
                page = meta['page']
                if page not in source_pages:
                    original = json.loads((extraction.parent / 'pages' / f'p{page:04d}.json').read_text())
                    filtered.append({'content': '【原文页文本；表格列关系、合并单元格和上下标须回看原页】\n' + original['text'],
                        'metadata': {'page': page, 'section': f'Page {page}', 'chunk_kind': 'source_page_with_table',
                            'evidence_kind': 'source_text', 'structure_review': 'table_layout_not_verified', 'protected_structure': True}})
                    source_pages.add(page)
            elif meta.get('table_id') in office_tables:
                table = office_tables[meta['table_id']]
                grid, markup = office_table_structure(table['original_ooxml'])
                derived = {**table, 'rows': grid, 'structured_html': markup, 'structure_review': 'office_cell_spans_retained_after_conversion'}
                write_json(output / 'tables' / (table['table_id'] + '.json'), derived)
                (output / 'tables' / (table['table_id'] + '.html')).write_text('<meta charset="utf-8">' + markup)
                with (output / 'tables' / (table['table_id'] + '.csv')).open('w', encoding='utf-8-sig', newline='') as handle:
                    csv.writer(handle).writerows(grid)
                filtered.append({'content': markup + '\n\n【原文前文】\n' + table.get('context', ''),
                    'metadata': {**meta, 'structure_review': 'office_cell_spans_retained_after_conversion', 'source_blank_spacing_compacted': True}})
            else:
                filtered.append(chunk)
        result['chunks'] = filtered
        result['machine_table_candidates'] = len(result['tables'])
        reviewed_tables(root, item, result, output, specs)
        if item['id'] in {'ahss_rsw', 'cn_cosmetic_validation'}:
            from app.loaders.ocr_router import NativeVisionOcr
            ocr = NativeVisionOcr(Path('data/runtime').resolve())
            for figure in result['figures']:
                if item['id'] == 'ahss_rsw' and figure['figure_id'] not in {'html_figure01', 'html_figure02', 'html_figure03', 'html_figure04'}:
                    continue
                image = source_dir / VERSION / figure['file']
                recognition = ocr.recognize(image)
                recognition.update(source_sha256=figure['sha256'], figure_id=figure['figure_id'])
                write_json(output / 'ocr' / (figure['figure_id'] + '.json'), recognition)
                # Reviewed Hubei cell transcriptions supersede OCR for indexing, but keep both artifacts.
                if item['id'] == 'ahss_rsw' and recognition['text'].strip():
                    result['chunks'].append({'content': '【图片表格的机器OCR；列与数值对应关系未核验，必须回看原图】\n' + figure.get('caption', '') + '\n' + recognition['text'],
                        'metadata': {'figure_id': figure['figure_id'], 'section': figure.get('caption', 'Image table'),
                            'chunk_kind': 'image_table_ocr', 'evidence_kind': 'ocr_text', 'structure_review': 'not_inferred',
                            'image_sha256': figure['sha256'], 'parse_route': 'machine_ocr'}})
    recover_pdf_encoding(result, root / item['file'], output)
    for chunk in result['chunks']:
        text, codes = mark_unmapped_controls(chunk['content'])
        if codes:
            chunk['content'] = '【PDF原文存在未映射字形，请回看原页；若涉及公式，不得据此补全或计算】\n' + text
            chunk['metadata'].update(unmapped_control_codepoints=codes, formula_layout_review='requires_original_page',
                                     original_glyphs_not_inferred=True)
    result.update(preparer_code_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  review_specs_sha256=sha256(json.dumps(specs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                  review_scope='source_relevance_and_selected_tables_not_all_engineering_claims',
                  original_file=item['file'])
    write_json(receipt, result)
    return result
