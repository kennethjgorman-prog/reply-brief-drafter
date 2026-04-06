"""
BriefDrafter Record Hyperlinker: standalone tool for adding clickable
hyperlinks to record/appendix citations in .docx and .pdf files.
Supports NYSCEF (NY state) and PACER/ECF (federal) court systems.
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

# === Citation Pattern Definitions ===
_D = r'[-\u2013]'  # hyphen or en-dash
_PAGE_PART = rf'\d+(?:\s*{_D}\s*\d+)?'
_PAGE_LIST = rf'{_PAGE_PART}(?:,\s*{_PAGE_PART})*'

CITATION_PATTERNS = {
    # Bare page numbers: (328), (328-329), (9, 10, 11)
    'bare': re.compile(
        rf'(?<![a-zA-Z0-9§¶)\-])'
        rf'\({_PAGE_LIST}\)'
        rf'(?![a-zA-Z])'
    ),
    # Record with R. prefix: (R. 328), (R 328), (R. at 328), (R. 328-329)
    'r_dot': re.compile(rf'\(R\.?\s*(?:at\s+)?{_PAGE_LIST}\)'),
    # Appendix: (A. 328), (A 328), (App. 328), (Appx. 328), (Appendix 328)
    'a_dot': re.compile(rf'\((?:A\.?|App(?:x|endix)?\.?)\s*(?:at\s+)?{_PAGE_LIST}\)'),
    # Joint Appendix: (JA. 328), (JA 328), (J.A. 328)
    'ja_dot': re.compile(rf'\((?:JA\.?|J\.A\.?)\s*(?:at\s+)?{_PAGE_LIST}\)'),
    # Special/Supplemental Appendix: (SA. 328), (SA 328), (SPA. 328), (SPA 328)
    'sa_dot': re.compile(rf'\((?:SA\.?|SPA\.?)\s*(?:at\s+)?{_PAGE_LIST}\)'),
}

# Backward compatibility
CITATION_PAT = CITATION_PATTERNS['bare']


def _find_all_citations(text, citation_formats):
    """Find all citations in text using selected format patterns.
    Returns sorted, non-overlapping list of (start, end, text) tuples."""
    matches = {}
    for fmt in citation_formats:
        pat = CITATION_PATTERNS.get(fmt)
        if not pat:
            continue
        for m in pat.finditer(text):
            s = m.start()
            if s not in matches or len(m.group(0)) > len(matches[s][2]):
                matches[s] = (s, m.end(), m.group(0))
    sorted_matches = sorted(matches.values())
    result = []
    last_end = 0
    for s, e, t in sorted_matches:
        if s >= last_end:
            result.append((s, e, t))
            last_end = e
    return result


def _extract_prefix_and_pages(citation_text):
    """Extract prefix and page parts from a citation string.
    '(R. 328-329, 335)' -> ('R. ', ['328-329', '335'])
    '(328-329)' -> ('', ['328-329'])
    """
    inner = citation_text[1:-1]
    first_digit = re.search(r'\d', inner)
    if first_digit and first_digit.start() > 0:
        prefix = inner[:first_digit.start()]
        page_content = inner[first_digit.start():]
    else:
        prefix = ''
        page_content = inner
    parts = [p.strip() for p in page_content.split(',')]
    return prefix, parts


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


def _resolve_url_via_labels(page_num, label_maps):
    """Given a record/appendix page number, look it up across all volume label maps.
    Supports NYSCEF, external URLs, and hosted document links.
    Returns dict: {'type': 'uri', 'uri': str} or None."""
    page_str = str(page_num)
    for vol in label_maps:
        label_map = vol.get('label_map')
        if not label_map:
            continue
        physical = label_map.get(page_str)
        if physical is not None:
            link_mode = vol.get('link_mode', 'url')
            if link_mode == 'hosted':
                # Self-hosted PDF — HTTPS URL, no security warnings
                hosted_id = vol.get('hosted_id', '')
                base = vol.get('host_base_url', '')
                return {'type': 'uri', 'uri': f"{base}/hyperlinker/hosted/{hosted_id}#page={physical}"}
            court_system = vol.get('court_system', 'nyscef')
            if court_system == 'pacer':
                base_url = vol['base_url'].split('#')[0]
                return {'type': 'uri', 'uri': f"{base_url}#page={physical}"}
            else:
                doc_index = vol['doc_index']
                return {'type': 'uri', 'uri': f"https://iapps.courts.state.ny.us/nyscef/ViewDocument?docIndex={doc_index}#page={physical}"}
    return None


def add_hyperlinks_to_docx(docx_path, label_maps, citation_formats=None):
    """Open an existing .docx and add hyperlinks to record/appendix citations.
    Supports bare pages, R., A./App., JA., SA./SPA. citation formats."""
    if citation_formats is None:
        citation_formats = ['bare']

    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from copy import deepcopy

    doc = DocxDocument(docx_path)
    link_count = 0

    for paragraph in doc.paragraphs:
        full_text = paragraph.text
        citations = _find_all_citations(full_text, citation_formats)
        if not citations:
            continue

        runs = paragraph.runs
        if not runs:
            continue

        # Check if any citation resolves
        has_resolvable = False
        for _, _, cite_text in citations:
            _, parts = _extract_prefix_and_pages(cite_text)
            for part_t in parts:
                page_match = re.match(r'(\d+)', part_t)
                if page_match and _resolve_url_via_labels(int(page_match.group(1)), label_maps):
                    has_resolvable = True
                    break
            if has_resolvable:
                break
        if not has_resolvable:
            continue

        run_boundaries = []
        pos = 0
        for run in runs:
            run_text = run.text or ''
            run_boundaries.append((pos, pos + len(run_text), run))
            pos += len(run_text)

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
        part_ref = paragraph.part

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
            r_id = part_ref.relate_to(
                url,
                'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
                is_external=True
            )
            hyperlink = OxmlElement('w:hyperlink')
            hyperlink.set(qn('r:id'), r_id)
            hyperlink.set(qn('w:tgtFrame'), '_blank')
            r = OxmlElement('w:r')
            if rPr_source is not None:
                rPr = deepcopy(rPr_source)
            else:
                rPr = OxmlElement('w:rPr')
            existing_color = rPr.find(qn('w:color'))
            if existing_color is not None:
                rPr.remove(existing_color)
            existing_u = rPr.find(qn('w:u'))
            if existing_u is not None:
                rPr.remove(existing_u)
            u = OxmlElement('w:u')
            u.set(qn('w:val'), 'none')
            rPr.append(u)
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

        for cite_start, cite_end, cite_text in citations:
            if cite_start > cursor:
                plain = full_text[cursor:cite_start]
                rPr = _get_rPr_at(cursor)
                p_elem.append(_make_run_elem(plain, rPr))

            prefix, parts = _extract_prefix_and_pages(cite_text)
            rPr_cite = _get_rPr_at(cite_start)

            # Emit opening paren + any prefix (e.g., "(R. " or "(JA. " or "(")
            p_elem.append(_make_run_elem('(' + prefix, rPr_cite))

            for i, part_text in enumerate(parts):
                if i > 0:
                    p_elem.append(_make_run_elem(', ', rPr_cite))
                page = int(re.match(r'(\d+)', part_text).group(1))
                resolved = _resolve_url_via_labels(page, label_maps)
                if resolved:
                    p_elem.append(_make_hyperlink_run(part_text, resolved['uri'], rPr_cite))
                    link_count += 1
                else:
                    p_elem.append(_make_run_elem(part_text, rPr_cite))

            p_elem.append(_make_run_elem(')', rPr_cite))
            cursor = cite_end

        if cursor < len(full_text):
            rPr = _get_rPr_at(cursor)
            p_elem.append(_make_run_elem(full_text[cursor:], rPr))

    base, ext = os.path.splitext(docx_path)
    output_path = base + '_hyperlinked' + ext
    doc.save(output_path)
    return output_path, link_count


def _convert_docx_to_pdf(docx_path):
    """Convert DOCX to PDF using Microsoft Word on macOS."""
    import subprocess
    output_pdf = os.path.splitext(docx_path)[0] + '.pdf'
    script = f'''
    tell application "Microsoft Word"
        open POSIX file "{docx_path}"
        set theDoc to active document
        save as theDoc file name POSIX file "{output_pdf}" file format format PDF
        close theDoc saving no
    end tell
    '''
    subprocess.run(['osascript', '-e', script], capture_output=True, timeout=60)
    if os.path.exists(output_pdf):
        return output_pdf
    return None


def _resolve_merged_page(page_num, label_maps, volume_offsets):
    """Look up a record/appendix page number and return its page index in the merged PDF."""
    page_str = str(page_num)
    for vi, vol in enumerate(label_maps):
        label_map = vol.get('label_map')
        if not label_map:
            continue
        physical = label_map.get(page_str)
        if physical is not None:
            # physical is 1-based, volume_offsets[vi] is 0-based
            return volume_offsets[vi] + physical - 1
    return None


def add_hyperlinks_merged_pdf(brief_path, label_maps, citation_formats=None):
    """Merge brief + appendix volumes into one PDF with internal GOTO links.
    No external file references, no security warnings."""
    if citation_formats is None:
        citation_formats = ['bare']
    import fitz

    # Convert DOCX to PDF if needed
    brief_ext = os.path.splitext(brief_path)[1].lower()
    if brief_ext == '.docx':
        pdf_brief = _convert_docx_to_pdf(brief_path)
        if not pdf_brief:
            raise Exception('Failed to convert DOCX to PDF. Make sure Microsoft Word is installed.')
    else:
        pdf_brief = brief_path

    merged = fitz.open(pdf_brief)
    brief_page_count = len(merged)

    # Append each volume and track where it starts in the merged doc
    volume_offsets = []
    for vol in label_maps:
        vol_start = len(merged)
        volume_offsets.append(vol_start)
        vol_pdf_path = vol.get('pdf_path')
        if vol_pdf_path and os.path.exists(vol_pdf_path):
            vol_doc = fitz.open(vol_pdf_path)
            merged.insert_pdf(vol_doc)
            vol_doc.close()

    # Process citations in the brief pages only
    link_count = 0
    for page_idx in range(brief_page_count):
        page = merged[page_idx]
        text = page.get_text()
        citations = _find_all_citations(text, citation_formats)
        if not citations:
            continue

        for _, _, cite_text in citations:
            _, parts = _extract_prefix_and_pages(cite_text)
            first_page_match = re.match(r'(\d+)', parts[0])
            if not first_page_match:
                continue
            first_page = int(first_page_match.group(1))

            target_page = _resolve_merged_page(first_page, label_maps, volume_offsets)
            if target_page is None:
                continue

            rects = page.search_for(cite_text)
            if not rects:
                continue

            rect = rects[0]
            # Internal GOTO link — jumps within same PDF, zero security warnings
            link = {
                "kind": fitz.LINK_GOTO,
                "from": rect,
                "page": target_page,
                "to": fitz.Point(0, 0),
            }
            page.insert_link(link)
            link_count += 1

    base, ext = os.path.splitext(brief_path)
    output_path = base + '_hyperlinked_merged.pdf'
    merged.save(output_path)
    merged.close()

    # Clean up temp PDF if we converted from DOCX
    if brief_ext == '.docx' and pdf_brief != brief_path and os.path.exists(pdf_brief):
        os.unlink(pdf_brief)

    return output_path, link_count


def add_hyperlinks_to_pdf(pdf_path, label_maps, citation_formats=None):
    """Open an existing PDF and add hyperlinks to record/appendix citations."""
    if citation_formats is None:
        citation_formats = ['bare']
    import fitz

    doc = fitz.open(pdf_path)
    link_count = 0

    for page in doc:
        text = page.get_text()
        citations = _find_all_citations(text, citation_formats)
        if not citations:
            continue

        for _, _, cite_text in citations:
            _, parts = _extract_prefix_and_pages(cite_text)
            first_page = int(re.match(r'(\d+)', parts[0]).group(1))
            resolved = _resolve_url_via_labels(first_page, label_maps)
            if not resolved:
                continue

            rects = page.search_for(cite_text)
            if not rects:
                continue

            rect = rects[0]
            link = {
                "kind": fitz.LINK_URI,
                "from": rect,
                "uri": resolved['uri'],
            }
            page.insert_link(link)
            # Set /NewWindow true so the link opens in a new tab
            for annot in page.annots():
                if annot.type[0] == fitz.PDF_ANNOT_LINK:
                    annot_rect = annot.rect
                    if abs(annot_rect.x0 - rect.x0) < 1 and abs(annot_rect.y0 - rect.y0) < 1:
                        xref = annot.xref
                        doc.xref_set_key(xref, "A/NewWindow", "true")
                        break
            link_count += 1

    base, ext = os.path.splitext(pdf_path)
    output_path = base + '_hyperlinked' + ext
    doc.save(output_path)
    doc.close()
    return output_path, link_count


# ============ ROUTES ============

@hyperlinker_bp.route('/hyperlinker/hosted/<doc_id>')
def hyperlinker_serve_hosted(doc_id):
    """Serve a hosted appendix/record PDF for hyperlink access.
    URLs like /hyperlinker/hosted/abc123#page=11 open the PDF at page 11."""
    if doc_id not in _hosted_docs:
        return 'Document not found', 404
    pdf_path = _hosted_docs[doc_id]
    if not os.path.exists(pdf_path):
        return 'Document not found', 404
    return send_file(pdf_path, mimetype='application/pdf')

# Persistent storage for hosted documents: doc_id -> pdf_path
_hosted_docs = {}


def _host_volume_pdf(pdf_path):
    """Host a volume PDF and return the doc_id for URL construction."""
    import hashlib
    # Generate stable ID from file content
    with open(pdf_path, 'rb') as f:
        doc_id = hashlib.sha256(f.read(8192)).hexdigest()[:12]
    _hosted_docs[doc_id] = pdf_path
    return doc_id


@hyperlinker_bp.route('/hyperlinker')
def hyperlinker_page():
    """Render the hyperlinker tool page."""
    return render_template('hyperlinker.html')


@hyperlinker_bp.route('/hyperlinker/upload', methods=['POST'])
def hyperlinker_upload():
    """Accept a .docx or .pdf upload. If session_id is provided, replace the brief
    in the existing session (keeping volumes intact)."""
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

    # Check if replacing brief in existing session
    existing_session_id = request.form.get('session_id', '').strip()
    if existing_session_id and existing_session_id in _hyperlinker_sessions:
        session = _hyperlinker_sessions[existing_session_id]
        # Remove old brief file
        if os.path.exists(session['path']):
            os.unlink(session['path'])
        # Remove old processed file if any
        if session.get('processed_path') and os.path.exists(session['processed_path']):
            os.unlink(session['processed_path'])
        # Save new brief
        filename = secure_filename(f.filename)
        filepath = os.path.join(session['tmp_dir'], filename)
        f.save(filepath)
        session['path'] = filepath
        session['filename'] = filename
        session['file_type'] = file_type
        session['processed_path'] = None
        return jsonify({
            'session_id': existing_session_id,
            'filename': filename,
            'file_type': file_type,
            'replaced': True,
            'volume_count': len(session['volumes']),
        })

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

    doc_url = request.form.get('nyscef_url', '').strip() or request.form.get('doc_url', '').strip()
    court_system = request.form.get('court_system', 'nyscef').strip().lower()

    # NYSCEF requires a URL; other systems can use relative file links
    if not doc_url and court_system == 'nyscef':
        return jsonify({'error': 'NYSCEF URL is required'}), 400

    # Parse the URL based on court system
    doc_index = ''
    base_url = ''
    link_mode = 'url'  # 'url' or 'relative'
    if court_system == 'nyscef':
        # NYSCEF: extract docIndex from URL
        doc_index = doc_url
        if 'docIndex=' in doc_index:
            doc_index = doc_index.split('docIndex=')[1].split('#')[0].split('&')[0]
    elif doc_url:
        # External URL provided (any court system)
        court_system = 'pacer'
        base_url = doc_url
    else:
        # No URL — host the PDF and generate HTTPS links (like NYSCEF)
        link_mode = 'hosted'

    session = _hyperlinker_sessions[session_id]
    filename = secure_filename(f.filename)
    pdf_path = os.path.join(session['tmp_dir'], filename)
    f.save(pdf_path)

    label_map = _parse_pdf_page_labels(pdf_path)

    # If no page labels, build a label map from user-provided start page and offset
    start_page = request.form.get('start_page', '').strip()
    front_matter = int(request.form.get('front_matter', '0').strip() or '0')
    label_source = 'pdf_labels'

    if label_map is None:
        if not start_page:
            # Keep the PDF on disk and store path in session for retry
            session.setdefault('pending_volume', {
                'pdf_path': pdf_path,
                'filename': f.filename,
                'doc_url': doc_url,
                'doc_index': doc_index,
                'base_url': base_url,
                'court_system': court_system,
            })
            return jsonify({
                'error': 'no_labels',
                'message': f'{f.filename} has no page labels. Enter the first record/appendix page number in this volume and any front matter offset.',
            }), 400
        # Build label map from start page + offset
        import PyPDF2
        reader = PyPDF2.PdfReader(pdf_path)
        total_pages = len(reader.pages)
        start_num = int(start_page)
        label_map = {}
        for i in range(front_matter, total_pages):
            record_page = start_num + (i - front_matter)
            label_map[str(record_page)] = i + 1  # 1-based physical page
        label_source = 'manual_offset'

    vol_index = len(session['volumes'])
    vol_entry = {
        'pdf_path': pdf_path,
        'doc_url': doc_url,
        'doc_index': doc_index,
        'base_url': base_url,
        'court_system': court_system,
        'link_mode': link_mode,
        'filename': f.filename,
        'label_count': len(label_map),
        'label_source': label_source,
    }
    if label_source == 'manual_offset':
        vol_entry['start_page'] = int(start_page)
        vol_entry['front_matter_pages'] = front_matter
    session['volumes'].append(vol_entry)

    return jsonify({
        'success': True,
        'vol_index': vol_index,
        'filename': f.filename,
        'label_count': len(label_map),
        'label_source': label_source,
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
        # First try to use the stored label map from upload (covers manual offset case)
        if vol.get('label_source') == 'manual_offset':
            # Rebuild the label map from stored offset params
            import PyPDF2
            reader = PyPDF2.PdfReader(vol['pdf_path'])
            total_pages = len(reader.pages)
            start_num = vol.get('start_page', 1)
            front_matter = vol.get('front_matter_pages', 0)
            label_map = {}
            for i in range(front_matter, total_pages):
                record_page = start_num + (i - front_matter)
                label_map[str(record_page)] = i + 1
        else:
            try:
                label_map = _parse_pdf_page_labels(vol['pdf_path'])
            except Exception as e:
                return jsonify({'error': f'Failed to parse {vol["filename"]}: {str(e)}'}), 500

            if label_map is None:
                return jsonify({'error': f'{vol["filename"]} has no page labels.'}), 400

        # If hosted mode, register the PDF for serving
        link_mode = vol.get('link_mode', 'url')
        hosted_id = ''
        if link_mode == 'hosted' and vol.get('pdf_path'):
            hosted_id = _host_volume_pdf(vol['pdf_path'])

        # Determine host base URL from request
        host_base_url = request.host_url.rstrip('/')

        label_maps.append({
            'doc_index': vol.get('doc_index', ''),
            'base_url': vol.get('base_url', ''),
            'court_system': vol.get('court_system', 'nyscef'),
            'link_mode': link_mode,
            'hosted_id': hosted_id,
            'host_base_url': host_base_url,
            'filename': vol.get('filename', ''),
            'pdf_path': vol.get('pdf_path', ''),
            'label_map': label_map,
        })

    citation_formats = data.get('citation_formats', ['bare'])
    if not citation_formats:
        citation_formats = ['bare']

    try:
        if session.get('file_type') == 'pdf':
            output_path, link_count = add_hyperlinks_to_pdf(session['path'], label_maps, citation_formats)
            session['processed_path'] = output_path
        else:
            output_path, link_count = add_hyperlinks_to_docx(session['path'], label_maps, citation_formats)
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
