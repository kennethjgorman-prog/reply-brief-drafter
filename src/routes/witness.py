"""
BriefDrafter witness map routes (Flask Blueprint).
"""

from flask import Blueprint, request, jsonify

from src.project_io import get_project, save_project
from src.utils.transcript_parser import (
    extract_witness_map as extract_witness_map_from_pdf,
    extract_witness_roster_from_digests as extract_roster_from_digests,
    build_witness_constraint,
)

witness_bp = Blueprint('witness', __name__)


# ============ WITNESS MAP (ported from MotionDrafter) ============

@witness_bp.route('/project/<project_id>/witness-map', methods=['GET', 'POST'])
def witness_map_route(project_id):
    """Get or update the witness map for a project."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if request.method == 'GET':
        return jsonify({'witness_map': project.get('witness_map', [])})

    # POST — save witness map
    data = request.json or {}
    project['witness_map'] = data.get('witness_map', [])
    save_project(project_id, project)
    return jsonify({'success': True})


@witness_bp.route('/project/<project_id>/extract-witnesses', methods=['POST'])
def extract_witnesses_route(project_id):
    """Extract witness map from uploaded trial transcript PDFs."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    docs = project.get('documents', {})
    witness_entries = []

    # Extract from trial transcript PDFs
    for key, doc in docs.items():
        if not isinstance(doc, dict):
            continue
        file_path = doc.get('path', '')
        if file_path and file_path.lower().endswith('.pdf') and ('transcript' in key.lower() or 'trial' in key.lower()):
            try:
                wmap = extract_witness_map_from_pdf(file_path)
                witness_entries.extend(wmap.get('entries', []))
                print(f"[WITNESS MAP] Extracted {len(wmap.get('entries', []))} entries from {key}", flush=True)
            except Exception as e:
                print(f"[WITNESS MAP] Error extracting from {key}: {e}", flush=True)

    # Also try roster extraction from document text
    roster = extract_roster_from_digests(docs)
    if roster:
        # Enrich existing entries with party/role from roster
        for entry in witness_entries:
            witness_last = entry['witness'].split()[-1]
            for name, info in roster.items():
                if witness_last.lower() in name.lower() or name.lower() in witness_last.lower():
                    if info.get('party') and not entry.get('party'):
                        entry['party'] = info['party'].capitalize()
                    if info.get('role') and not entry.get('role'):
                        entry['role'] = info['role']
                    break

    project['witness_map'] = witness_entries
    save_project(project_id, project)

    return jsonify({
        'success': True,
        'entries': witness_entries,
        'count': len(witness_entries),
    })


def _build_witness_constraint_for_project(project):
    """Build witness constraint block from project's witness_map."""
    entries = project.get('witness_map', [])
    if not entries:
        return ''
    return build_witness_constraint({'entries': entries})
