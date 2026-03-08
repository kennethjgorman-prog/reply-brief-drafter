"""
BriefDrafter summarization & record indexing routes (Flask Blueprint).
"""

import time
import uuid
import threading
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify

from src.project_io import get_project, save_project
from src.utils.file_parser import parse_pdf_pages
from src.processors.two_pass_processor import TwoPassProcessor
from src.record_indexing import _build_record_index

summarization_bp = Blueprint('summarization', __name__)

# ---------------------------------------------------------------------------
# Transcript Summarization (Two-Pass Processor)
# ---------------------------------------------------------------------------

# In-memory job tracker for background summarization
_summarize_jobs = {}  # job_id -> {status, stage, current, total, message, result, error}

# In-memory index job tracker
_index_jobs = {}


def _cleanup_jobs(jobs_dict, max_age_seconds=3600):
    """Remove completed jobs older than max_age_seconds."""
    now = time.time()
    expired = [k for k, v in jobs_dict.items() if v.get('completed_at', 0) and (now - v.get('completed_at', 0)) > max_age_seconds]
    for k in expired:
        del jobs_dict[k]


def _build_focus_areas_from_analysis(project):
    """Auto-populate focus areas from the analysis phase arguments."""
    analysis = project.get('analysis', {})
    arguments = analysis.get('arguments', [])
    if not arguments:
        return ''

    focus_lines = []
    for i, arg in enumerate(arguments, 1):
        title = arg.get('title', '')
        detail = arg.get('appellant_argument', '') or arg.get('respondent_counter', '')
        if title:
            focus_lines.append(f"{i}. {title}: {detail[:200]}")

    return '\n'.join(focus_lines)


def _run_summarization(job_id, project_id, doc_type, file_path, focus_areas, model):
    """Background thread: run the two-pass transcript summarization."""
    try:
        _summarize_jobs[job_id]['status'] = 'running'

        def progress_cb(stage, current, total, message):
            _summarize_jobs[job_id].update({
                'stage': stage,
                'current': current,
                'total': total,
                'message': message,
            })

        pages = parse_pdf_pages(file_path)
        processor = TwoPassProcessor(model=model)
        result = processor.process_transcript(
            pages=pages,
            focus_areas=focus_areas,
            citation_config_name='appellate_record',
            deponent_name='',
            chunk_size=10,
            progress_callback=progress_cb,
        )

        # Save summary into project (re-read from disk to avoid overwriting concurrent changes)
        fresh_project = get_project(project_id)
        if fresh_project:
            if 'summaries' not in fresh_project:
                fresh_project['summaries'] = {}
            fresh_project['summaries'][doc_type] = {
                'narrative': result['narrative'],
                'fact_count': result['fact_count'],
                'word_count': result['word_count'],
                'created_at': datetime.now().isoformat(),
                'model': model,
            }
            save_project(project_id, fresh_project)

        _summarize_jobs[job_id]['status'] = 'complete'
        _summarize_jobs[job_id]['completed_at'] = time.time()
        _summarize_jobs[job_id]['result'] = {
            'narrative': result['narrative'],
            'fact_count': result['fact_count'],
            'word_count': result['word_count'],
        }

    except Exception as e:
        _summarize_jobs[job_id]['status'] = 'error'
        _summarize_jobs[job_id]['completed_at'] = time.time()
        _summarize_jobs[job_id]['error'] = str(e)


@summarization_bp.route('/project/<project_id>/summarize/<doc_type>', methods=['POST'])
def summarize_document(project_id, doc_type):
    """Start background summarization of an uploaded document."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    doc_info = project.get('documents', {}).get(doc_type)
    if not doc_info:
        return jsonify({'error': f'Document {doc_type} not uploaded'}), 400

    file_path = doc_info.get('path', '')
    if not file_path or not Path(file_path).exists():
        return jsonify({'error': 'Document file not found'}), 400

    # Auto-populate focus areas from analysis, allow override from request
    focus_areas = request.json.get('focus_areas', '') if request.is_json else ''
    if not focus_areas:
        focus_areas = _build_focus_areas_from_analysis(project)

    model = request.json.get('model', 'sonnet') if request.is_json else 'sonnet'

    job_id = str(uuid.uuid4())[:8]
    _summarize_jobs[job_id] = {
        'status': 'starting',
        'stage': '',
        'current': 0,
        'total': 0,
        'message': 'Starting...',
        'result': None,
        'error': None,
    }

    thread = threading.Thread(
        target=_run_summarization,
        args=(job_id, project_id, doc_type, file_path, focus_areas, model),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id, 'status': 'starting'})


@summarization_bp.route('/project/<project_id>/summarize-status/<job_id>')
def summarize_status(project_id, job_id):
    """Poll summarization progress."""
    _cleanup_jobs(_summarize_jobs)
    job = _summarize_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@summarization_bp.route('/project/<project_id>/summary/<doc_type>')
def get_summary(project_id, doc_type):
    """Get stored summary for a document."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    summary = project.get('summaries', {}).get(doc_type)
    if not summary:
        return jsonify({'error': 'No summary found'}), 404

    return jsonify(summary)


@summarization_bp.route('/project/<project_id>/index-record', methods=['POST'])
def index_record(project_id):
    """Start background record indexing."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    docs = project.get('documents', {})
    opening_text = docs.get('opening_brief', {}).get('text', '')

    job_id = str(uuid.uuid4())[:8]
    _index_jobs[job_id] = {
        'status': 'starting',
        'stage': 'extraction',
        'current': 0,
        'total': 0,
        'message': 'Starting record index...',
        'result': None,
        'error': None,
    }

    def run_index(jid, pid, documents, ob_text):
        try:
            def progress(stage, current, total, message):
                _index_jobs[jid].update({
                    'stage': stage,
                    'current': current,
                    'total': total,
                    'message': message,
                })

            index = _build_record_index(documents, ob_text, progress_callback=progress)
            # Save to project (re-read from disk to avoid overwriting concurrent changes)
            fresh_proj = get_project(pid)
            fresh_proj['record_index'] = index
            save_project(pid, fresh_proj)
            _index_jobs[jid]['status'] = 'complete'
            _index_jobs[jid]['completed_at'] = time.time()
            _index_jobs[jid]['result'] = {'fact_count': len(index)}
        except Exception as e:
            _index_jobs[jid]['status'] = 'error'
            _index_jobs[jid]['completed_at'] = time.time()
            _index_jobs[jid]['error'] = str(e)

    thread = threading.Thread(
        target=run_index,
        args=(job_id, project_id, docs, opening_text),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id, 'status': 'starting'})


@summarization_bp.route('/project/<project_id>/index-record-status/<job_id>')
def index_record_status(project_id, job_id):
    """Poll record indexing progress."""
    _cleanup_jobs(_index_jobs)
    job = _index_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)
