"""
BriefDrafter NYSCEF Hyperlinker: standalone tool for adding clickable
NYSCEF hyperlinks to record citations in .docx and .pdf files.
"""

import os
import re
import uuid
import tempfile
from flask import Blueprint, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

hyperlinker_bp = Blueprint('hyperlinker', __name__)

# Temp storage for hyperlinker uploads: session_id -> {path, filename, processed_path}
_hyperlinker_sessions = {}

# Match bare page-number citations: (4), (5-6), (47, 55), (547-548, 556)
CITATION_PAT = re.compile(
    r'(?<![a-zA-Z0-9§¶)\-])'
    r'(\(\d+(?:\s*-\s*\d+)?(?:,\s*\d+(?:\s*-\s*\d+)?)*\))'
    r'(?![a-zA-Z])'
)


def _parse_pdf_page_labels(pdf_path):
    """Parse /PageLabels from a local PDF into a
    {label_string: physical_page_number (1-based)} mapping.
    Returns None if the PDF has no page labels."""
    import PyPDF2
    from PyPDF2.generic import IndirectObject

    reader = PyPDF2.PdfReader(pdf_path)

    try:
        root = reader.trailer['/Root']
        if '/PageLabels' not in root:
            return None
        page_labels_obj = root['/PageLabels']
        if isinstance(page_labels_obj, IndirectObject):
            page_labels_obj = page_labels_obj.get_object()
    except (KeyError, TypeError):
        return None

    nums = []
    if '/Nums' in page_labels_obj:
        raw = page_labels_obj['/Nums']
        for item in raw:
            nums.append(item.get_object() if isinstance(item, IndirectObject) else item)
    elif '/Kids' in page_labels_obj:
        def _flatten(node):
            if isinstance(node, IndirectObject):
                node = node.get_object()
            if '/Nums' in node:
                for item in node['/Nums']:
                    nums.append(item.get_object() if isinstance(item, IndirectObject) else item)
            if '/Kids' in node:
                for kid in node['/Kids']:
                    _flatten(kid)
        _flatten(page_labels_obj)

    if not nums:
        return None

    ranges = []
    i = 0
    while i < len(nums) - 1:
        page_idx = int(nums[i])
        label_dict = nums[i + 1]
        if isinstance(label_dict, IndirectObject):
            label_dict = label_dict.get_object()
        style = str(label_dict.get('/S', '')) if '/S' in label_dict else None
        start = int(label_dict.get('/St', 1)) if '/St' in label_dict else 1
        prefix = str(label_dict.get('/P', '')) if '/P' in label_dict else ''
        ranges.append((page_idx, style, start, prefix))
        i += 2

    ranges.sort(key=lambda x: x[0])
    total_pages = len(reader.pages)

    def _to_roman(n):
        vals = [1000,900,500,400,100,90,50,40,10,9,5,4,1]
        syms = ['M','CM','D','CD','C','XC','L','XL','X','IX','V','IV','I']
        r = ''
        for vi, v in enumerate(vals):
            while n >= v:
                r += syms[vi]
                n -= v
        return r

    label_map = {}
    for ri, (page_idx, style, start, prefix) in enumerate(ranges):
        end_idx = ranges[ri + 1][0] if ri + 1 < len(ranges) else total_pages
        for pi in range(page_idx, end_idx):
            num = start + (pi - page_idx)
            if style == '/D':
                label = prefix + str(num)
            elif style == '/r':
                label = prefix + _to_roman(num).lower()
            elif style == '/R':
                label = prefix + _to_roman(num)
            elif style is None:
                label = prefix
            else:
                label = prefix + str(num)
            label_map[label] = pi + 1

    return label_map


def _resolve_nyscef_url_via_labels(page_num, label_maps):
    """Given a record page number, look it up across all volume label maps."""
    page_str = str(page_num)
    for vol in label_maps:
        label_map = vol.get('label_map')
        if not label_map:
            continue
        physical = label_map.get(page_str)
        if physical is not None:
            doc_index = vol['doc_index']
            return f"https://iapps.courts.state.ny.us/nyscef/ViewDocument?docIndex={doc_index}#page={physical}"
    return None


def add_hyperlinks_to_docx(docx_path, label_maps):
    """Open an existing .docx and add NYSCEF hyperlinks to record citations."""
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from copy import deepcopy

    doc = DocxDocument(docx_path)
    link_count = 0

    for paragraph in doc.paragraphs:
        full_text = paragraph.text
        if not CITATION_PAT.search(full_text):
            continue

        runs = paragraph.runs
        if not runs:
            continue

        run_boundaries = []
        pos = 0
        for run in runs:
            run_text = run.text or ''
            run_boundaries.append((pos, pos + len(run_text), run))
            pos += len(run_text)

        matches = list(CITATION_PAT.finditer(full_text))
        if not matches:
            continue

        has_resolvable = False
        for m in matches:
            inner = m.group(1)[1:-1]
            parts = [p.strip() for p in inner.split(',')]
            for part in parts:
                page = int(re.match(r'(\d+)', part).group(1))
                if _resolve_nyscef_url_via_labels(page, label_maps):
                    has_resolvable = True
                    break
            if has_resolvable:
                break
        if not has_resolvable:
            continue

        p_elem = paragraph._p

        char_formats = []
        for start, end, run in run_boundaries:
            rPr = run._r.find(qn('w:rPr'))
            rPr_copy = deepcopy(rPr) if rPr is not None else None
            for _ in range(end - start):
                char_formats.append(rPr_copy)

        for child in list(p_elem):
            if child.tag == qn('w:r') or child.tag == qn('w:hyperlink'):
                p_elem.remove(child)

        cursor = 0
        part = paragraph.part

        def _make_run_elem(text, rPr_source):
            r = OxmlElement('w:r')
            if rPr_source is not None:
                r.append(deepcopy(rPr_source))
            t = OxmlElement('w:t')
            t.set(qn('xml:space'), 'preserve')
            t.text = text
            r.append(t)
            return r

        def _make_hyperlink_run(text, url, rPr_source):
            r_id = part.relate_to(
                url,
                'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
                is_external=True
            )
            hyperlink = OxmlElement('w:hyperlink')
            hyperlink.set(qn('r:id'), r_id)
            r = OxmlElement('w:r')
            if rPr_source is not None:
                rPr = deepcopy(rPr_source)
            else:
                rPr = OxmlElement('w:rPr')
            existing_color = rPr.find(qn('w:color'))
            if existing_color is not None:
                rPr.remove(existing_color)
            color = OxmlElement('w:color')
            color.set(qn('w:val'), '0000FF')
            rPr.append(color)
            existing_u = rPr.find(qn('w:u'))
            if existing_u is not None:
                rPr.remove(existing_u)
            u = OxmlElement('w:u')
            u.set(qn('w:val'), 'single')
            rPr.append(u)
            rStyle = OxmlElement('w:rStyle')
            rStyle.set(qn('w:val'), 'Hyperlink')
            rPr.insert(0, rStyle)
            r.append(rPr)
            t = OxmlElement('w:t')
            t.set(qn('xml:space'), 'preserve')
            t.text = text
            r.append(t)
            hyperlink.append(r)
            return hyperlink

        def _get_rPr_at(char_pos):
            if char_pos < len(char_formats):
                return char_formats[char_pos]
            elif char_formats:
                return char_formats[-1]
            return None

        for m in matches:
            if m.start() > cursor:
                plain = full_text[cursor:m.start()]
                rPr = _get_rPr_at(cursor)
                p_elem.append(_make_run_elem(plain, rPr))

            citation_text = m.group(1)
            inner = citation_text[1:-1]
            parts = [pt.strip() for pt in inner.split(',')]
            rPr_cite = _get_rPr_at(m.start())

            p_elem.append(_make_run_elem('(', rPr_cite))

            for i, part_text in enumerate(parts):
                if i > 0:
                    p_elem.append(_make_run_elem(', ', rPr_cite))
                page = int(re.match(r'(\d+)', part_text).group(1))
                url = _resolve_nyscef_url_via_labels(page, label_maps)
                if url:
                    p_elem.append(_make_hyperlink_run(part_text, url, rPr_cite))
                    link_count += 1
                else:
                    p_elem.append(_make_run_elem(part_text, rPr_cite))

            p_elem.append(_make_run_elem(')', rPr_cite))
            cursor = m.end()

        if cursor < len(full_text):
            rPr = _get_rPr_at(cursor)
            p_elem.append(_make_run_elem(full_text[cursor:], rPr))

    base, ext = os.path.splitext(docx_path)
    output_path = base + '_hyperlinked' + ext
    doc.save(output_path)
    return output_path, link_count


def add_hyperlinks_to_pdf(pdf_path, label_maps):
    """Open an existing PDF and add NYSCEF hyperlinks to record citations."""
    import fitz

    doc = fitz.open(pdf_path)
    link_count = 0

    for page in doc:
        text = page.get_text()
        if not CITATION_PAT.search(text):
            continue

        for m in CITATION_PAT.finditer(text):
            citation_text = m.group(1)
            inner = citation_text[1:-1]
            parts = [p.strip() for p in inner.split(',')]

            first_page = int(re.match(r'(\d+)', parts[0]).group(1))
            url = _resolve_nyscef_url_via_labels(first_page, label_maps)
            if not url:
                continue

            rects = page.search_for(citation_text)
            if not rects:
                continue

            rect = rects[0]
            link = {
                "kind": fitz.LINK_URI,
                "from": rect,
                "uri": url,
            }
            page.insert_link(link)
            link_count += 1

    base, ext = os.path.splitext(pdf_path)
    output_path = base + '_hyperlinked' + ext
    doc.save(output_path)
    doc.close()
    return output_path, link_count


# ============ ROUTES ============

@hyperlinker_bp.route('/hyperlinker')
def hyperlinker_page():
    """Render the NYSCEF hyperlinker tool page."""
    return render_template('hyperlinker.html')


@hyperlinker_bp.route('/hyperlinker/upload', methods=['POST'])
def hyperlinker_upload():
    """Accept a .docx or .pdf upload and store it in a temp directory."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file uploaded'}), 400
    lower_name = f.filename.lower()
    if lower_name.endswith('.docx'):
        file_type = 'docx'
    elif lower_name.endswith('.pdf'):
        file_type = 'pdf'
    else:
        return jsonify({'error': 'Please upload a .docx or .pdf file'}), 400

    session_id = str(uuid.uuid4())[:8]
    tmp_dir = tempfile.mkdtemp(prefix='hyperlinker_')
    filename = secure_filename(f.filename)
    filepath = os.path.join(tmp_dir, filename)
    f.save(filepath)

    _hyperlinker_sessions[session_id] = {
        'path': filepath,
        'filename': filename,
        'file_type': file_type,
        'tmp_dir': tmp_dir,
        'processed_path': None,
        'volumes': [],
    }
    return jsonify({'session_id': session_id, 'filename': filename, 'file_type': file_type})


@hyperlinker_bp.route('/hyperlinker/upload-volume', methods=['POST'])
def hyperlinker_upload_volume():
    """Accept a record volume PDF upload for page label parsing."""
    session_id = request.form.get('session_id')
    if not session_id or session_id not in _hyperlinker_sessions:
        return jsonify({'error': 'Invalid session'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename or not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file'}), 400

    nyscef_url = request.form.get('nyscef_url', '').strip()
    if not nyscef_url:
        return jsonify({'error': 'NYSCEF URL is required'}), 400

    doc_index = nyscef_url
    if 'docIndex=' in doc_index:
        doc_index = doc_index.split('docIndex=')[1].split('#')[0].split('&')[0]

    session = _hyperlinker_sessions[session_id]
    filename = secure_filename(f.filename)
    pdf_path = os.path.join(session['tmp_dir'], filename)
    f.save(pdf_path)

    label_map = _parse_pdf_page_labels(pdf_path)
    if label_map is None:
        os.unlink(pdf_path)
        return jsonify({'error': f'{f.filename} has no page labels.'}), 400

    vol_index = len(session['volumes'])
    session['volumes'].append({
        'pdf_path': pdf_path,
        'nyscef_url': nyscef_url,
        'doc_index': doc_index,
        'filename': f.filename,
        'label_count': len(label_map),
    })

    return jsonify({
        'success': True,
        'vol_index': vol_index,
        'filename': f.filename,
        'label_count': len(label_map),
    })


@hyperlinker_bp.route('/hyperlinker/remove-volume', methods=['POST'])
def hyperlinker_remove_volume():
    """Remove a previously uploaded volume."""
    data = request.json or {}
    session_id = data.get('session_id')
    vol_index = data.get('vol_index')
    if not session_id or session_id not in _hyperlinker_sessions:
        return jsonify({'error': 'Invalid session'}), 400

    session = _hyperlinker_sessions[session_id]
    if vol_index is not None and 0 <= vol_index < len(session['volumes']):
        vol = session['volumes'].pop(vol_index)
        if os.path.exists(vol['pdf_path']):
            os.unlink(vol['pdf_path'])

    return jsonify({'success': True, 'volume_count': len(session['volumes'])})


@hyperlinker_bp.route('/hyperlinker/process', methods=['POST'])
def hyperlinker_process():
    """Process the uploaded docx using page labels from uploaded volume PDFs."""
    data = request.json or {}
    session_id = data.get('session_id')
    if not session_id or session_id not in _hyperlinker_sessions:
        return jsonify({'error': 'Invalid session. Please re-upload your file.'}), 400

    session = _hyperlinker_sessions[session_id]

    if not session['volumes']:
        return jsonify({'error': 'Please upload at least one record volume PDF.'}), 400

    label_maps = []
    for vi, vol in enumerate(session['volumes']):
        try:
            label_map = _parse_pdf_page_labels(vol['pdf_path'])
        except Exception as e:
            return jsonify({'error': f'Failed to parse {vol["filename"]}: {str(e)}'}), 500

        if label_map is None:
            return jsonify({'error': f'{vol["filename"]} has no page labels.'}), 400

        label_maps.append({'doc_index': vol['doc_index'], 'label_map': label_map})

    try:
        if session.get('file_type') == 'pdf':
            output_path, link_count = add_hyperlinks_to_pdf(session['path'], label_maps)
        else:
            output_path, link_count = add_hyperlinks_to_docx(session['path'], label_maps)
        session['processed_path'] = output_path
        return jsonify({'success': True, 'link_count': link_count, 'volumes_parsed': len(label_maps)})
    except Exception as e:
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500


@hyperlinker_bp.route('/hyperlinker/download')
def hyperlinker_download():
    """Download the processed file."""
    session_id = request.args.get('session_id')
    if not session_id or session_id not in _hyperlinker_sessions:
        return jsonify({'error': 'Invalid session'}), 400

    session = _hyperlinker_sessions[session_id]
    if not session.get('processed_path') or not os.path.exists(session['processed_path']):
        return jsonify({'error': 'No processed file available. Please process first.'}), 400

    base, ext = os.path.splitext(session['filename'])
    download_name = f"{base}_hyperlinked{ext}"

    if session.get('file_type') == 'pdf':
        mimetype = 'application/pdf'
    else:
        mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    return send_file(
        session['processed_path'],
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype,
    )
