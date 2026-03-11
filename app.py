#!/usr/bin/env python3
"""
Brief Drafter
Drafts appellate briefs (Appellant's, Respondent's, and Reply) using AI assistance
"""

import os
import re
import json
import fcntl
import uuid
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, redirect, session
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Module imports
from src.config import PROJECTS_DIR, BRIEF_TYPE_CONFIG, ALLOWED_EXTENSIONS, MAX_TOTAL_CHARS, MAX_PRIMARY_CHARS, MAX_SECONDARY_CHARS, SUMMARIZER_JOBS_DIR, config as app_config
from src.project_io import get_project, save_project, extract_text, allowed_file
from src.text_processing import _strip_opposing_brief_chrome, _truncate, _fit_documents, _extract_search_terms, _search_record_pages
from src.claude_client import call_claude, call_claude_with_docs
from src.guardrails import validate_citations, enforce_paragraph_cites, enforce_case_cites, guardrail_brief, _replace_party_surname, count_brief_metrics, validate_revision_integrity, validate_supplement_integrity
from src.prompt_builders import (
    _build_drafting_protocol, _build_anti_hallucination_block,
    _build_writing_style, _build_exemplars, _build_structure_prompt,
    _strip_attorney_names, _build_party_label_constraint,
    build_intro_task, build_argument_task, build_conclusion_task,
    build_facts_task, build_procedural_history_task, build_expert_opinions_task,
    build_custom_section_task, build_revision_prompt, build_supplement_prompt,
)
from src.document_gathering import (
    _gather_additional_docs, _gather_respondent_briefs, _preprocess_opening_brief,
    _gather_record_volumes, _gather_legal_research, build_doc_items_for_brief_type,
)
from src.analysis import _parse_analysis_json, _analyze_for_appellant, _analyze_for_respondent, _analyze_for_reply
from src.record_indexing import _format_record_index_for_prompt, _extract_record_evidence, _extract_transcript_quotes
from src.drafting_engine import (
    _draft_appellant_brief, _draft_respondent_brief, _draft_reply_brief,
)
from src.docx_generator import generate_brief_docx, generate_section_docx, resolve_nyscef_url
from src.routes.hyperlinker import hyperlinker_bp
from src.routes.dropbox_routes import dropbox_bp, get_dropbox_shared_link
from src.routes.summarization import summarization_bp
from src.routes.witness import witness_bp, _build_witness_constraint_for_project

load_dotenv()

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Register blueprints
app.register_blueprint(hyperlinker_bp)
app.register_blueprint(dropbox_bp)
app.register_blueprint(summarization_bp)
app.register_blueprint(witness_bp)

# Ensure projects directory exists
PROJECTS_DIR.mkdir(exist_ok=True)


# ============ ROUTES ============

@app.route('/')
def index():
    """Main page - list projects or create new"""
    projects = []
    if PROJECTS_DIR.exists():
        for p in PROJECTS_DIR.iterdir():
            if p.is_dir() and (p / 'project.json').exists():
                proj = get_project(p.name)
                if proj:
                    bt = proj.get('brief_type', 'reply')
                    projects.append({
                        'id': p.name,
                        'case_name': proj.get('case_name', 'Untitled'),
                        'created': proj.get('created', ''),
                        'status': proj.get('status', 'draft'),
                        'brief_type': bt,
                        'brief_type_label': BRIEF_TYPE_CONFIG.get(bt, {}).get('label', 'Reply Brief'),
                    })

    projects.sort(key=lambda x: x.get('created', ''), reverse=True)
    return render_template('index.html', projects=projects)


@app.route('/project/new', methods=['POST'])
def create_project():
    """Create new brief project"""
    data = request.json or {}

    brief_type = data.get('brief_type', 'reply')
    if brief_type not in BRIEF_TYPE_CONFIG:
        brief_type = 'reply'

    # Determine representing based on brief type
    if brief_type == 'respondent':
        representing = 'respondent'
    else:
        representing = 'appellant'

    project_id = str(uuid.uuid4())[:8]
    project_data = {
        'id': project_id,
        'brief_type': brief_type,
        'representing': representing,
        'case_name': data.get('case_name', 'New Case'),
        'court': data.get('court', ''),
        'docket_number': data.get('docket_number', ''),
        'appellant': data.get('appellant', ''),
        'respondent': data.get('respondent', ''),
        'attorney_name': data.get('attorney_name', ''),
        'attorney_firm': data.get('attorney_firm', ''),
        'created': datetime.now().isoformat(),
        'status': 'uploading',
        'documents': {},
        'analysis': None,
        'drafted_sections': {}
    }

    # Create project directory
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(exist_ok=True)
    (project_dir / 'uploads').mkdir(exist_ok=True)

    save_project(project_id, project_data)

    return jsonify({'project_id': project_id})


@app.route('/project/<project_id>')
def project_workspace(project_id):
    """Project workspace page"""
    project = get_project(project_id)
    if not project:
        return "Project not found", 404
    brief_type = project.get('brief_type', 'reply')
    config = BRIEF_TYPE_CONFIG.get(brief_type, BRIEF_TYPE_CONFIG['reply'])
    return render_template('workspace.html', project=project, config=config)


@app.route('/project/<project_id>/upload', methods=['POST'])
def upload_document(project_id):
    """Upload a document to the project"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    doc_type = request.form.get('doc_type', 'other')
    issue_name = request.form.get('issue_name', '').strip()

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed. Use PDF, DOCX, or TXT'}), 400

    # Save file
    filename = secure_filename(file.filename)
    upload_dir = PROJECTS_DIR / project_id / 'uploads'
    file_path = upload_dir / f"{doc_type}_{filename}"
    file.save(file_path)

    # Extract text
    text = extract_text(file_path)

    # Lock, re-read, update, save — prevents concurrent uploads from clobbering each other
    project_dir = PROJECTS_DIR / project_id
    lock_file = project_dir / '.project.lock'
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # Re-read project inside lock to get latest state
            project = get_project(project_id)

            # Delete old file if re-uploading same doc_type
            if doc_type in project.get('documents', {}):
                old_path = project['documents'][doc_type].get('path', '')
                if old_path and Path(old_path).exists() and Path(old_path) != file_path:
                    try:
                        Path(old_path).unlink()
                    except OSError:
                        pass

            project['documents'][doc_type] = {
                'filename': filename,
                'path': str(file_path),
                'text': text,
                'char_count': len(text)
            }

            # Store case law issue grouping
            if issue_name and (doc_type == 'legal_research' or doc_type.startswith('legal_research_')):
                if 'case_law_issues' not in project:
                    project['case_law_issues'] = {}
                project['case_law_issues'][doc_type] = issue_name

            # If existing_draft, also save it as the full_brief so revise works immediately
            if doc_type == 'existing_draft':
                if 'drafted_sections' not in project:
                    project['drafted_sections'] = {}
                project['drafted_sections']['full_brief'] = {
                    'content': text,
                    'drafted_at': datetime.now().isoformat(),
                    'source': 'uploaded'
                }

            with open(project_dir / 'project.json', 'w') as f:
                json.dump(project, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    # Attempt Dropbox shared link generation
    dropbox_link = get_dropbox_shared_link(filename)
    if dropbox_link:
        fresh = get_project(project_id)
        fresh['documents'][doc_type]['dropbox_link'] = dropbox_link
        save_project(project_id, fresh)
        print(f"[DROPBOX] Shared link for {filename}: {dropbox_link}", flush=True)

    response = {
        'success': True,
        'doc_type': doc_type,
        'filename': filename,
        'char_count': len(text)
    }
    if dropbox_link:
        response['dropbox_link'] = dropbox_link

    # Include text for existing_draft so frontend can display it immediately
    if doc_type == 'existing_draft':
        response['text'] = text

    return jsonify(response)


@app.route('/project/<project_id>/documents')
def list_documents(project_id):
    """List uploaded documents"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    docs = []
    for doc_type, doc_info in project.get('documents', {}).items():
        docs.append({
            'type': doc_type,
            'filename': doc_info.get('filename'),
            'char_count': doc_info.get('char_count', 0)
        })

    return jsonify({'documents': docs})


@app.route('/project/<project_id>/delete-document', methods=['POST'])
def delete_document(project_id):
    """Delete an uploaded document from the project"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    doc_type = request.json.get('doc_type')
    if not doc_type:
        return jsonify({'error': 'No doc_type provided'}), 400

    project_dir = PROJECTS_DIR / project_id
    lock_file = project_dir / '.project.lock'
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            project = get_project(project_id)
            doc_info = project.get('documents', {}).get(doc_type)
            if not doc_info:
                return jsonify({'error': f'Document {doc_type} not found'}), 404

            # Delete the file from disk
            file_path = doc_info.get('path', '')
            if file_path and os.path.exists(file_path):
                os.remove(file_path)

            # Remove from project data
            del project['documents'][doc_type]

            with open(project_dir / 'project.json', 'w') as f:
                json.dump(project, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    return jsonify({'success': True, 'doc_type': doc_type})


# ── Import from Transcript Summarizer ──────────────────────────

@app.route('/project/<project_id>/import-summaries')
def import_summaries(project_id):
    """List completed Transcript Summarizer jobs available for import."""
    jobs = []
    if SUMMARIZER_JOBS_DIR.exists():
        for jf in sorted(SUMMARIZER_JOBS_DIR.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                jdata = json.loads(jf.read_text())
                if jdata.get('status') != 'completed':
                    continue
                jobs.append({
                    'job_id': jdata.get('job_id', jf.stem),
                    'filename': jdata.get('filename', ''),
                    'document_type': jdata.get('document_type', ''),
                    'citation_format': jdata.get('citation_format', ''),
                    'pages_processed': jdata.get('pages_processed', 0),
                    'completion_time': jdata.get('completion_time', ''),
                    'citation_count': jdata.get('citation_count', 0),
                    'has_narrative': bool(jdata.get('narrative_text')),
                    'char_count': len(jdata.get('narrative_text', '')),
                    'has_filtered': bool(jdata.get('filtered_narrative')),
                    'filtered_char_count': len(jdata.get('filtered_narrative', '')),
                    'filter_prompt': jdata.get('filter_prompt', ''),
                })
            except Exception:
                continue
    return jsonify({'jobs': jobs})


@app.route('/project/<project_id>/import-summary', methods=['POST'])
def import_summary(project_id):
    """Import a Transcript Summarizer job output as a project document."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    job_id = data.get('job_id')
    doc_type = data.get('doc_type', 'trial_transcript')
    if not job_id:
        return jsonify({'error': 'No job_id provided'}), 400

    job_path = SUMMARIZER_JOBS_DIR / f'{job_id}.json'
    if not job_path.exists():
        return jsonify({'error': 'Job not found'}), 404

    jdata = json.loads(job_path.read_text())
    if jdata.get('status') != 'completed':
        return jsonify({'error': 'Job not yet completed'}), 400

    # Get narrative text — use filtered version if requested, fall back to full
    use_filtered = data.get('use_filtered', False)
    if use_filtered and jdata.get('filtered_narrative'):
        narrative = jdata['filtered_narrative']
    else:
        narrative = jdata.get('narrative_text', '')
    if not narrative and jdata.get('output_file'):
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(jdata['output_file'])
            narrative = '\n\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            return jsonify({'error': f'Could not extract text from DOCX: {e}'}), 500

    if not narrative:
        return jsonify({'error': 'No narrative text available in this job'}), 400

    fname = jdata.get('filename', 'Transcript')
    # Extract clean deponent name from filename
    import re as _re
    deponent_match = _re.search(r'DEPOSITION_OF_([A-Z_]+?)_DATED', fname)
    if deponent_match:
        deponent = deponent_match.group(1).replace('_', ' ').title()
    else:
        deponent = fname.rsplit('.', 1)[0].replace('_', ' ')

    project_dir = PROJECTS_DIR / project_id
    lock_file = project_dir / '.project.lock'
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            project = get_project(project_id)
            # Always use transcript_digest_N slots for summarizer imports
            n = 1
            while f'transcript_digest_{n}' in project['documents']:
                n += 1
            doc_type = f'transcript_digest_{n}'
            project['documents'][doc_type] = {
                'filename': f'{deponent} ({"Filtered" if use_filtered else "Full"})',
                'path': f'imported_from_summarizer:{job_id}',
                'text': narrative,
                'char_count': len(narrative),
                'source': 'transcript_summarizer',
                'source_job_id': job_id,
                'citation_format': jdata.get('citation_format', ''),
                'deponent_name': deponent,
            }
            with open(project_dir / 'project.json', 'w') as f:
                json.dump(project, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    print(f"[BRIEF] Imported summary for '{deponent}' as {doc_type} ({len(narrative):,} chars)", flush=True)

    return jsonify({
        'success': True,
        'doc_type': doc_type,
        'filename': f'{deponent} (Summary).txt',
        'char_count': len(narrative),
        'deponent_name': deponent,
    })


@app.route('/project/<project_id>/analyze', methods=['POST'])
def analyze_arguments(project_id):
    """Analyze documents based on brief type"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    docs = project.get('documents', {})
    brief_type = project.get('brief_type', 'reply')

    # Validate required documents per brief type
    if brief_type == 'appellant':
        if 'lower_court_decision' not in docs:
            return jsonify({'error': 'Lower court decision not uploaded'}), 400
    elif brief_type == 'respondent':
        if 'appellant_brief' not in docs:
            return jsonify({'error': "Appellant's brief not uploaded"}), 400
    else:  # reply
        if 'respondent_brief' not in docs:
            return jsonify({'error': "Respondent's brief not uploaded"}), 400
        if 'opening_brief' not in docs:
            return jsonify({'error': 'Opening brief not uploaded'}), 400

    # Dispatch to type-specific analysis
    cli = project.get('case_law_issues', {})
    if brief_type == 'appellant':
        result = _analyze_for_appellant(docs, cli)
    elif brief_type == 'respondent':
        result = _analyze_for_respondent(docs, cli)
    else:
        result = _analyze_for_reply(docs)

    analysis = _parse_analysis_json(result)

    # Re-read project from disk to avoid overwriting concurrent changes
    fresh_project = get_project(project_id)
    fresh_project['analysis'] = analysis
    fresh_project['status'] = 'analyzed'
    save_project(project_id, fresh_project)

    return jsonify(analysis)


@app.route('/project/<project_id>/structure', methods=['POST'])
def save_structure(project_id):
    """Save attorney-defined brief structure"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    structure = {
        'preliminary_statement': data.get('preliminary_statement', ''),
        'procedural_history': data.get('procedural_history', ''),
        'factual_background': data.get('factual_background', ''),
        'points': [],
    }

    for i, pt in enumerate(data.get('points', []), 1):
        structure['points'].append({
            'id': i,
            'heading': pt.get('heading', ''),
            'argument_description': pt.get('argument_description', ''),
            'facts': pt.get('facts', ''),
            'cases': pt.get('cases', ''),
        })

    project['brief_structure'] = structure
    save_project(project_id, project)

    return jsonify({'success': True, 'point_count': len(structure['points'])})


@app.route('/project/<project_id>/structure')
def get_structure(project_id):
    """Return saved brief structure (or null)"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    structure = project.get('brief_structure')
    return jsonify({'structure': structure})


@app.route('/project/<project_id>/nyscef-config', methods=['POST'])
def save_nyscef_config(project_id):
    """Save NYSCEF hyperlink configuration for record volumes"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    volumes = []
    for vol in data.get('volumes', []):
        raw_url = vol.get('url', vol.get('doc_index', '')).strip()
        doc_index = raw_url
        if 'docIndex=' in doc_index:
            doc_index = doc_index.split('docIndex=')[1].split('#')[0].split('&')[0]
        volumes.append({
            'doc_key': vol.get('doc_key', ''),
            'url': raw_url,
            'doc_index': doc_index,
            'first_page': int(vol.get('first_page', 1)),
            'page_offset': int(vol.get('page_offset', 1)),
        })

    project['nyscef_config'] = {'volumes': volumes}
    save_project(project_id, project)
    return jsonify({'success': True, 'volume_count': len(volumes)})


@app.route('/project/<project_id>/nyscef-config')
def get_nyscef_config(project_id):
    """Return saved NYSCEF config (or null)"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify({'nyscef_config': project.get('nyscef_config')})


@app.route('/project/<project_id>/draft', methods=['POST'])
def draft_section(project_id):
    """Draft a section of the brief (type-aware)"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    section_type = data.get('section_type', 'argument')
    argument_number = data.get('argument_number', 1)
    custom_instructions = data.get('custom_instructions', '').strip()
    selected_docs = data.get('selected_docs', None)
    model = data.get('model', 'sonnet')
    brief_type = project.get('brief_type', 'reply')

    docs = project.get('documents', {})
    # Filter to selected docs when provided (Statement of Facts / Procedural History)
    if selected_docs is not None:
        working_docs = {k: v for k, v in docs.items() if k in selected_docs}
    else:
        working_docs = docs
    analysis = project.get('analysis') or {}
    record_combined = _gather_record_volumes(working_docs)
    research_text = _gather_legal_research(working_docs, project.get('case_law_issues', {}))

    # Build argument info — prefer structure Points over analysis
    argument_info = ""
    structure = project.get('brief_structure')
    structure_points = structure.get('points', []) if structure else []
    analysis_items = analysis.get('arguments') or analysis.get('errors') or analysis.get('weaknesses') or []

    if section_type == 'argument' and structure_points and 0 < argument_number <= len(structure_points):
        pt = structure_points[argument_number - 1]
        argument_info = f"""
ARGUMENT TO DRAFT (from attorney's brief structure):
Heading: {pt.get('heading', '')}
Argument: {pt.get('argument_description', '')}
Key Facts: {pt.get('facts', '')}
Key Cases: {pt.get('cases', '')}

Draft ONLY this Point. Use ONLY the facts and cases listed above plus what you find in the uploaded documents.
"""
    elif section_type == 'argument' and analysis_items:
        if 0 < argument_number <= len(analysis_items):
            arg = analysis_items[argument_number - 1]
            if brief_type == 'appellant':
                argument_info = f"""
ARGUMENT TO DRAFT:
Title: {arg.get('title', arg.get('issue', ''))}
Error: {arg.get('error_description', '')}
Correct Standard: {arg.get('correct_standard', '')}
Standard of Review: {arg.get('standard_of_review', '')}
Preservation: {arg.get('preservation', '')}
Strategy: {arg.get('reply_strategy', '')}
"""
            elif brief_type == 'respondent':
                argument_info = f"""
ARGUMENT TO RESPOND TO:
Title: {arg.get('title', '')}
Appellant Argues: {arg.get('appellant_argument', '')}
Weakness in Their Argument: {arg.get('weakness', '')}
Response Strategy: {arg.get('response_strategy', '')}
"""
            else:  # reply
                argument_info = f"""
ARGUMENT TO ADDRESS:
Title: {arg.get('title', '')}
Your Original Argument (from opening brief): {arg.get('appellant_argument', arg.get('summary', ''))}
Respondent's Counter-Argument: {arg.get('respondent_counter', '')}
Weaknesses to Exploit in Reply: {arg.get('weaknesses', '')}
"""

    # Search large records for relevant pages instead of truncating from the top
    if record_combined and len(record_combined) > MAX_TOTAL_CHARS // 2:
        if section_type == 'custom':
            search_text = custom_instructions
        elif section_type == 'argument':
            search_text = argument_info
        elif section_type in ('facts', 'procedural_history'):
            # Broad search — facts need everything, procedural needs dates/filings
            search_text = project.get('case_name', '')
            if structure_points:
                search_text += ' ' + ' '.join(pt.get('heading', '') for pt in structure_points)
            if section_type == 'procedural_history':
                search_text += ' motion order decision filed commenced action trial hearing'
        else:  # intro, conclusion — broad search across all point headings
            search_text = project.get('case_name', '')
            if structure_points:
                search_text += ' ' + ' '.join(pt.get('heading', '') for pt in structure_points)
        if search_text:
            terms = _extract_search_terms(search_text)
            if terms:
                record_combined = _search_record_pages(record_combined, terms, MAX_TOTAL_CHARS // 2)

    # Build document context based on brief type, with truncation
    brief_role_map = {'appellant': "an appellant's opening brief", 'respondent': "a respondent's brief"}
    brief_role = brief_role_map.get(brief_type, "a reply brief")
    doc_items = build_doc_items_for_brief_type(working_docs, record_combined, research_text, brief_type)

    fitted = _fit_documents(doc_items)
    doc_context = "\n\n".join(f"--- {label} ---\n{text if text else '(Not uploaded)'}" for label, text in fitted)

    # Pre-process opening brief constraints for reply briefs (skip for facts/procedural_history)
    reply_constraints = ''
    if brief_type == 'reply' and section_type not in ('facts', 'procedural_history'):
        full_opening = docs.get('opening_brief', {}).get('text', '')
        if full_opening:
            reply_constraints = _preprocess_opening_brief(full_opening)

    # Build task instruction
    if section_type == 'intro':
        print(f"[DRAFT] *** NEW INTRO PROMPT ACTIVE *** section_type={section_type}, brief_type={brief_type}", flush=True)
        task = build_intro_task(brief_type)
    elif section_type == 'argument':
        task = build_argument_task(argument_number)
    elif section_type == 'conclusion':
        task = build_conclusion_task(brief_type)
    elif section_type == 'facts':
        task = build_facts_task(brief_type, custom_instructions)
    elif section_type == 'procedural_history':
        task = build_procedural_history_task(custom_instructions)
    elif section_type == 'experts':
        task = build_expert_opinions_task(custom_instructions)
    elif section_type == 'custom':
        task = build_custom_section_task(custom_instructions)
    else:
        task = ""

    # Inject record index for facts section — but only when record volumes are selected
    # (the index is 787K+ chars for large records and blows the token budget when
    # drafting from source documents alone)
    record_index_block = ''
    if section_type == 'facts' and record_combined:
        record_index = project.get('record_index', [])
        if record_index:
            record_index_block = _format_record_index_for_prompt(record_index)

    if section_type == 'procedural_history':
        prompt = f"""You are a senior appellate attorney preparing a PROCEDURAL HISTORY section for an appellate brief. Your job is to produce a THOROUGH, DETAILED account of the motion practice in this case. This is not a summary — it is a comprehensive narrative of the litigation.

DEPTH AND THOROUGHNESS — THIS IS THE #1 PRIORITY:
For EACH motion or cross-motion, you MUST cover ALL of the following in detail:
1. THE MOTION: What relief was sought, what legal grounds were asserted, what specific arguments the moving party made. Devote 2-4 substantive paragraphs to each motion. Describe EACH argument with specificity — do not generalize. If the moving party argued three separate grounds for summary judgment, describe all three.
2. THE OPPOSITION: What arguments plaintiff/defendant made in response. Cover EACH counter-argument with the same detail as the motion. If the opposing party raised issues of fact, describe what those factual disputes were. If an expert was submitted, state the expert's name, credentials, and the substance of their opinions (not just "plaintiff submitted an expert").
3. THE REPLY: What the moving party argued in reply, particularly any new arguments or responses to the opposition's expert evidence.
4. THE COURT'S DECISION: What the court ruled on each branch of the motion and the court's reasoning for each ruling. If the court granted in part and denied in part, cover each part separately.

A superficial procedural history that glosses over arguments with vague summaries like "defendants argued they were entitled to summary judgment" is UNACCEPTABLE. You must describe WHAT specifically they argued and WHY.

STRUCTURE — ORGANIZE BY MOTION:
Use subheadings to organize the procedural history by motion/cross-motion. Under each motion, present the motion, opposition, reply, and ruling in that order. Example structure:
- Defendants Clinton Hill's Motion for Summary Judgment
  (motion arguments, opposition arguments, reply arguments, court ruling)
- Defendants Hoffman and Elrauch's Motion for Summary Judgment
  (motion arguments, opposition arguments, reply arguments, court ruling)

MANDATORY STYLE RULES:
- NEVER include attorney names. Refer to parties only as "plaintiff," "defendants," or "the court."
- Dates: include ONLY for (1) motions/cross-motions, (2) court orders, (3) procedural events (complaint, note of issue, depositions). No dates for opposition or reply papers.
- CORRECT: "In opposition, plaintiff argued that..."
- INCORRECT: "In his affirmation dated July 17, 2024, Brian J. Isaac, Esq. argued that..."

CRITICAL DISTINCTION — DO NOT VIOLATE:
A Procedural History describes the LITIGATION PROCESS: motions filed, arguments made in papers, court rulings.
A Statement of Facts describes the UNDERLYING EVENTS: the accident, the building, the lease terms.
YOU MUST frame everything as litigation activity: "Defendants argued that..." "Plaintiff submitted..." "The court found..."
YOU MUST NOT narrate accident facts or describe physical conditions as standalone facts — only in the context of "Party X argued that..." or "Party X's expert opined that..."

DO NOT list documents submitted. NEVER catalog papers a party filed. Go straight to WHAT they argued.

SUBHEADINGS: Do NOT put record page ranges in subheadings. Cites belong in the body text.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

MOTION PAPERS AND COURT FILINGS: Provided as structured document blocks for citation tracking. READ EVERY DOCUMENT CAREFULLY. Extract the specific arguments, not just the general topic.

{_build_anti_hallucination_block().replace('[CITE NEEDED]', '(record page number)').replace('[FULL CITE NEEDED]', '(record page number)').replace('[CASE CITE NEEDED]', '(case citation from source documents)')}

{_build_drafting_protocol().replace('[CITE NEEDED]', '(record page number)').replace('[FULL CITE NEEDED]', '(record page number)').replace('[CASE CITE NEEDED]', '(case citation from source documents)')}

{_build_writing_style().replace('[CITE NEEDED]', '(record page number)').replace('[FULL CITE NEEDED]', '(record page number)').replace('[CASE CITE NEEDED]', '(case citation from source documents)')}

{task}

RECORD CITATION FORMAT: Bare parenthetical record page numbers only — (19) or (652) or (979-993). No "R." prefix. No "at p." Preserve exact page numbers from source documents.

CASE LAW CITATION FORMAT — NEW YORK OFFICIAL:
- NO periods in reporters: AD2d, AD3d, NY2d, NY3d, Misc 2d, Misc 3d — NEVER A.D.2d or N.Y.2d
- BRACKETS for court/year, NOT parentheses: [1st Dept. 2002] — NEVER (1st Dept 2002)
- "Dept." takes a period: [1st Dept. 2002], [2d Dept. 2020]
- CORRECT: _Alloway v. 715 Riverside_, 298 AD2d 148 [1st Dept. 2002]
- WRONG: Alloway v. 715 Riverside, 298 A.D.2d 148 (1st Dept 2002)

NEVER output [CITE NEEDED] or [FULL CITE NEEDED] or any bracketed placeholder. This is ABSOLUTELY PROHIBITED. Transitional and structural sentences (e.g., "Defendants advanced two arguments in support of their motion") do NOT need citations — just write them without a cite. Substantive factual assertions about what a party argued or what the court found MUST have a record page cite from the source documents. You have all the source documents — there is no reason for any placeholder.

FORMATTING: Output PLAIN TEXT ONLY. NO markdown (no ##, no **, no *, no bold). Section headings and subheadings in ALL CAPS on their own line. Tab-indent body paragraphs. Case names with _underscores_.

LENGTH: This section should be LONG and THOROUGH — at least 2,000 words. Do not summarize or abbreviate. Cover every argument made by every party on every motion.

{_build_party_label_constraint(project)}
Draft the Procedural History now. Be EXHAUSTIVE. Cover every motion, every argument, every opposition, every reply, and every ruling in detail."""
    else:
        drafting_protocol = '' if section_type in ('facts', 'experts', 'intro') else _build_drafting_protocol()
        writing_style = _build_writing_style()
        if section_type in ('facts', 'experts', 'intro'):
            writing_style = writing_style.replace('[CITE NEEDED]', '(record page number)').replace('[FULL CITE NEEDED]', '(record page number)').replace('[CASE CITE NEEDED]', '(record page number)')

        prompt = f"""You are an expert appellate attorney drafting {brief_role}.

{reply_constraints}

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{argument_info}

{record_index_block}

{_build_anti_hallucination_block()}

{drafting_protocol}

{writing_style}

{_build_exemplars(brief_type)}

{task}

{"" if section_type in ('facts', 'experts', 'intro') else "REMINDER: Use ONLY cases and legal authorities found in the uploaded documents. NO outside research. If you need a case not in the documents, write [CASE CITE NEEDED]. Never fabricate citations."}

FORMATTING REMINDER: Output PLAIN TEXT ONLY. NO markdown (no ##, no **, no *). Tab-indent body paragraphs. Section headings in ALL CAPS on their own line. Point headings: "POINT I" on one line, heading text in ALL CAPS on next line. Case names with _underscores_.

{_build_party_label_constraint(project)}
Draft the section now:"""

    if section_type == 'custom':
        max_tok = 8000
    elif section_type in ('facts', 'experts'):
        max_tok = 32000
    elif section_type == 'procedural_history':
        max_tok = 32000
    elif section_type == 'argument':
        max_tok = 8000
    else:
        max_tok = 4000

    # Build document blocks from fitted sources for citation tracking
    section_docs = [{"text": text, "title": label} for label, text in fitted if text]
    total_doc_chars = sum(len(d["text"]) for d in section_docs)
    print(f"[DRAFT] section={section_type}, prompt_len={len(prompt)}, doc_count={len(section_docs)}, "
          f"doc_chars={total_doc_chars:,}, total_chars={len(prompt)+total_doc_chars:,}, "
          f"est_tokens={((len(prompt)+total_doc_chars)//4):,}", flush=True)
    for d in section_docs:
        print(f"  [{d['title'][:50]}] {len(d['text']):,} chars", flush=True)

    # For facts/experts: embed documents inline so Claude reads page markers directly.
    # The Citations API (call_claude_with_docs) is counterproductive here — we need
    # Claude to find "--- PAGE XXXX ---" markers and cite those page numbers.
    if section_type in ('facts', 'experts', 'intro', 'procedural_history') and section_docs:
        inline_docs = "\n\n".join(f"=== {d['title']} ===\n{d['text']}" for d in section_docs)
        full_prompt = f"{prompt}\n\n{inline_docs}"
        print(f"[DRAFT] Using inline docs for {section_type}, full_prompt_len={len(full_prompt):,}", flush=True)
        result = call_claude(full_prompt, max_tokens=max_tok, model=model)
        section_citations = []
    elif section_docs:
        result, section_citations = call_claude_with_docs(prompt, section_docs, max_tokens=max_tok, model=model)
        print(f"[CITATIONS] draft_section({section_type}) returned {len(section_citations)} source citations", flush=True)
    else:
        result = call_claude(prompt, max_tokens=max_tok, model=model)

    # Strip attorney names and citation placeholders from procedural history output
    if section_type == 'procedural_history':
        result = _strip_attorney_names(result)
        result = re.sub(r'\s*\[CITE NEEDED\]', '', result)
        result = re.sub(r'\s*\[FULL CITE NEEDED\]', '', result)
        result = re.sub(r'\s*\[CASE CITE NEEDED\]', '', result)

    # Replace party surname with party label (e.g., "Batchilly" -> "plaintiff")
    result = _replace_party_surname(result, project)

    # Convert any bold case names to underscore format
    result = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', result)

    # Flag paragraphs where last sentence is missing a record cite
    # Strip any [CITE NEEDED] placeholders from facts/experts/intro output
    if section_type in ('facts', 'experts', 'intro'):
        result = re.sub(r'\s*\[(?:CITE|FULL CITE|CASE CITE) NEEDED\]\.?', '.', result)
        result = re.sub(r'\.\.', '.', result)

    # Skip for argument/custom/facts/experts/intro/procedural_history — these cite per-fact not per-paragraph
    if section_type not in ('argument', 'custom', 'facts', 'experts', 'intro', 'procedural_history'):
        result = enforce_paragraph_cites(result)

    # Insert full case citations from uploaded legal research (skip procedural history)
    if section_type != 'procedural_history':
        result = enforce_case_cites(result, research_text)

    # Citation validation — checks case names AND reporter numbers against sources (skip procedural history)
    if section_type != 'procedural_history':
        source_texts = [text for label, text in fitted if text]
        result = validate_citations(result, *source_texts)

    # Strip all validation artifacts from argument sections
    if section_type in ('argument', 'custom'):
        cite_needed_count = len(re.findall(r'\[CITE NEEDED\]', result))
        result = re.sub(r'\s*\[CITE NEEDED\]\.?', '.', result)
        result = re.sub(r'\s*\[CASE CITE NEEDED\]\.?', '.', result)
        result = re.sub(r'\s*\[FULL CITE NEEDED\]\.?', '.', result)
        result = re.sub(r'\s*\[UNVERIFIED CITATION\]\.?', '', result)
        result = re.sub(r'\s*\[CITE NUMBER UNVERIFIED\]\.?', '', result)
        result = re.sub(r'\s*\[VERIFY\]\.?', '.', result)
        result = re.sub(r'\.\.', '.', result)
        if cite_needed_count > 0:
            print(f"[WARN] Stripped {cite_needed_count} [CITE NEEDED] tags from {section_type} section", flush=True)

    section_key = f"{section_type}_{argument_number}" if section_type == 'argument' else section_type
    # Re-read project from disk to avoid overwriting concurrent changes
    fresh_project = get_project(project_id)
    if 'drafted_sections' not in fresh_project:
        fresh_project['drafted_sections'] = {}
    fresh_project['drafted_sections'][section_key] = {
        'content': result,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, fresh_project)

    return jsonify({
        'section': section_key,
        'content': result
    })


@app.route('/project/<project_id>/draft-all', methods=['POST'])
def draft_entire_brief(project_id):
    """Draft the entire brief using multi-pass approach (dispatches by brief type)"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    drafting_instructions = data.get('drafting_instructions', '').strip()

    # Save instructions to project for reference
    if drafting_instructions:
        project['drafting_instructions'] = drafting_instructions
        save_project(project_id, project)

    model = data.get('model', 'sonnet')
    docs = project.get('documents', {})
    brief_type = project.get('brief_type', 'reply')

    if brief_type == 'appellant':
        final_brief, research = _draft_appellant_brief(project, docs, drafting_instructions, model=model)
    elif brief_type == 'respondent':
        final_brief, research = _draft_respondent_brief(project, docs, drafting_instructions, model=model)
    else:
        final_brief, research = _draft_reply_brief(project, docs, drafting_instructions, model=model)

    # Re-read project from disk to avoid overwriting concurrent changes
    fresh_project = get_project(project_id)
    if 'drafted_sections' not in fresh_project:
        fresh_project['drafted_sections'] = {}
    for key, value in research.items():
        fresh_project['drafted_sections'][key] = value
    fresh_project['drafted_sections']['full_brief'] = {
        'content': final_brief,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, fresh_project)

    return jsonify({
        'full_brief': final_brief,
        'research': research
    })


@app.route('/project/<project_id>/revise', methods=['POST'])
def revise_brief(project_id):
    """Revise an existing drafted brief with targeted instructions"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    sections = project.get('drafted_sections', {})
    if 'full_brief' not in sections or not sections['full_brief'].get('content'):
        return jsonify({'error': 'No draft to revise. Draft the brief first.'}), 400

    data = request.json or {}
    revision_instructions = data.get('revision_instructions', '').strip()
    if not revision_instructions:
        return jsonify({'error': 'Revision instructions are required'}), 400

    existing_brief = sections['full_brief']['content']
    brief_type = project.get('brief_type', 'reply')
    docs = project.get('documents', {})

    # Gather source documents for context, with truncation
    record_combined = _gather_record_volumes(docs)
    research_text = _gather_legal_research(docs, project.get('case_law_issues', {}))

    doc_items = build_doc_items_for_brief_type(docs, record_combined, research_text, brief_type)

    fitted = _fit_documents(doc_items)
    source_docs = "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    # Pre-process opening brief constraints for reply brief revisions
    revise_constraints = ''
    if brief_type == 'reply':
        full_opening = docs.get('opening_brief', {}).get('text', '')
        if full_opening:
            revise_constraints = _preprocess_opening_brief(full_opening)

    # Build party context so the AI knows which side it's writing for
    appellant_name = project.get('appellant', 'Appellant')
    respondent_name = project.get('respondent', 'Respondent')
    if brief_type == 'appellant':
        party_context = f"You are writing FOR {appellant_name} (the appellant) AGAINST {respondent_name} (the respondent). Every argument must advocate for the appellant's position."
    elif brief_type == 'respondent':
        party_context = f"You are writing FOR {respondent_name} (the respondent) AGAINST {appellant_name} (the appellant). Every argument must advocate for the respondent's position and defend the lower court/agency decision."
    else:  # reply
        party_context = f"You are writing FOR {appellant_name} (the appellant) AGAINST {respondent_name} (the respondent). This is a reply brief responding to the respondent's arguments."

    prompt = build_revision_prompt(revision_instructions, party_context, revise_constraints)
    # Append exemplars for brief type
    prompt = prompt.replace(
        "OUTPUT ONLY THE COMPLETE REVISED BRIEF TEXT.",
        f"{_build_exemplars(brief_type)}\n\nOUTPUT ONLY THE COMPLETE REVISED BRIEF TEXT."
    )

    # --- Pre-revision metrics (baseline for validation) ---
    pre_metrics = count_brief_metrics(existing_brief)
    print(f"[REVISION] Pre-metrics: {pre_metrics['words']} words, {pre_metrics['record_cites']} record cites, "
          f"{pre_metrics['case_cites']} case cites, {pre_metrics['quotes']} quotes, "
          f"Points: {pre_metrics['points']}", flush=True)

    # Scale max_tokens to brief length — 1 token ~ 4 chars, add 20% headroom
    estimated_tokens = max(16000, int(len(existing_brief) / 3.5))
    model = data.get('model', 'sonnet')

    # Build document blocks for citation tracking.
    # Budget: skip record volumes (too large for revision context) and cap total size.
    revise_docs = [{"text": existing_brief, "title": "Existing Brief"}]
    context_budget = 150000  # chars, leave room for prompt + output tokens
    context_used = len(existing_brief) + len(prompt)
    for label, text in fitted:
        if text and 'RECORD ON APPEAL' not in label:
            if context_used + len(text) < context_budget:
                revise_docs.append({"text": text, "title": label})
                context_used += len(text)
    revised_text, revise_citations = call_claude_with_docs(prompt, revise_docs, max_tokens=estimated_tokens, model=model)
    print(f"[CITATIONS] revise_brief returned {len(revise_citations)} source citations", flush=True)

    # Convert any bold case names to underscore format
    revised_text = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', revised_text)

    # Citation validation — flag fabricated case names and reporter numbers
    source_texts_for_validation = [text for label, text in fitted if text]
    source_texts_for_validation.append(existing_brief)  # also check against the brief being revised
    revised_text = validate_citations(revised_text, *source_texts_for_validation)

    # --- Post-revision validation: REJECT if content gutted ---
    post_metrics = count_brief_metrics(revised_text)
    print(f"[REVISION] Post-metrics: {post_metrics['words']} words, {post_metrics['record_cites']} record cites, "
          f"{post_metrics['case_cites']} case cites, {post_metrics['quotes']} quotes, "
          f"Points: {post_metrics['points']}", flush=True)

    violations = validate_revision_integrity(pre_metrics, post_metrics)

    # Refusal detection: reject if AI wrote meta-commentary instead of revising
    refusal_phrases = ['i cannot', 'i apologize', 'i\'m unable', 'please clarify', 'i must maintain']
    lower_revised = revised_text[:500].lower()
    for phrase in refusal_phrases:
        if phrase in lower_revised:
            violations.append(f"AI refused to revise (detected: '{phrase}')")
            break

    if violations:
        print(f"[REVISION] REJECTED -- {len(violations)} violations: {violations}", flush=True)
        return jsonify({
            'error': 'Revision rejected -- content loss detected. Your original brief is preserved.',
            'violations': violations,
            'pre_metrics': {k: v if not isinstance(v, list) else len(v) for k, v in pre_metrics.items()},
            'post_metrics': {k: v if not isinstance(v, list) else len(v) for k, v in post_metrics.items()},
        }), 422

    # --- Validation passed — save revision ---
    # Re-read project from disk to avoid overwriting concurrent changes
    fresh_project = get_project(project_id)
    if 'drafted_sections' not in fresh_project:
        fresh_project['drafted_sections'] = {}
    if 'revision_count' not in fresh_project:
        fresh_project['revision_count'] = 0
    if 'revision_history' not in fresh_project:
        fresh_project['revision_history'] = []

    # Update fresh project with revision data
    fresh_project['drafted_sections']['full_brief'] = {
        'content': revised_text,
        'drafted_at': datetime.now().isoformat()
    }
    fresh_project['revision_count'] = fresh_project['revision_count'] + 1
    fresh_project['revision_history'].append({
        'instructions': revision_instructions,
        'timestamp': datetime.now().isoformat(),
        'previous_brief': existing_brief
    })
    if len(fresh_project['revision_history']) > 20:
        fresh_project['revision_history'] = fresh_project['revision_history'][-20:]
    save_project(project_id, fresh_project)

    return jsonify({
        'revised_brief': revised_text,
        'revision_count': fresh_project.get('revision_count', 1),
        'metrics': {
            'pre': {k: v if not isinstance(v, list) else len(v) for k, v in pre_metrics.items()},
            'post': {k: v if not isinstance(v, list) else len(v) for k, v in post_metrics.items()},
        }
    })


@app.route('/project/<project_id>/supplement', methods=['POST'])
def supplement_brief(project_id):
    """Supplement an existing brief with new transcript summary evidence."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    sections = project.get('drafted_sections', {})
    if 'full_brief' not in sections or not sections['full_brief'].get('content'):
        return jsonify({'error': 'No draft to supplement. Draft the brief first.'}), 400

    data = request.json or {}
    supplement_doc_keys = data.get('supplement_doc_keys', [])
    if not supplement_doc_keys:
        return jsonify({'error': 'No transcript summaries selected for supplementation.'}), 400

    existing_brief = sections['full_brief']['content']
    brief_type = project.get('brief_type', 'reply')
    docs = project.get('documents', {})

    # Validate that all requested docs exist
    summary_docs = []
    for key in supplement_doc_keys:
        doc = docs.get(key)
        if not doc or not doc.get('text'):
            return jsonify({'error': f'Document "{key}" not found or has no text.'}), 400
        summary_docs.append((doc.get('filename', key), doc['text']))

    # Build party context
    appellant_name = project.get('appellant', 'Appellant')
    respondent_name = project.get('respondent', 'Respondent')
    if brief_type == 'appellant':
        party_context = f"You are writing FOR {appellant_name} (the appellant) AGAINST {respondent_name} (the respondent). Every argument must advocate for the appellant's position."
    elif brief_type == 'respondent':
        party_context = f"You are writing FOR {respondent_name} (the respondent) AGAINST {appellant_name} (the appellant). Every argument must advocate for the respondent's position and defend the lower court/agency decision."
    else:  # reply
        party_context = f"You are writing FOR {appellant_name} (the appellant) AGAINST {respondent_name} (the respondent). This is a reply brief responding to the respondent's arguments."

    # Opening brief constraints for reply briefs
    supp_constraints = ''
    if brief_type == 'reply':
        full_opening = docs.get('opening_brief', {}).get('text', '')
        if full_opening:
            supp_constraints = _preprocess_opening_brief(full_opening)

    prompt = build_supplement_prompt(party_context, supp_constraints)
    # Append exemplars
    prompt = prompt.replace(
        "OUTPUT THE COMPLETE SUPPLEMENTED BRIEF. No commentary. PLAIN TEXT ONLY — NO MARKDOWN:",
        f"{_build_exemplars(brief_type)}\n\nOUTPUT THE COMPLETE SUPPLEMENTED BRIEF. No commentary. PLAIN TEXT ONLY — NO MARKDOWN:"
    )

    # Pre-supplement metrics
    pre_metrics = count_brief_metrics(existing_brief)
    print(f"[SUPPLEMENT] Pre-metrics: {pre_metrics['words']} words, {pre_metrics['record_cites']} record cites, "
          f"{pre_metrics['case_cites']} case cites, {pre_metrics['quotes']} quotes, "
          f"Points: {pre_metrics['points']}", flush=True)
    print(f"[SUPPLEMENT] Adding {len(summary_docs)} summary doc(s): {[name for name, _ in summary_docs]}", flush=True)

    # Build API docs: existing brief + summaries only (no record volumes)
    supp_docs = [{"text": existing_brief, "title": "Existing Brief"}]
    for name, text in summary_docs:
        supp_docs.append({"text": text, "title": f"Transcript Summary: {name}"})

    # More headroom than revise — supplement adds content
    estimated_tokens = max(16000, int(len(existing_brief) / 3.0))
    model = data.get('model', 'sonnet')

    supplemented_text, supp_citations = call_claude_with_docs(prompt, supp_docs, max_tokens=estimated_tokens, model=model)
    print(f"[CITATIONS] supplement_brief returned {len(supp_citations)} source citations", flush=True)

    # Post-processing: bold to underscore case names
    supplemented_text = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', supplemented_text)

    # Citation validation
    source_texts_for_validation = [text for _, text in summary_docs]
    source_texts_for_validation.append(existing_brief)
    supplemented_text = validate_citations(supplemented_text, *source_texts_for_validation)

    # Post-supplement validation
    post_metrics = count_brief_metrics(supplemented_text)
    print(f"[SUPPLEMENT] Post-metrics: {post_metrics['words']} words, {post_metrics['record_cites']} record cites, "
          f"{post_metrics['case_cites']} case cites, {post_metrics['quotes']} quotes, "
          f"Points: {post_metrics['points']}", flush=True)

    violations = validate_supplement_integrity(pre_metrics, post_metrics)

    # Refusal detection
    refusal_phrases = ['i cannot', 'i apologize', 'i\'m unable', 'please clarify', 'i must maintain']
    lower_supp = supplemented_text[:500].lower()
    for phrase in refusal_phrases:
        if phrase in lower_supp:
            violations.append(f"AI refused to supplement (detected: '{phrase}')")
            break

    if violations:
        print(f"[SUPPLEMENT] REJECTED -- {len(violations)} violations: {violations}", flush=True)
        return jsonify({
            'error': 'Supplement rejected -- content loss detected. Your original brief is preserved.',
            'violations': violations,
            'pre_metrics': {k: v if not isinstance(v, list) else len(v) for k, v in pre_metrics.items()},
            'post_metrics': {k: v if not isinstance(v, list) else len(v) for k, v in post_metrics.items()},
        }), 422

    # Calculate deltas
    words_added = post_metrics['words'] - pre_metrics['words']
    cites_added = post_metrics['record_cites'] - pre_metrics['record_cites']

    # Save supplemented brief
    fresh_project = get_project(project_id)
    if 'drafted_sections' not in fresh_project:
        fresh_project['drafted_sections'] = {}
    if 'revision_count' not in fresh_project:
        fresh_project['revision_count'] = 0
    if 'revision_history' not in fresh_project:
        fresh_project['revision_history'] = []

    fresh_project['drafted_sections']['full_brief'] = {
        'content': supplemented_text,
        'drafted_at': datetime.now().isoformat()
    }
    fresh_project['revision_count'] = fresh_project['revision_count'] + 1
    fresh_project['revision_history'].append({
        'instructions': f"[SUPPLEMENT] Added evidence from: {', '.join(name for name, _ in summary_docs)}",
        'timestamp': datetime.now().isoformat(),
        'previous_brief': existing_brief,
        'type': 'supplement',
    })
    if len(fresh_project['revision_history']) > 20:
        fresh_project['revision_history'] = fresh_project['revision_history'][-20:]
    save_project(project_id, fresh_project)

    print(f"[SUPPLEMENT] Success: +{words_added} words, +{cites_added} citations", flush=True)

    return jsonify({
        'supplemented_brief': supplemented_text,
        'words_added': words_added,
        'cites_added': cites_added,
        'revision_count': fresh_project.get('revision_count', 1),
        'metrics': {
            'pre': {k: v if not isinstance(v, list) else len(v) for k, v in pre_metrics.items()},
            'post': {k: v if not isinstance(v, list) else len(v) for k, v in post_metrics.items()},
        }
    })


@app.route('/project/<project_id>/restore', methods=['POST'])
def restore_revision(project_id):
    """Restore a previous revision of the brief"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    revision_index = data.get('revision_index')

    history = project.get('revision_history', [])
    if not history:
        return jsonify({'error': 'No revision history available'}), 400

    if revision_index is None or revision_index < 0 or revision_index >= len(history):
        return jsonify({'error': f'Invalid revision index. Available: 0-{len(history)-1}'}), 400

    entry = history[revision_index]
    previous_brief = entry.get('previous_brief')
    if not previous_brief:
        return jsonify({'error': 'This revision does not have saved brief text (recorded before this feature was added)'}), 400

    project['drafted_sections']['full_brief'] = {
        'content': previous_brief,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, project)

    return jsonify({
        'restored_brief': previous_brief,
        'restored_from': f'Before revision {revision_index + 1}',
        'revision_count': project.get('revision_count', 0)
    })


@app.route('/project/<project_id>/generate', methods=['POST'])
def generate_brief(project_id):
    """Generate complete brief as Word document (type-aware)"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    output_path = generate_brief_docx(project)

    project['status'] = 'complete'
    project['output_file'] = str(output_path)
    save_project(project_id, project)

    return jsonify({
        'success': True,
        'download_url': f'/project/{project_id}/download'
    })


@app.route('/project/<project_id>/download')
def download_brief(project_id):
    """Download generated brief"""
    project = get_project(project_id)
    if not project:
        return "Project not found", 404

    brief_type = project.get('brief_type', 'reply')
    config = BRIEF_TYPE_CONFIG.get(brief_type, BRIEF_TYPE_CONFIG['reply'])
    output_filename = config['output_filename']

    output_path = PROJECTS_DIR / project_id / output_filename
    if not output_path.exists():
        # Backward compat: try legacy Reply_Brief.docx
        output_path = PROJECTS_DIR / project_id / 'Reply_Brief.docx'
        if not output_path.exists():
            return "Brief not generated yet", 404

    case_name_safe = project.get('case_name', 'draft').replace(' ', '_')
    download_name = f"{output_filename.replace('.docx', '')}_{case_name_safe}.docx"

    return send_file(
        output_path,
        as_attachment=True,
        download_name=download_name,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


@app.route('/project/<project_id>/download-section/<section_key>')
def download_section(project_id, section_key):
    """Download a single drafted section as a Word document"""
    project = get_project(project_id)
    if not project:
        return "Project not found", 404

    sections = project.get('drafted_sections', {})
    if section_key not in sections or not sections[section_key].get('content'):
        return "Section not found", 404

    output_path, filename = generate_section_docx(project, section_key)

    return send_file(
        output_path,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


if __name__ == '__main__':
    print("\n" + "="*60)
    print("BRIEF DRAFTER")
    print("="*60)
    print(f"\nServer starting at: http://127.0.0.1:5003")
    print("\nUpload your documents, then let Claude draft your brief.")
    print("Press Ctrl+C to stop.\n")

    app.run(debug=False, host='127.0.0.1', port=5003)
