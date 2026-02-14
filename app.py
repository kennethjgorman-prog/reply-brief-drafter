#!/usr/bin/env python3
"""
Brief Drafter
Drafts appellate briefs (Appellant's, Respondent's, and Reply) using AI assistance
"""

import os
import re
import json
import uuid
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from anthropic import Anthropic
import pdfplumber
from docx import Document as DocxDocument
from src.utils.file_parser import parse_pdf_pages
from src.processors.two_pass_processor import TwoPassProcessor

load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

# Configuration
BASE_DIR = Path(__file__).parent
PROJECTS_DIR = BASE_DIR / 'projects'
PROJECTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}

# Allow large file uploads (500MB) and long timeouts for big PDFs
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload

# ============ BRIEF TYPE CONFIGURATION ============

BRIEF_TYPE_CONFIG = {
    'appellant': {
        'label': "Appellant's Brief",
        'doc_title': 'BRIEF FOR APPELLANT',
        'signature_role': 'Attorney for Appellant',
        'output_filename': 'Appellants_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '✏️'},
            {'key': 'lower_court_decision', 'label': 'Lower Court Decision', 'icon': '📄'},
            {'key': 'trial_transcript', 'label': 'Trial Transcript', 'icon': '📄'},
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix", 'icon': '📑'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '📚'},
        ],
        'additional_uploads': [
            {'key': 'record_vol_1', 'label': 'Record Vol. 1'},
            {'key': 'record_vol_2', 'label': 'Record Vol. 2'},
            {'key': 'record_vol_3', 'label': 'Record Vol. 3'},
            {'key': 'record_vol_4', 'label': 'Record Vol. 4'},
            {'key': 'record_vol_5', 'label': 'Record Vol. 5'},
            {'key': 'memo_of_law', 'label': 'Memorandum of Law'},
            {'key': 'reply_affirmation', 'label': 'Reply Affirmation'},
            {'key': 'legal_research_2', 'label': 'Legal Research 2'},
            {'key': 'legal_research_3', 'label': 'Legal Research 3'},
            {'key': 'legal_research_4', 'label': 'Legal Research 4'},
            {'key': 'legal_research_5', 'label': 'Legal Research 5'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': 'Analyze for Appealable Errors',
        'draft_button': "Draft Appellant's Brief",
        'analyze_loading': 'Analyzing lower court decision for errors...',
        'draft_loading': "Drafting appellant's brief...",
    },
    'respondent': {
        'label': "Respondent's Brief",
        'doc_title': 'BRIEF FOR RESPONDENT',
        'signature_role': 'Attorney for Respondent',
        'output_filename': 'Respondents_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '✏️'},
            {'key': 'appellant_brief', 'label': "Appellant's Opening Brief", 'icon': '📄'},
            {'key': 'lower_court_decision', 'label': 'Lower Court Decision', 'icon': '📄'},
            {'key': 'respondent_appendix', 'label': "Respondent's Appendix", 'icon': '📑'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '📚'},
        ],
        'additional_uploads': [
            {'key': 'record_vol_1', 'label': 'Record Vol. 1'},
            {'key': 'record_vol_2', 'label': 'Record Vol. 2'},
            {'key': 'record_vol_3', 'label': 'Record Vol. 3'},
            {'key': 'record_vol_4', 'label': 'Record Vol. 4'},
            {'key': 'record_vol_5', 'label': 'Record Vol. 5'},
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix"},
            {'key': 'legal_research_2', 'label': 'Legal Research 2'},
            {'key': 'legal_research_3', 'label': 'Legal Research 3'},
            {'key': 'legal_research_4', 'label': 'Legal Research 4'},
            {'key': 'legal_research_5', 'label': 'Legal Research 5'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': "Analyze Appellant's Brief for Weaknesses",
        'draft_button': "Draft Respondent's Brief",
        'analyze_loading': "Analyzing appellant's brief for weaknesses...",
        'draft_loading': "Drafting respondent's brief...",
    },
    'reply': {
        'label': 'Reply Brief',
        'doc_title': 'REPLY BRIEF FOR APPELLANT',
        'signature_role': 'Attorney for Appellant',
        'output_filename': 'Reply_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '✏️'},
            {'key': 'opening_brief', 'label': 'Opening Brief (Your Brief)', 'icon': '📄'},
            {'key': 'respondent_brief', 'label': "Respondent's Brief", 'icon': '📄'},
            {'key': 'record_vol_1', 'label': 'Record on Appeal Vol. 1', 'icon': '📁'},
            {'key': 'record_vol_2', 'label': 'Record on Appeal Vol. 2', 'icon': '📁'},
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix", 'icon': '📑'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '📚'},
        ],
        'additional_uploads': [
            {'key': 'respondent_appendix', 'label': "Respondent's Appendix"},
            {'key': 'trial_transcript', 'label': 'Trial Transcript'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': 'Analyze Both Briefs',
        'draft_button': 'Draft Entire Reply Brief',
        'analyze_loading': 'Analyzing arguments...',
        'draft_loading': 'Drafting entire reply brief...',
    },
}

# Max characters to include per document in prompts (~150K tokens total budget)
# Primary docs (briefs, decisions) get more space; secondary docs get less
MAX_PRIMARY_CHARS = 200000    # ~50K tokens per primary doc
MAX_SECONDARY_CHARS = 100000  # ~25K tokens per secondary doc
MAX_TOTAL_CHARS = 380000      # ~130K tokens for docs, leaving room for prompt (~30K) + output (16K) within 200K context


def _truncate(text, max_chars):
    """Truncate text to max_chars, noting truncation if applied"""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... DOCUMENT TRUNCATED at {max_chars} characters due to size limits ...]"


def _fit_documents(doc_list, max_total=MAX_TOTAL_CHARS):
    """Proportionally truncate a list of (label, text, priority) tuples to fit max_total.
    Priority: 'primary' docs get 2x share vs 'secondary' docs.
    Documents smaller than their share redistribute surplus to larger docs."""
    total = sum(len(t) for _, t, _ in doc_list if t)
    if total <= max_total:
        return [(label, text) for label, text, _ in doc_list]

    # Two-pass allocation: first pass assigns shares, second redistributes surplus
    docs_with_text = [(i, label, text, priority) for i, (label, text, priority) in enumerate(doc_list) if text]
    total_weight = sum(2.0 if p == 'primary' else 1.0 for _, _, _, p in docs_with_text)

    allocations = {}
    for i, label, text, priority in docs_with_text:
        weight = 2.0 if priority == 'primary' else 1.0
        allocations[i] = int(max_total * (weight / total_weight))

    # Redistribute surplus from docs that fit within their share
    surplus = 0
    needs_more = []
    for i, label, text, priority in docs_with_text:
        if len(text) <= allocations[i]:
            surplus += allocations[i] - len(text)
            allocations[i] = len(text)
        else:
            needs_more.append((i, priority))

    if surplus > 0 and needs_more:
        need_weight = sum(2.0 if p == 'primary' else 1.0 for _, p in needs_more)
        for i, priority in needs_more:
            weight = 2.0 if priority == 'primary' else 1.0
            allocations[i] += int(surplus * (weight / need_weight))

    results = []
    for i, (label, text, priority) in enumerate(doc_list):
        if not text:
            results.append((label, text))
        else:
            results.append((label, _truncate(text, allocations[i])))
    return results


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- Search-then-draft: find relevant pages in large records ---

_STOP_WORDS = frozenset([
    'the', 'a', 'an', 'is', 'was', 'were', 'are', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'must',
    'of', 'to', 'in', 'for', 'on', 'at', 'by', 'from', 'with', 'about',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
    'that', 'this', 'these', 'those', 'it', 'its', 'they', 'them', 'their',
    'he', 'she', 'his', 'her', 'him', 'we', 'us', 'our', 'you', 'your',
    'who', 'whom', 'which', 'what', 'where', 'when', 'how', 'why',
    'if', 'then', 'than', 'because', 'while', 'although', 'also',
    'each', 'every', 'all', 'any', 'few', 'more', 'most', 'some', 'such',
    'no', 'only', 'own', 'same', 'too', 'very', 'just',
    'draft', 'section', 'brief', 'argument', 'point', 'court',
])


def _extract_search_terms(text):
    """Extract meaningful search terms from text for record page scoring."""
    if not text:
        return []
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    return list(set(w for w in words if w not in _STOP_WORDS))


def _search_record_pages(record_text, search_terms, max_chars):
    """Search a large record for pages relevant to the given search terms.

    Splits by --- PAGE N --- markers, scores each page by term hits,
    always includes the first 10 pages of each volume (case info),
    then fills with highest-scoring pages (plus neighbors) until max_chars.
    Uses sequential indexing to handle multi-volume records where page
    numbers restart (e.g., both Vol 1 and Vol 2 have pages 1-536).
    Returns selected pages in original order with markers preserved.
    """
    if not record_text or not search_terms:
        return record_text

    # Split into pages using sequential index (handles multi-volume page resets)
    # Volume headers like "--- RECORD VOL. 2 ---" get attached to the next page
    parts = re.split(r'(--- PAGE \d+ ---)', record_text)

    pages = []  # list of (seq_index, text) — sequential order preserved
    current_text_parts = []
    pending_prefix = ""  # volume headers before first page marker

    for part in parts:
        m = re.match(r'--- PAGE (\d+) ---', part)
        if m:
            # Save previous page
            if current_text_parts:
                pages.append(''.join(current_text_parts))
                current_text_parts = []
            # If there's a volume header pending, attach it to this page
            if pending_prefix:
                current_text_parts.append(pending_prefix)
                pending_prefix = ""
            current_text_parts.append(part)
            # Detect volume boundary: page 1 means new volume starts
            page_num = int(m.group(1))
            if page_num == 1 and pages:
                # Mark this as a volume boundary for "first 10 pages" logic
                pass  # handled below via is_early_page tracking
        else:
            # Check if this part contains a volume header (e.g., between volumes)
            vol_match = re.search(r'(--- RECORD VOL\. \d+ ---)', part)
            if vol_match:
                # Split: text before header goes to current page, header becomes prefix for next
                before = part[:vol_match.start()]
                vol_header = vol_match.group(1)
                after = part[vol_match.end():]
                if before.strip():
                    current_text_parts.append(before)
                pending_prefix = vol_header + after
            else:
                current_text_parts.append(part)

    # Save last page
    if current_text_parts:
        pages.append(''.join(current_text_parts))

    if not pages:
        return record_text

    # Identify "first 10 pages" of each volume
    early_indices = set()
    vol_page_counter = 0
    for idx, page_text in enumerate(pages):
        m = re.search(r'--- PAGE (\d+) ---', page_text)
        if m:
            page_num = int(m.group(1))
            if page_num == 1:
                vol_page_counter = 0  # new volume
            vol_page_counter += 1
            if vol_page_counter <= 10:
                early_indices.add(idx)

    # Score each page by how many distinct search terms appear
    terms_lower = [t.lower() for t in search_terms]
    scores = []
    for idx, page_text in enumerate(pages):
        text_lower = page_text.lower()
        score = sum(1 for term in terms_lower if term in text_lower)
        scores.append(score)

    # Start with early pages (first 10 of each volume)
    selected = set(early_indices)

    # Rank remaining pages by score, descending
    ranked = sorted(
        [(idx, scores[idx]) for idx in range(len(pages)) if idx not in selected and scores[idx] > 0],
        key=lambda x: (-x[1], x[0])
    )

    # Add highest-scoring pages plus neighbors until budget is reached
    budget_used = sum(len(pages[idx]) for idx in selected)

    for idx, score in ranked:
        neighbors = [idx - 1, idx, idx + 1]
        new_indices = [n for n in neighbors if 0 <= n < len(pages) and n not in selected]
        added_size = sum(len(pages[n]) for n in new_indices)

        if budget_used + added_size > max_chars:
            if idx not in selected:
                page_size = len(pages[idx])
                if budget_used + page_size <= max_chars:
                    selected.add(idx)
                    budget_used += page_size
                else:
                    break
            continue

        for n in new_indices:
            selected.add(n)
        budget_used += added_size

    # Reassemble in original order
    result_parts = [pages[idx] for idx in sorted(selected)]

    omitted = len(pages) - len(selected)
    result = '\n\n'.join(result_parts)
    if omitted > 0:
        result += f"\n\n[... {omitted} pages omitted — showing {len(selected)} most relevant pages out of {len(pages)} total ...]"

    return result


def extract_text(file_path: Path) -> str:
    """Extract text from PDF, DOCX, or TXT file"""
    ext = file_path.suffix.lower()

    if ext == '.pdf':
        text_parts = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        # Use printed page number from top of page if available
                        first_line = page_text.strip().split('\n')[0].strip()
                        if first_line.isdigit():
                            page_label = first_line
                        else:
                            page_label = str(i)
                        text_parts.append(f"--- PAGE {page_label} ---\n{page_text}")
        except Exception as e:
            return f"Error reading PDF: {e}"
        return "\n\n".join(text_parts)

    elif ext == '.docx':
        try:
            doc = DocxDocument(str(file_path))
            return '\n\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
        except Exception as e:
            return f"Error reading DOCX: {e}"

    else:  # .txt
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"


def get_project(project_id: str) -> dict:
    """Load project data with backward-compat migration"""
    project_file = PROJECTS_DIR / project_id / 'project.json'
    if project_file.exists():
        with open(project_file, 'r') as f:
            data = json.load(f)
        # Migrate legacy projects that lack brief_type
        if 'brief_type' not in data:
            data['brief_type'] = 'reply'
            data['representing'] = 'appellant'
            save_project(project_id, data)
        return data
    return None


def save_project(project_id: str, data: dict):
    """Save project data"""
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(exist_ok=True)
    with open(project_dir / 'project.json', 'w') as f:
        json.dump(data, f, indent=2)


MODELS = {
    'sonnet': 'claude-sonnet-4-20250514',
    'opus': 'claude-opus-4-20250514',
}

def call_claude(prompt: str, max_tokens: int = 4000, model: str = 'sonnet') -> str:
    """Call Claude API with streaming to support long Opus requests"""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY not set in .env file"

    model_id = MODELS.get(model, MODELS['sonnet'])
    try:
        client = Anthropic(api_key=api_key)
        with client.messages.stream(
            model=model_id,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            result = stream.get_final_text()
        return result
    except Exception as e:
        return f"ERROR: {str(e)}"


def validate_citations(memo_text: str, *source_texts) -> str:
    """
    Validate case citations against source materials.
    Checks every _Case v. Party_ citation to confirm the party names
    appear somewhere in the uploaded source documents. Citations not
    found in sources are tagged [UNVERIFIED CITATION].
    """
    combined_sources = '\n'.join(t for t in source_texts if t).lower()

    if not combined_sources.strip():
        print("[CITATION CHECK] No source materials to validate against, skipping", flush=True)
        return memo_text

    flagged = []

    def check_case(match):
        full_match = match.group(0)
        case_name = match.group(1).strip()

        # Extract plaintiff (before v./v)
        v_match = re.search(r'^(.+?)\s+v\.?\s+', case_name)
        if not v_match:
            return full_match

        plaintiff = v_match.group(1).strip()
        plaintiff_words = plaintiff.split()

        skip = {'matter', 'of', 'in', 're', 'the', 'ex', 'rel', 'people', 'state'}
        significant_p = [w for w in plaintiff_words if w.lower() not in skip]

        if not significant_p:
            return full_match

        p_name = significant_p[0].lower().rstrip('.,;:')

        if len(p_name) < 3:
            return full_match

        # Check plaintiff name in sources (word boundary match)
        if re.search(r'\b' + re.escape(p_name) + r'\b', combined_sources):
            return full_match

        # Fallback: check defendant name
        d_match = re.search(r'v\.?\s+(.+)', case_name)
        if d_match:
            defendant = d_match.group(1).strip()
            defendant_words = defendant.split()
            significant_d = [w for w in defendant_words if w.lower() not in skip]
            if significant_d:
                d_name = significant_d[0].lower().rstrip('.,;:')
                if len(d_name) >= 3 and re.search(r'\b' + re.escape(d_name) + r'\b', combined_sources):
                    return full_match

        flagged.append(case_name)
        print(f"[CITATION CHECK] UNVERIFIED: {case_name}", flush=True)
        return full_match + ' [UNVERIFIED CITATION]'

    result = re.sub(r'_([^_]+?v\.?\s+[^_]+?)_', check_case, memo_text)

    if flagged:
        print(f"[CITATION CHECK] Flagged {len(flagged)} unverified citation(s): {flagged}", flush=True)
    else:
        print("[CITATION CHECK] All citations verified against source materials", flush=True)

    return result


def enforce_paragraph_cites(draft_text: str) -> str:
    """Check every FACTUAL paragraph's last sentence for a record citation.
    If missing, append [CITE NEEDED]. Skips legal argument paragraphs
    (those with case citations), headings, short paragraphs, and transitions."""
    record_cite_pattern = re.compile(r'\(\d[\d\-–, ]*\)')
    # Pattern for case law citations — paragraphs with these are legal argument, not factual
    case_cite_pattern = re.compile(r'\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d)\s+\d+')
    paragraphs = draft_text.split('\n\n')
    fixed = []
    flagged_count = 0

    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            fixed.append(para)
            continue

        # Skip headings (all caps, short, no period at end)
        if stripped.isupper() or (len(stripped) < 120 and not stripped.endswith('.')):
            fixed.append(para)
            continue

        # Skip short paragraphs (transitions, topic sentences)
        if len(stripped) < 200:
            fixed.append(para)
            continue

        # Skip paragraphs that contain case law citations — these are legal argument
        if case_cite_pattern.search(stripped):
            fixed.append(para)
            continue

        # Skip paragraphs that contain underscored case names
        if re.search(r'_[^_]+v\.?\s+[^_]+_', stripped):
            fixed.append(para)
            continue

        # Skip paragraphs that attribute to respondent (argument paragraphs)
        if re.search(r'(?i)\b(?:respondent|defendant|global|lomma)\s+(?:argues?|contends?|claims?|asserts?|relies)', stripped):
            fixed.append(para)
            continue

        # This is a factual paragraph — check for record cite
        # Find last sentence — split on period followed by space or end
        sentences = re.split(r'(?<=[.!?])\s+', stripped)
        if not sentences:
            fixed.append(para)
            continue

        last_sentence = sentences[-1]
        if not record_cite_pattern.search(last_sentence):
            flagged_count += 1
            # Append flag to the last sentence
            if last_sentence.endswith('.'):
                new_last = last_sentence[:-1] + ' [CITE NEEDED].'
            else:
                new_last = last_sentence + ' [CITE NEEDED]'
            para = para[:para.rfind(last_sentence)] + new_last

        fixed.append(para)

    if flagged_count:
        print(f"[CITE CHECK] Flagged {flagged_count} paragraph(s) missing last-sentence record cite", flush=True)
    else:
        print("[CITE CHECK] All factual paragraphs have record cites", flush=True)

    return '\n\n'.join(fixed)


def enforce_case_cites(draft_text: str, research_text: str) -> str:
    """Find case names in the draft that are missing full citations,
    look up the full cite from the uploaded legal research, and insert it."""
    if not research_text:
        return draft_text

    # Build a lookup: case name -> full citation string from the research docs
    # Match patterns like: Case v. Party, 123 AD3d 456 [2d Dept 2020]
    # or: Case v Party, 110 A.D.3d 965 (2013)
    cite_lookup = {}
    # Parse citations line by line: find "v" in line, then find the last comma
    # followed by a digit (the volume number) — everything before is case name,
    # everything after is the citation. Handles commas in "Inc.," and "LLC,"
    for line in research_text.split('\n'):
        if not re.search(r'\bv\.?\s', line):
            continue
        # Find last comma followed by space+digit (that's where the cite starts)
        cite_comma = None
        for i in range(len(line) - 2, -1, -1):
            if line[i] == ',' and i + 1 < len(line) and line[i+1:].lstrip().startswith(tuple('0123456789')):
                cite_comma = i
                break
        if cite_comma is None:
            continue
        case_name = line[:cite_comma].strip()
        full_cite = line[cite_comma+1:].strip()
        if not case_name or not full_cite or not re.match(r'\d', full_cite):
            continue
        # Normalize: remove periods from reporter for lookup
        # Store with the case name's last significant word as key
        # Use multiple keys for flexible matching
        name_lower = case_name.lower()
        # Extract plaintiff name (before v)
        v_match = re.match(r'(.+?)\s+v\.?\s+(.+)', case_name, re.IGNORECASE)
        if v_match:
            plaintiff = v_match.group(1).strip()
            defendant = v_match.group(2).strip()
            # Normalize the cite to official NY format (no periods in reporter)
            normalized_cite = full_cite.replace('A.D.3d', 'AD3d').replace('A.D.2d', 'AD2d')
            normalized_cite = normalized_cite.replace('N.Y.3d', 'NY3d').replace('N.Y.2d', 'NY2d')
            normalized_cite = normalized_cite.replace('N.Y.S.2d', 'NYS2d').replace('N.Y.S.3d', 'NYS3d')
            # Convert (2013) to [2013] if no dept info
            normalized_cite = re.sub(r'\((\d{4})\)', r'[\1]', normalized_cite)
            key = f"{plaintiff.lower()} v {defendant.lower()}".replace('.', '')
            cite_lookup[key] = (case_name, normalized_cite)

    if not cite_lookup:
        return draft_text

    print(f"[CASE CITE] Found {len(cite_lookup)} case citations in legal research", flush=True)

    # Find underscored case names in draft that lack a full cite after them
    # Pattern: _Case v. Party_ NOT followed by a comma and reporter
    def insert_cite(match):
        full_match = match.group(0)
        inner = match.group(1).strip()

        # Check if a cite already follows (look ahead in the original text)
        end_pos = match.end()
        after = draft_text[end_pos:end_pos + 30]
        if re.match(r',?\s*\d+\s+(?:AD|NY|Misc)', after):
            return full_match  # already has a cite

        # Try to find this case in our lookup
        inner_lower = inner.lower().replace('.', '')
        for key, (orig_name, cite) in cite_lookup.items():
            # Check if the case names match (fuzzy — check plaintiff and defendant)
            v_match_draft = re.match(r'(.+?)\s+v\.?\s+(.+)', inner, re.IGNORECASE)
            v_match_key = re.match(r'(.+?)\s+v\s+(.+)', key)
            if v_match_draft and v_match_key:
                p_draft = v_match_draft.group(1).strip().lower().replace('.', '')
                d_draft = v_match_draft.group(2).strip().lower().replace('.', '').rstrip(',')
                p_key = v_match_key.group(1).strip()
                d_key = v_match_key.group(2).strip()
                # Match if plaintiff's first significant word matches
                p_words_draft = [w for w in p_draft.split() if len(w) > 2]
                p_words_key = [w for w in p_key.split() if len(w) > 2]
                if p_words_draft and p_words_key and p_words_draft[0] == p_words_key[0]:
                    print(f"[CASE CITE] Inserting full cite for {inner}", flush=True)
                    return f"_{inner}_, {cite}"

        return full_match

    result = re.sub(r'_([^_]+?v\.?\s+[^_]+?)_', insert_cite, draft_text)
    return result


def guardrail_brief(draft_text: str, brief_type: str, research_text: str = '', opening_brief_text: str = '') -> str:
    """Post-processing guardrails for drafted briefs. Validates and fixes output programmatically.
    This is code, not a prompt — Claude can't ignore it."""

    result = draft_text

    # 1. Strip any markdown that slipped through
    result = re.sub(r'^#{1,4}\s+', '', result, flags=re.MULTILINE)  # ## headings
    result = re.sub(r'\*\*([^*]+)\*\*', r'\1', result)  # **bold**
    result = re.sub(r'(?<![_])\*([^*]+)\*(?![_])', r'\1', result)  # *italic*

    # 2. Fix case name formatting: bold to underscore
    result = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', result)

    # 3. Fix wrong section headings based on brief type
    if brief_type == 'appellant':
        # Appellant briefs should NOT have counterstatement language
        result = re.sub(
            r'(?im)^[\t ]*COUNTER[- ]?STATEMENT\s+(?:TO\s+)?(?:DEFENDANTS?\')?\s*(?:STATEMENT\s+OF\s+)?FACTS.*$',
            'STATEMENT OF THE CASE',
            result
        )
        result = re.sub(
            r'(?im)^[\t ]*COUNTERSTATEMENT\s+OF\s+FACTS.*$',
            'STATEMENT OF THE CASE',
            result
        )
        # Fix "requesting affirmance" in conclusion — appellant requests reversal
        result = re.sub(r'(?i)requesting affirmance', 'requesting reversal', result)
        result = re.sub(r'(?i)should be affirmed', 'should be reversed', result)

    elif brief_type == 'respondent':
        # Respondent briefs should NOT say "STATEMENT OF THE CASE" — use COUNTERSTATEMENT
        result = re.sub(
            r'(?im)^[\t ]*STATEMENT\s+OF\s+THE\s+CASE\s*$',
            'COUNTERSTATEMENT OF FACTS',
            result
        )
        # Fix "requesting reversal" — respondent requests affirmance
        result = re.sub(r'(?i)requesting reversal', 'requesting affirmance', result)

    elif brief_type == 'reply':
        # Reply briefs should not introduce new statement of facts
        pass

    # 4. Fix citation format: periods in reporters (A.D.3d -> AD3d)
    result = re.sub(r'A\.D\.3d', 'AD3d', result)
    result = re.sub(r'A\.D\.2d', 'AD2d', result)
    result = re.sub(r'N\.Y\.3d', 'NY3d', result)
    result = re.sub(r'N\.Y\.2d', 'NY2d', result)
    result = re.sub(r'N\.Y\.S\.2d', 'NYS2d', result)
    result = re.sub(r'N\.Y\.S\.3d', 'NYS3d', result)

    # 5. Fix bracket format: case cites must use [] not () for court/year
    # Pattern: AD3d 123 (2d Dept 2020) -> AD3d 123 [2d Dept 2020]
    result = re.sub(r'(\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d)\s+\d+)\s*\((\d{1,2}(?:st|d|th)\s+Dept\s+\d{4})\)',
                    r'\1 [\2]', result)

    # 6. Enforce paragraph cites (only for appellant/respondent briefs, not reply)
    # Reply briefs have many pure argument paragraphs that don't need record cites
    if brief_type != 'reply':
        result = enforce_paragraph_cites(result)

    # 7. Enforce case cites from research
    if research_text:
        result = enforce_case_cites(result, research_text)

    # 8. Enforce terminology from opening brief (code-level, AI can't override)
    if opening_brief_text and brief_type == 'reply':
        plaintiff_ct = len(re.findall(r'\bplaintiff\b', opening_brief_text, re.IGNORECASE))
        appellant_ct = len(re.findall(r'\bappellant\b', opening_brief_text, re.IGNORECASE))
        compound_ct = len(re.findall(r'\bplaintiff[- ]appellant\b', opening_brief_text, re.IGNORECASE))
        p_only = plaintiff_ct - compound_ct
        a_only = appellant_ct - compound_ct

        if p_only > a_only * 3:
            # Opening brief prefers "plaintiff" — replace standalone "appellant" with "plaintiff"
            replaced = 0
            def _fix_appellant(m):
                nonlocal replaced
                word = m.group(0)
                # Check if this is inside a case name (_..._) or compound form
                start = m.start()
                # Don't replace inside underscored case names
                before = result[:start]
                underscore_count = before.count('_')
                if underscore_count % 2 == 1:
                    return word  # inside a case name
                # Don't replace if preceded by "plaintiff-" or followed by compound
                prev_chars = result[max(0, start-11):start]
                if 'plaintiff-' in prev_chars.lower() or 'plaintiff ' in prev_chars.lower():
                    return word
                next_chars = result[m.end():m.end()+12].lower()
                if next_chars.startswith('-appellant') or next_chars.startswith(' appellant'):
                    return word
                # Preserve case
                replaced += 1
                if word[0].isupper():
                    return 'Plaintiff' if word == word.capitalize() else 'PLAINTIFF'
                return 'plaintiff'
            result = re.sub(r'\b[Aa]ppellant\b(?![\-])', _fix_appellant, result)
            # Also fix APPELLANTS -> PLAINTIFFS in headings
            result = re.sub(r'\bAPPELLANTS\b', 'PLAINTIFFS', result)
            result = re.sub(r'\bAppellants\b', 'Plaintiffs', result)
            result = re.sub(r'\bappellants\b', 'plaintiffs', result)
            if replaced:
                print(f"[TERMINOLOGY] Replaced {replaced} 'appellant' → 'plaintiff' to match opening brief", flush=True)

    return result


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

    # Update project
    project['documents'][doc_type] = {
        'filename': filename,
        'path': str(file_path),
        'text': text,
        'char_count': len(text)
    }

    # If existing_draft, also save it as the full_brief so revise works immediately
    if doc_type == 'existing_draft':
        if 'drafted_sections' not in project:
            project['drafted_sections'] = {}
        project['drafted_sections']['full_brief'] = {
            'content': text,
            'drafted_at': datetime.now().isoformat(),
            'source': 'uploaded'
        }

    save_project(project_id, project)

    response = {
        'success': True,
        'doc_type': doc_type,
        'filename': filename,
        'char_count': len(text)
    }

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


def _parse_analysis_json(result):
    """Parse JSON from Claude's analysis response"""
    try:
        start = result.find('{')
        end = result.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(result[start:end])
        return {'arguments': [], 'error': 'Could not parse response'}
    except json.JSONDecodeError:
        return {'arguments': [], 'raw_response': result}


def _analyze_for_appellant(docs):
    """Analyze lower court decision for appealable errors"""
    decision_text = docs.get('lower_court_decision', {}).get('text', '')
    transcript_text = docs.get('trial_transcript', {}).get('text', '')
    appendix_text = docs.get('appellant_appendix', {}).get('text', '')
    research_text = _gather_legal_research(docs)
    record_combined = _gather_record_volumes(docs)

    # Fit documents within token budget
    fitted = _fit_documents([
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('TRIAL TRANSCRIPT', transcript_text, 'secondary'),
        ('APPELLANT\'S APPENDIX', appendix_text, 'secondary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ])

    doc_sections = "\n\n".join(f"{label}:\n{text}" for label, text in fitted if text)

    prompt = f"""You are an expert appellate attorney analyzing a lower court decision to identify ALL appealable errors for an appellant's brief.

{doc_sections}

ANALYSIS REQUIREMENTS:

1. Identify EVERY appealable error in the lower court decision:
   - Errors of law (wrong legal standard applied)
   - Errors of fact (findings not supported by record)
   - Abuse of discretion
   - Procedural errors
   - Constitutional violations
   - Evidentiary rulings

2. For EACH error identified:
   - The specific ruling or finding that was wrong
   - The correct legal standard that should have been applied
   - Whether the issue was preserved for appeal (objection on the record)
   - The standard of review (de novo, abuse of discretion, clearly erroneous)
   - Record citations supporting the error

3. Assess strength and priority of each issue

OUTPUT FORMAT (JSON):
{{
  "errors": [
    {{
      "number": 1,
      "title": "Brief title of the error",
      "issue": "The specific legal question presented",
      "error_description": "What the lower court got wrong",
      "correct_standard": "What the law actually requires",
      "standard_of_review": "De novo / Abuse of discretion / Clearly erroneous",
      "preservation": "How/where the issue was preserved on the record",
      "record_citations": ["Page references from the record"],
      "cases_to_cite": ["Cases from the uploaded documents supporting reversal"],
      "reply_strategy": "How to frame this argument in the brief",
      "priority": "high/medium/low"
    }}
  ]
}}

Respond ONLY with valid JSON."""

    return call_claude(prompt, max_tokens=6000)


def _analyze_for_respondent(docs):
    """Analyze appellant's brief for weaknesses to defend the lower court decision"""
    appellant_text = docs.get('appellant_brief', {}).get('text', '')
    decision_text = docs.get('lower_court_decision', {}).get('text', '')
    appendix_text = docs.get('respondent_appendix', {}).get('text', '')
    research_text = _gather_legal_research(docs)
    record_combined = _gather_record_volumes(docs)

    # Fit documents within token budget
    fitted = _fit_documents([
        ('APPELLANT\'S OPENING BRIEF', appellant_text, 'primary'),
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('RESPONDENT\'S APPENDIX', appendix_text, 'secondary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ])

    doc_sections = "\n\n".join(f"{label}:\n{text}" for label, text in fitted if text)

    prompt = f"""You are an expert appellate attorney analyzing the appellant's opening brief to find weaknesses and prepare a respondent's brief defending the lower court decision.

{doc_sections}

ANALYSIS REQUIREMENTS:

1. For EACH argument appellant makes, identify:
   - The specific claim and cases appellant cites
   - Weaknesses in appellant's argument
   - Mischaracterized cases or holdings
   - Facts appellant ignores or misrepresents
   - Issues that were NOT preserved for appeal
   - Why the lower court's decision was correct

2. For EACH case appellant cites:
   - Is the characterization of the holding accurate?
   - Are there distinguishing facts?
   - Does the case actually support affirmance?

3. Identify affirmative defenses:
   - Harmless error arguments
   - Alternative grounds for affirmance
   - Waiver/forfeiture issues
   - Mootness or standing problems

OUTPUT FORMAT (JSON):
{{
  "weaknesses": [
    {{
      "number": 1,
      "title": "Brief title of the issue",
      "appellant_argument": "What appellant argues with their citations",
      "weakness": "Why this argument fails",
      "mischaracterized_cases": [
        {{
          "case": "Full citation",
          "appellant_claims": "What appellant says the case holds",
          "actual_holding": "What the case actually holds",
          "why_distinguishable": "Why this case supports affirmance"
        }}
      ],
      "record_evidence_for_affirmance": ["Facts supporting the lower court decision"],
      "response_strategy": "How to structure the response",
      "priority": "high/medium/low"
    }}
  ]
}}

Respond ONLY with valid JSON."""

    return call_claude(prompt, max_tokens=6000)


def _analyze_for_reply(docs):
    """Analyze both briefs for reply brief — existing logic"""
    respondent_briefs = _gather_respondent_briefs(docs, sanitize=False)
    respondent_text = '\n\n'.join(text for _, text, _ in respondent_briefs)
    opening_text = docs.get('opening_brief', {}).get('text', '')

    # Fit documents within token budget
    fitted = _fit_documents([
        ('opening', opening_text, 'primary'),
        ('respondent', respondent_text, 'primary'),
    ])
    opening_text = fitted[0][1] or ''
    respondent_text = fitted[1][1] or ''

    prompt = f"""You are an expert appellate attorney conducting DEEP LEGAL ANALYSIS of briefs to prepare a reply brief.

YOUR TASK: Conduct thorough analysis extracting SPECIFIC CITATIONS and HOLDINGS from the documents.

APPELLANT'S OPENING BRIEF:
{opening_text}

RESPONDENT'S BRIEF:
{respondent_text}

ANALYSIS REQUIREMENTS - BE THOROUGH:

1. For EACH argument point, you MUST extract:
   - EXACT case citations as they appear in respondent's brief (full citation format)
   - The SPECIFIC HOLDING or PROPOSITION respondent claims each case supports
   - EXACT QUOTES from respondent's brief showing their argument
   - Page numbers where respondent makes each argument

2. For EACH case respondent cites, analyze:
   - Does appellant's brief cite the same case? What does appellant say about it?
   - Is respondent's characterization of the holding accurate?
   - Are there distinguishing facts respondent ignores?

3. Identify SPECIFIC WEAKNESSES:
   - Misquoted or mischaracterized cases
   - Facts in the record that contradict respondent's claims
   - Legal standards respondent misstates
   - Arguments respondent fails to address

OUTPUT FORMAT (JSON):
{{
  "arguments": [
    {{
      "number": 1,
      "title": "Brief title of disputed issue",
      "appellant_argument": "What appellant argued with specific citations",
      "respondent_counter": "EXACT QUOTE from respondent's brief showing their argument",
      "cases_cited_by_respondent": [
        {{
          "case": "Full case citation as it appears",
          "respondent_claims": "What respondent says this case holds",
          "actual_holding": "What the case actually holds (if different)",
          "distinguishable_because": "Why this case doesn't apply here"
        }}
      ],
      "record_citations_to_use": ["Specific facts from record to cite in reply with page numbers"],
      "weaknesses": "Specific errors, misstatements, or gaps in respondent's argument",
      "reply_strategy": "How to structure the reply to this point",
      "priority": "high/medium/low"
    }}
  ]
}}

Respond ONLY with valid JSON."""

    return call_claude(prompt, max_tokens=6000)


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
    if brief_type == 'appellant':
        result = _analyze_for_appellant(docs)
    elif brief_type == 'respondent':
        result = _analyze_for_respondent(docs)
    else:
        result = _analyze_for_reply(docs)

    analysis = _parse_analysis_json(result)

    project['analysis'] = analysis
    project['status'] = 'analyzed'
    save_project(project_id, project)

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


def resolve_nyscef_url(page_num, nyscef_config):
    """Given a record page number, return NYSCEF URL with #page= or None."""
    if not nyscef_config or not nyscef_config.get('volumes'):
        return None
    volumes = sorted(nyscef_config['volumes'],
                     key=lambda v: v.get('first_page', 0), reverse=True)
    for vol in volumes:
        first_page = vol.get('first_page', 1)
        doc_index = vol.get('doc_index', '')
        offset = vol.get('page_offset', 0)
        if not doc_index or page_num < first_page:
            continue
        pdf_page = (page_num - first_page) + 1 + offset
        return f"https://iapps.courts.state.ny.us/nyscef/ViewDocument?docIndex={doc_index}#page={pdf_page}"
    return None


@app.route('/project/<project_id>/nyscef-config', methods=['POST'])
def save_nyscef_config(project_id):
    """Save NYSCEF hyperlink configuration for record volumes"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    volumes = []
    for vol in data.get('volumes', []):
        doc_index = vol.get('doc_index', '').strip()
        if 'docIndex=' in doc_index:
            doc_index = doc_index.split('docIndex=')[1].split('#')[0].split('&')[0]
        volumes.append({
            'doc_key': vol.get('doc_key', ''),
            'doc_index': doc_index,
            'first_page': int(vol.get('first_page', 1)),
            'page_offset': int(vol.get('page_offset', 0)),
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


def _build_drafting_protocol():
    """Shared anti-hallucination, citation, and formatting protocol for all brief types.
    Adapted from Universal Litigation Transcript Protocol v9.2 Enhanced Guardrails."""
    return """
================================================================================
DRAFTING PROTOCOL WITH ENHANCED ANTI-HALLUCINATION GUARDRAILS
================================================================================

YOU ARE BEING USED IN A LEGAL CONTEXT WHERE ACCURACY IS CRITICAL.
  - Hallucinated facts could mislead a court
  - Incorrect citations waste attorney time
  - Fabricated testimony or case law violates ethical rules
  - Your output WILL be reviewed by the attorney

================================================================================
RULE 1: SOURCE-FIRST WORKFLOW (MANDATORY)
================================================================================

You CANNOT write a sentence without FIRST finding the source in the uploaded documents.

  OLD (WRONG) WORKFLOW: Think of a fact → write sentence → try to find citation
  NEW (REQUIRED) WORKFLOW: Find fact in document → note page number → THEN write sentence with citation

For EVERY factual sentence, you MUST:
  1. FIND the specific fact in the RECORD ON APPEAL or other evidentiary document
  2. NOTE the page number where you found it (printed at top center of each page)
  3. WRITE the sentence based on what you actually found
  4. CITE the page at the end: (page). or (page-page).

If you cannot find a fact in the documents, DO NOT WRITE IT. Write [CITE NEEDED] instead.

================================================================================
RULE 2: RECORD CITATIONS — EVERY FACTUAL SENTENCE (NON-NEGOTIABLE)
================================================================================

EVERY SENTENCE that states a fact from the record MUST end with a citation.

  FORMAT: ([page]). — Period goes AFTER the closing parenthesis
  NO prefixes: No "R." or "A." or "p." — just the bare page number
  EXAMPLE: "The plaintiff fell on the stairs" (125).
  EXAMPLE: "The court dismissed the case" (91).

  CRITICAL — USE RECORD PAGE NUMBERS, NOT TRANSCRIPT PAGE NUMBERS:
  - The record on appeal has its own continuous pagination (the number after "--- PAGE X ---")
  - Deposition transcripts embedded in the record have INTERNAL page numbers ("Page 47" etc.)
  - You MUST cite the RECORD page number, NOT the internal transcript page number
  - WRONG: Testimony at deposition page 47, cited as (47)
  - RIGHT: Same testimony found at record page 135, cited as (135)
  - Match the page numbering used in the OPENING BRIEF — those are the correct record pages

  REQUIREMENTS:
  - EVERY factual sentence needs its own cite
  - The LAST sentence of every paragraph MUST have a citation
  - NO facts may be stated without a citation to Record, Appendix, or RA
  - If you cannot find support, write "[CITE NEEDED]"

  IF YOUR OUTPUT HAS FACTUAL SENTENCES WITHOUT CITATIONS, IT HAS FAILED.

================================================================================
RULE 3: CASE LAW CITATIONS — ZERO TOLERANCE FOR FABRICATION
================================================================================

*** YOU ARE FORBIDDEN FROM INVENTING CASE NAMES ***

YOUR ONLY SOURCES FOR CASE LAW ARE:
  a) Cases cited in any uploaded brief (opening brief, respondent's brief)
  b) Cases in the uploaded Legal Research document
  c) Cases cited in the lower court decision

THAT'S IT. NO OTHER SOURCES. PERIOD.

BEFORE YOU WRITE ANY CASE CITATION, ASK YOURSELF:
  "Did I see this exact case name in one of the uploaded documents?"
  If NO → DO NOT CITE IT. Write "[CASE CITE NEEDED]" instead.

YOU MUST NOT:
  - Cite ANY case from your training data or general knowledge
  - Cite ANY case you "remember" but cannot find in the uploaded documents
  - Invent a case name that "sounds right"
  - Fabricate holdings for real cases
  - Cite cases from your training data — your training data is OFF LIMITS

WHEN CITING A CASE:
  - Find the FULL citation string in the uploaded document and COPY IT
  - WRONG: _Smith v. Jones_ held that... (missing the full citation)
  - RIGHT: _Smith v. Jones_, 123 AD3d 456 [2d Dept 2020] held that...
  - If you cannot find the full citation, write [FULL CITE NEEDED]

CITATION FORMAT:
  - NEW YORK OFFICIAL FORMAT: 123 AD3d 456 [2d Dept 2020]
  - Case names use UNDERSCORES: _Case Name v. Other Party_
  - Court and year in SQUARE BRACKETS [ ], NEVER parentheses ( )
  - NO PERIODS in reporters: AD3d NOT A.D.3d, NY2d NOT N.Y.2d, NYS2d NOT N.Y.S.2d

================================================================================
RULE 4: ZERO INFERENCE POLICY
================================================================================

You may ONLY state facts that are EXPLICITLY in the uploaded documents.

PROHIBITED:
  - Emotional states not explicitly stated
  - Motivations or intentions not testified to
  - Causal relationships not explicitly stated
  - Credibility assessments
  - Logical conclusions, even if "obvious"
  - Negative inferences (absence of something not stated)

EXAMPLES OF PROHIBITED INFERENCES:
  ✗ "He was not hospitalized" → ✓ "He was treated in the ED and discharged"
  ✗ "She was able to walk" → ✓ "She exited the building"
  ✗ "The defendant knew about the condition" → ✓ State what the record actually says

PROHIBITED: ADDING CHARACTERIZATIONS TO CASE DESCRIPTIONS
  When describing what happened in a case, use ONLY the court's words.
  Do NOT add adjectives, labels, or causal explanations the court did not use.

  ✗ WRONG: In _Monroe_, "the bands snapped" due to a malfunction
    (The court said "the metal bands broke" — it NEVER said "malfunction."
     You FABRICATED "malfunction." That is a lie to the court.)

  ✓ RIGHT: In _Monroe_, "one or more of the metal bands broke, causing
    the logs to come loose and plaintiff to be propelled off the trailer"
    (Uses the court's ACTUAL language.)

  ✗ WRONG: The court found that defendant was negligent
    (Unless the court used the word "negligent" — do not characterize.)

  ✓ RIGHT: The court held that defendant "failed to exercise reasonable care"
    (Uses the court's ACTUAL language.)

  THIS RULE APPLIES TO EVERY CASE YOU DISCUSS:
  - Do NOT summarize a case holding in your own words and put it in quotes
  - Do NOT add words like "malfunction," "defect," "negligence," "reckless,"
    "intentional," "unsafe," "dangerous" unless the court used those exact words
  - When in doubt, QUOTE the court's actual language rather than paraphrasing
  - If you are describing a case from the respondent's brief, you are reading
    the respondent's CHARACTERIZATION of the case — NOT the court's language.
    Do NOT put the respondent's characterization in quotes and cite the case.

WHEN IN DOUBT:
  - Flag with [VERIFY] and let the attorney decide
  - Quote the source directly — use the EXACT words from the document
  - It is BETTER to flag uncertainty than to fabricate

================================================================================
RULE 5: DOCUMENT SOURCE HIERARCHY
================================================================================

CATEGORY A — EVIDENTIARY SOURCES (cite as record facts):
  - Lower court decision / order
  - Trial transcript
  - Record volumes / appendix
  - Exhibits, affidavits, sworn statements from the record

CATEGORY B — ADVOCACY DOCUMENTS (NOT facts — these are spin):
  - Appellant's brief / opening brief
  - Respondent's brief / answering brief
  - Any party's memorandum of law

RULES FOR CATEGORY B:
  a) NEVER cite a record page based on what an opposing brief says is on that page.
     Go to the ACTUAL record page and verify.
  b) NEVER put quotes from an opposing brief and cite a record page as if you found it.
  c) ALWAYS attribute: "Appellant argues that..." or "Respondent contends that..."
  d) NEVER adopt the opposing party's characterizations as your own.
  e) NEVER quote the opposing brief's DESCRIPTION of a case and cite the case
     as if those were the court's words. The opposing brief is SPIN — it
     describes cases the way it wants the court to see them.

  EXAMPLE OF RULE (e) VIOLATION:
  Respondent's brief says: "In Monroe, the bands snapped causing injury."
  ✗ WRONG: In _Monroe_, "the bands snapped" (_Monroe_ at 653).
    (You quoted RESPONDENT'S description and cited it as the court's language!)
  ✓ RIGHT: Respondent characterizes _Monroe_ as involving bands that snapped.
    However, the _Monroe_ court actually stated that "one or more of the metal
    bands broke" (_Monroe_ at 653).
    (You attributed respondent's language to respondent, then used the court's
    actual language separately.)

================================================================================
RULE 6: FORMATTING — PLAIN TEXT ONLY (NO MARKDOWN)
================================================================================

  - NEVER use markdown: NO ## headings, NO **bold**, NO *italics*, NO # anything
  - Output PLAIN TEXT ONLY
  - Section headings: plain ALL CAPS on their own line
  - Point headings: "POINT I" on its own line, heading in ALL CAPS on next line(s)
  - Sub-headings: tab + "A." + tab + heading text
  - Body paragraphs: Start with a tab character
  - Block quotes: Indent with two tabs
  - Blank line between paragraphs and around headings
  - Case names: _underscores_ for underlining (NOT asterisks)

================================================================================
SELF-AUDIT — RUN BEFORE OUTPUTTING
================================================================================

Before submitting your draft, check EVERY paragraph:

  1. Does EVERY factual sentence end with a record citation?
     If NO → ADD the citation or mark [CITE NEEDED]

  2. Did I find EVERY fact in the actual document before writing it?
     If NO → DELETE the sentence or mark [VERIFY]

  3. Did I make ANY inferences not explicitly in the documents?
     If YES → REWRITE to state only what the document says

  4. Does EVERY case citation include the FULL cite (volume, reporter, page, [court year])?
     If NO → Find and add the full cite or mark [FULL CITE NEEDED]

  5. Did I cite ANY case from my training data instead of the uploaded documents?
     If YES → DELETE it and write [CASE CITE NEEDED]

  6. Did I use the correct section headings for this brief type?
     If appellant brief → "STATEMENT OF THE CASE" (NOT "Counterstatement")
     If respondent brief → "COUNTERSTATEMENT OF FACTS" (NOT "Statement of the Case")

  7. Is my formatting plain text with no markdown?
     If NO → Remove all markdown

IF ANY CHECK FAILS, FIX IT BEFORE OUTPUTTING.
================================================================================"""


def _build_writing_style():
    """Writing style guidance for fact sections and argument sections"""
    return """
WRITING STYLE - TWO MODES (USE THE CORRECT ONE FOR EACH SECTION):

═══════════════════════════════════════════════════════════════
MODE 1: FACT SECTIONS (Preliminary Statement, Statement of Facts/Case, Counterstatement of Facts)
═══════════════════════════════════════════════════════════════

Write in flowing, professional narrative prose — NOT a choppy list of facts.

12 SENTENCE PATTERNS - USE STRATEGICALLY (never same pattern 2x in a row):
1. DIRECT ATTRIBUTION: "[Subject] [verb] [fact]" (45).
2. SUBORDINATE CLAUSE: "When [context], [main clause]" (45).
3. EMBEDDED ATTRIBUTION: "[Fact], [subject] testified, [continuation]" (45).
4. TEMPORAL TRANSITION: "[Time marker] + [fact]" (45).
5. COMPOUND WITH CONTRAST: "[Fact 1], but/yet [Fact 2]" (45).
6. PARTICIPIAL PHRASE: "[Verb-ing phrase], [main clause]" (45).
7. PASSIVE VOICE (sparingly): "[Object] was [past participle]" (45).
8. DIRECT QUOTE: [Attribution], "[direct quote]" (45).
9. INVERTED ORDER: "[Important fact first], [attribution second]" (45).
10. SEQUENTIAL: "[Subject] [verb] [item 1], [item 2], and [item 3]" (45).
11. CONCESSIVE: "Although/Though [fact 1], [fact 2]" (45).
12. APPOSITIONAL: "[Subject], [descriptive phrase], [verb phrase]" (45).

ANTI-MONOTONY RULES:
- NEVER use the same pattern more than 2x in a row
- VARY sentence length: Short (8-12 words) → Medium (13-20) → Long (21-30) — create rhythm
- ROTATE attribution verbs (pool of 20, never repeat within 5 sentences):
  testified, stated, explained, confirmed, noted, indicated, acknowledged,
  described, clarified, maintained, recounted, recalled, asserted, reported,
  revealed, admitted, conceded, observed, mentioned, established
- Combine related facts into fewer, richer sentences
- Use pronouns naturally — do NOT repeat party names in every sentence

FACT STYLE EXAMPLE — BAD (monotonous):
"Appellant testified he relied on his insurance. Appellant stated he expected
Nationwide to answer. Appellant explained he was a permissive user. Appellant
said he contacted Progressive later."

FACT STYLE EXAMPLE — GOOD (flowing):
"Following service of the complaint, Ekstein relied on Nationwide Insurance
Company to interpose an answer on his behalf, believing his status as a
permissive user of the vehicle entitled him to coverage (34-35). It was not
until plaintiff moved for default judgment that he contacted his own carrier,
Progressive Insurance, which agreed to assign counsel (34-35). By that point,
however, his time to answer had long expired (5)."

═══════════════════════════════════════════════════════════════
MODE 2: ARGUMENT SECTIONS (Point I, Point II, Point III, etc.)
═══════════════════════════════════════════════════════════════

Write persuasive legal argument in sophisticated, flowing prose.

ARGUMENT STYLE RULES:
- Lead with legal conclusions, then support with authority
- Integrate case citations into flowing prose — NOT as choppy standalone sentences
- Use rhetorical contrast: "Appellant claims X, but the record shows Y"
- Build logical chains: legal standard → application of facts → conclusion
- Combine related legal points into cohesive paragraphs (3-7 sentences each)
- Vary sentence length for rhythm and emphasis
- Use same 12 sentence patterns and anti-monotony rules as fact sections

ARGUMENT STYLE EXAMPLE — BAD (choppy, list-like):
"The court had discretion. The court exercised discretion properly.
Appellant failed to cross-move. CPLR 2215 requires a formal motion.
Appellant did not comply with CPLR 2215."

ARGUMENT STYLE EXAMPLE — GOOD (persuasive, flowing):
"The Supreme Court properly exercised its discretion in declining to treat
Ekstein's informal opposition papers as a cross-motion for relief. Under
CPLR 2215, a party seeking affirmative relief must make a formal cross-motion
— a requirement Ekstein indisputably failed to meet (5). While courts retain
discretion to entertain informal requests, _Fried v. Jacob Holding, Inc._,
110 AD3d 56 [2d Dept 2013], that discretion is not unlimited, and the factors
identified in _Fried_ weigh decisively against Ekstein here."

SENTENCE FLOW OPTIMIZATION (both modes):
1. PRONOUN CHAINS: Link sentences with pronouns referring to previous subjects
2. TOPIC CONTINUITY: Maintain subject continuity within topic groups
3. LOGICAL GROUPING: 3-7 sentences per paragraph, introduction → support → transition
4. SUBORDINATION: Use subordinate clauses for cause/effect, time, condition

PARTY REFERENCES — CRITICAL:
- Do NOT repetitively use "Appellant" or "Respondent" — it becomes monotonous
- Use the party's NAME (e.g., "Ekstein," "Zweibel") as the primary reference
- Use their ROLE in the case below (e.g., "defendant," "plaintiff") as secondary reference
- Use "Appellant"/"Respondent" only occasionally for variety
- Use pronouns ("he," "she," "they") naturally after establishing who you mean
- Mix these references: name → pronoun → role → name → pronoun

QUOTATION MARKS — SACRED:
- Quotation marks indicate EXACT WORDS from testimony, court decisions, or statutes
- NEVER remove quotation marks from quoted language
- NEVER paraphrase text that appears in quotation marks — preserve the exact words
- NEVER convert a direct quote into a paraphrase by dropping the quotes
- If the source material has language in quotes, keep it in quotes in the brief
- Adding quotation marks to language that was not quoted is equally wrong
"""


def _build_structure_prompt(structure):
    """Build the attorney-directed structure block for the drafting prompt"""
    parts = []
    parts.append("=== ATTORNEY-DEFINED BRIEF STRUCTURE (MANDATORY — FOLLOW EXACTLY) ===")
    parts.append("")
    parts.append("The attorney has defined the exact structure for this brief.")
    parts.append("Draft ONLY the Points defined below. Use ONLY the facts and cases listed.")
    parts.append("Do NOT invent additional arguments, facts, or Points beyond what is specified.")
    parts.append("Do NOT add cases from your training data — only use cases the attorney listed")
    parts.append("and cases found in the uploaded documents.")
    parts.append("")

    if structure.get('preliminary_statement'):
        parts.append("PRELIMINARY STATEMENT NOTES:")
        parts.append(structure['preliminary_statement'])
        parts.append("")

    if structure.get('procedural_history'):
        parts.append("PROCEDURAL HISTORY:")
        parts.append(structure['procedural_history'])
        parts.append("")

    if structure.get('factual_background'):
        parts.append("KEY FACTS:")
        parts.append(structure['factual_background'])
        parts.append("")

    for pt in structure.get('points', []):
        parts.append(f"--- POINT {pt['id']}: {pt.get('heading', '')} ---")
        if pt.get('argument_description'):
            parts.append(f"ARGUMENT: {pt['argument_description']}")
        if pt.get('facts'):
            parts.append(f"KEY FACTS FOR THIS POINT:\n{pt['facts']}")
        if pt.get('cases'):
            parts.append(f"KEY CASES FOR THIS POINT:\n{pt['cases']}")
        parts.append("")

    parts.append("=== END ATTORNEY-DEFINED STRUCTURE ===")
    return "\n".join(parts)


def _gather_additional_docs(docs):
    """Collect memo of law, reply affirmation, and other non-standard docs"""
    additional = []
    for key in ('memo_of_law', 'reply_affirmation'):
        text = docs.get(key, {}).get('text', '')
        if text:
            label = key.replace('_', ' ').upper()
            additional.append((label, text, 'secondary'))
    return additional


def _draft_appellant_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for appellant's brief — skips extraction passes"""
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    transcript_text = _truncate(docs.get('trial_transcript', {}).get('text', ''), MAX_SECONDARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)
    record_combined = _gather_record_volumes(docs)

    structure_block = _build_structure_prompt(structure)

    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    existing_draft_section = ""
    drafting_task = "Draft the complete appellant's brief following the attorney's structure."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft following the structure provided."

    # Fit supplementary documents
    doc_items = [
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('TRIAL TRANSCRIPT', transcript_text, 'secondary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ] + _gather_additional_docs(docs)
    fitted = _fit_documents(doc_items)
    doc_context = "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} an APPELLANT'S BRIEF arguing for reversal of the lower court decision.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{structure_block}

{existing_draft_section}=== SOURCE DOCUMENTS (for finding exact quotes and record cites) ===
{doc_context}

=== DRAFTING REQUIREMENTS ===

1. STRUCTURE: Follow the attorney's defined Points EXACTLY. Draft these sections IN ORDER:
   - QUESTIONS PRESENTED (based on the Points defined)
   - PRELIMINARY STATEMENT (using attorney's notes)
   - STATEMENT OF THE CASE (factual background with record cites)
   - ARGUMENT — one POINT for each Point the attorney defined, using the heading provided
   - CONCLUSION (requesting reversal)

   CRITICAL: This is an APPELLANT'S brief. Use ONLY these section headings.
   Do NOT use "Counter-Statement" or "Counterstatement" — those are for respondent's briefs.
   The fact section MUST be titled "STATEMENT OF THE CASE" — nothing else.

{_build_drafting_protocol()}

{_build_writing_style()}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    final_brief = guardrail_brief(final_brief, 'appellant', research_text)

    return final_brief, {'drafting_mode': 'structured'}


def _draft_respondent_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for respondent's brief — skips extraction passes"""
    appellant_text = _truncate(docs.get('appellant_brief', {}).get('text', ''), MAX_PRIMARY_CHARS)
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)
    record_combined = _gather_record_volumes(docs)

    structure_block = _build_structure_prompt(structure)

    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    existing_draft_section = ""
    drafting_task = "Draft the complete respondent's brief following the attorney's structure."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft following the structure provided."

    # Fit supplementary documents
    doc_items = [
        ('APPELLANT\'S OPENING BRIEF (ADVOCACY — NOT EVIDENCE)', appellant_text, 'primary'),
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ] + _gather_additional_docs(docs)
    fitted = _fit_documents(doc_items)
    doc_context = "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} a RESPONDENT'S BRIEF defending the lower court decision.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{structure_block}

{existing_draft_section}=== SOURCE DOCUMENTS (for finding exact quotes and record cites) ===
{doc_context}

=== DRAFTING REQUIREMENTS ===

1. STRUCTURE: Follow the attorney's defined Points EXACTLY. Draft:
   - PRELIMINARY STATEMENT (using attorney's notes)
   - COUNTERSTATEMENT OF QUESTIONS PRESENTED
   - COUNTERSTATEMENT OF FACTS (using attorney's factual background)
   - ARGUMENT — one POINT for each Point the attorney defined, using the heading provided
   - CONCLUSION (requesting affirmance)

2. WARNING — APPELLANT'S BRIEF IS ADVOCACY, NOT EVIDENCE:
   - Do NOT quote appellant's brief and cite record page numbers as if you verified the record
   - When referencing what appellant argues, ATTRIBUTE IT: "Appellant argues..." or "Appellant contends..."

{_build_drafting_protocol()}

{_build_writing_style()}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    final_brief = guardrail_brief(final_brief, 'respondent', research_text)

    return final_brief, {'drafting_mode': 'structured'}


def _sanitize_respondent_brief(text):
    """Strip record citations and quoted testimony from respondent's brief
    so the AI cannot mine it for facts. The respondent's brief is ADVOCACY —
    it should only be used to identify what arguments to refute, not as a
    source of facts or testimony quotes."""
    import re
    # Strip record citations: (R. 529), (R. 529-530), (R. at 529), (529), (529-530)
    # Replace with [respondent's record cite] so AI can't copy page numbers
    result = re.sub(r'\(R\.\s*(?:at\s*)?\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*\)', '[record cite removed]', text)
    result = re.sub(r'\((?:at\s*)?\d{2,4}(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*\)', '[record cite removed]', result)
    # Strip quoted testimony (strings in quotes longer than 20 chars)
    # Replace with summary so AI sees what respondent claims but can't copy the words
    def replace_long_quote(m):
        quote = m.group(1)
        if len(quote) > 20:
            return '[respondent\'s characterization of testimony removed — verify in actual record]'
        return m.group(0)
    result = re.sub(r'"([^"]{8,})"', replace_long_quote, result)
    result = re.sub(r'\u201c([^\u201d]{8,})\u201d', replace_long_quote, result)
    return result


def _gather_respondent_briefs(docs, sanitize=True):
    """Collect all respondent brief texts (respondent_brief, respondent_brief_2, etc.).
    If sanitize=True, strip record citations and quoted testimony so the AI
    cannot mine respondent briefs for facts."""
    briefs = []
    if 'respondent_brief' in docs and docs['respondent_brief'].get('text'):
        text = docs['respondent_brief']['text']
        if sanitize:
            text = _sanitize_respondent_brief(text)
        briefs.append(('RESPONDENT\'S BRIEF #1 (ADVOCACY — ARGUMENTS ONLY, NOT A SOURCE OF FACTS)', text, 'primary'))
    for key in sorted(docs.keys()):
        if key.startswith('respondent_brief_') and docs[key].get('text'):
            num = key.replace('respondent_brief_', '')
            text = docs[key]['text']
            if sanitize:
                text = _sanitize_respondent_brief(text)
            label = f"RESPONDENT'S BRIEF #{num} (ADVOCACY — ARGUMENTS ONLY, NOT A SOURCE OF FACTS)"
            briefs.append((label, text, 'primary'))
    return briefs


def _preprocess_opening_brief(opening_text):
    """Extract structure, terminology, and scope from the opening brief.
    Returns a concise constraint block that fits in the prompt without truncation.
    This is pure code — no API call. The AI MUST follow these constraints."""
    import re as _re

    constraints = []

    # --- 1. TERMINOLOGY ---
    plaintiff_count = len(_re.findall(r'\bplaintiff\b', opening_text, _re.IGNORECASE))
    appellant_count = len(_re.findall(r'\bappellant\b', opening_text, _re.IGNORECASE))
    # Don't double-count compound forms like "plaintiff-appellant"
    compound_count = len(_re.findall(r'\bplaintiff[- ]appellant\b', opening_text, _re.IGNORECASE))
    plaintiff_only = plaintiff_count - compound_count
    appellant_only = appellant_count - compound_count

    if plaintiff_only > appellant_only * 3:
        constraints.append(
            f'TERMINOLOGY (MANDATORY): The opening brief uses "plaintiff" {plaintiff_only} times '
            f'vs "appellant" only {appellant_only} times. YOU MUST use "plaintiff" throughout. '
            f'Do NOT use "appellant" unless quoting a case or the compound "plaintiff-appellant".'
        )
    elif appellant_only > plaintiff_only * 3:
        constraints.append(
            f'TERMINOLOGY (MANDATORY): The opening brief uses "appellant" {appellant_only} times '
            f'vs "plaintiff" only {plaintiff_only} times. YOU MUST use "appellant" throughout.'
        )

    # Check respondent vs defendant
    respondent_count = len(_re.findall(r'\brespondent\b', opening_text, _re.IGNORECASE))
    defendant_count = len(_re.findall(r'\bdefendant\b', opening_text, _re.IGNORECASE))
    compound_rd = len(_re.findall(r'\bdefendant[- ]respondent\b', opening_text, _re.IGNORECASE))
    respondent_only = respondent_count - compound_rd
    defendant_only = defendant_count - compound_rd

    if defendant_only > respondent_only * 3:
        constraints.append(
            f'The opening brief uses "defendant" {defendant_only} times vs "respondent" '
            f'{respondent_only} times. Use "defendant" when referring to the opposing party.'
        )
    elif respondent_only > defendant_only * 3:
        constraints.append(
            f'The opening brief uses "respondent" {respondent_only} times vs "defendant" '
            f'{defendant_only} times. Use "respondent" when referring to the opposing party.'
        )

    # --- 2. POINT HEADINGS AND SUB-HEADINGS ---
    # Find all POINT headings — they start with "POINT" followed by roman numeral
    # The heading text is on the same line or the next all-caps line(s)
    lines = opening_text.split('\n')
    points = []
    current_point = None
    current_subs = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Match POINT heading: "POINT I", "POINT II:", etc. (with optional colon)
        point_match = _re.match(r'^POINT\s+([IVX]+):?\s*$', stripped)
        if point_match:
            # Save previous point
            if current_point:
                points.append({'heading': current_point, 'subs': current_subs})
            # Collect the heading text from subsequent ALL-CAPS lines only
            heading_lines = [_re.sub(r':$', '', stripped)]  # strip trailing colon
            total_heading_len = len(stripped)
            for j in range(i + 1, min(i + 15, len(lines))):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                # Only include lines that are ALL CAPS or mostly uppercase
                # (Point headings in briefs are written in ALL CAPS)
                upper_chars = sum(1 for c in next_line if c.isupper())
                alpha_chars = sum(1 for c in next_line if c.isalpha())
                if alpha_chars == 0:
                    continue
                uppercase_ratio = upper_chars / alpha_chars
                if uppercase_ratio < 0.7:
                    break  # hit body text (mixed case)
                # Stop if heading is getting too long (> 400 chars = not a heading anymore)
                total_heading_len += len(next_line) + 1
                if total_heading_len > 400:
                    break
                heading_lines.append(next_line)
            current_point = ' '.join(heading_lines)
            current_subs = []
            current_point_line = i
            continue

        # Match sub-headings: "A. ...", "B. ...", "C. ..." etc.
        # Must be within 5000 chars of the Point heading (not from embedded case law)
        # Must be a SHORT heading (< 100 chars), NOT Q&A or testimony
        sub_match = _re.match(r'^(?:\t)?([A-C])\.\s+(.+)', stripped)
        if sub_match and current_point and len(stripped) < 100:
            # Only consider sub-headings within ~100 lines of the Point heading
            if hasattr(current_point_line, '__class__') and (i - current_point_line) > 100:
                continue
            sub_letter = sub_match.group(1)
            sub_text = sub_match.group(2).strip()
            # Only accept sub-headings that look like legal headings
            # Must contain a legal keyword
            legal_keywords = ('law', 'labor', 'negligence', 'liability', 'statutory',
                            'agent', 'precast', 'lomma', 'standard', 'summary',
                            'judgment', 'hoisting', 'rigging', 'duty', 'control',
                            'supervision', 'defect', 'proximate', 'burden')
            if any(kw in sub_text.lower() for kw in legal_keywords):
                current_subs.append(f'{sub_letter}. {sub_text}')

    # Don't forget the last point
    if current_point:
        points.append({'heading': current_point, 'subs': current_subs})

    if points:
        num_points = len(points)
        structure_lines = [
            f'STRUCTURE (MANDATORY): The opening brief has exactly {num_points} Point(s). '
            f'Your reply brief MUST have exactly {num_points} Point(s) matching these:'
        ]
        for p in points:
            structure_lines.append(f'\n  {p["heading"]}')
            for sub in p['subs']:
                structure_lines.append(f'    {sub}')

        structure_lines.append(
            f'\nDo NOT add, remove, or reorganize Points. Do NOT create sub-headings '
            f'for topics not covered under the corresponding Point in the opening brief.'
        )
        constraints.append('\n'.join(structure_lines))

    # --- 3. SCOPE EXCLUSIONS ---
    # Detect what the brief does NOT address by checking for common legal topics
    scope_topics = {
        'damages': r'\bdamages\b',
        'injuries': r'\binjur(?:y|ies)\b',
        'pain and suffering': r'\bpain\s+and\s+suffering\b',
        'causation': r'\bcausation\b',
        'comparative fault': r'\bcomparative\s+fault\b',
        'contributory negligence': r'\bcontributory\s+negligence\b',
        'bailment': r'\bbailment\b',
    }
    absent_topics = []
    for topic, pattern in scope_topics.items():
        count = len(_re.findall(pattern, opening_text, _re.IGNORECASE))
        if count == 0:
            absent_topics.append(topic)

    if absent_topics:
        constraints.append(
            'SCOPE EXCLUSIONS (MANDATORY): The following topics are NOT addressed in the '
            'opening brief and MUST NOT appear in the reply brief:\n  - '
            + '\n  - '.join(absent_topics)
            + '\nIf any of these appear in the respondent\'s brief, IGNORE them — they are '
            'outside the scope of this appeal.'
        )

    # --- 4. CASE LAW FROM OPENING BRIEF ---
    # Extract the most prominent cases (cited multiple times)
    case_pattern = r'_([^_]+?v\.?\s+[^_]+?)_'
    case_mentions = _re.findall(case_pattern, opening_text)
    if case_mentions:
        from collections import Counter
        case_counts = Counter(c.strip() for c in case_mentions)
        top_cases = case_counts.most_common(15)
        if top_cases:
            case_lines = ['KEY CASES FROM OPENING BRIEF (use these in your reply):']
            for case_name, count in top_cases:
                case_lines.append(f'  - {case_name} (cited {count}x)')
            constraints.append('\n'.join(case_lines))

    # --- BUILD FINAL BLOCK ---
    if not constraints:
        return ''

    return (
        '=== OPENING BRIEF CONSTRAINTS (MANDATORY — FOLLOW EXACTLY) ===\n'
        'These constraints were extracted directly from the attorney\'s opening brief.\n'
        'Violating ANY of these constraints is a CRITICAL ERROR.\n\n'
        + '\n\n'.join(constraints)
        + '\n=== END OPENING BRIEF CONSTRAINTS ===\n'
    )


def _draft_reply_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for reply brief — skips extraction passes"""
    opening_text = _truncate(docs.get('opening_brief', {}).get('text', ''), MAX_PRIMARY_CHARS)
    respondent_briefs = _gather_respondent_briefs(docs)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)
    record_combined = _gather_record_volumes(docs)
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)

    # Use pre-processed summaries if available
    summaries = project.get('summaries', {})
    transcript_quotes = _extract_transcript_quotes(docs, summaries=summaries) if summaries else ''

    structure_block = _build_structure_prompt(structure)

    # Pre-process opening brief to extract constraints (use FULL text, not truncated)
    full_opening_text = docs.get('opening_brief', {}).get('text', '')
    opening_brief_constraints = _preprocess_opening_brief(full_opening_text)

    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    existing_draft_section = ""
    drafting_task = "Draft an EXHAUSTIVE reply brief FOR APPELLANTS arguing for REVERSAL, following the attorney's structure EXACTLY. Every claim must be supported. Every respondent argument must be addressed and REFUTED. The conclusion must request REVERSAL."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft following the structure provided."

    # Fit supplementary documents
    doc_items = [
        ('APPELLANT\'S OPENING BRIEF', opening_text, 'primary'),
    ]
    for label, text, priority in respondent_briefs:
        doc_items.append((f'{label} (ADVOCACY — NOT EVIDENCE)', text, priority))
    doc_items += [
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ] + _gather_additional_docs(docs)
    fitted = _fit_documents(doc_items)
    doc_context = "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    resp_count = len(respondent_briefs)
    resp_note = f"There are {resp_count} respondent briefs. You must address arguments from ALL of them." if resp_count > 1 else ""

    prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} a REPLY BRIEF FOR APPELLANTS.

CRITICAL — YOU ARE WRITING FOR THE APPELLANTS (THE PARTY THAT LOST BELOW).
- Appellants are APPEALING the lower court's decision. They want REVERSAL.
- This REPLY BRIEF responds to RESPONDENT'S BRIEF(S) by showing why respondent's arguments fail.
{resp_note}
- Every Point must REFUTE a respondent argument and explain why the lower court ERRED.
- The CONCLUSION must ask for REVERSAL, NEVER affirmance.

{opening_brief_constraints}

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{structure_block}

{existing_draft_section}=== SOURCE DOCUMENTS (for finding exact quotes and record cites) ===
{doc_context}

{f"=== KEY TRANSCRIPT QUOTES (USE THESE VERBATIM) ==={chr(10)}{transcript_quotes}" if transcript_quotes else ""}

=== DRAFTING REQUIREMENTS ===

1. STRUCTURE: Follow the attorney's defined Points EXACTLY. Draft:
   - PRELIMINARY STATEMENT (using attorney's notes)
   - ARGUMENT — one POINT for each Point the attorney defined, using the heading provided
   - CONCLUSION (requesting REVERSAL)

2. SCOPE — MIRROR THE OPENING BRIEF:
   - Do NOT introduce issues, claims, or topics not raised in the opening brief
   - If the opening brief addresses only liability, do NOT discuss injuries or damages
   - The reply brief responds to respondent's arguments ON THE ISSUES THE APPELLANT RAISED

3. WARNING — RESPONDENT'S BRIEF IS ADVOCACY, NOT EVIDENCE:
   - Do NOT quote respondent's brief and cite record page numbers as if you verified the record
   - When referencing what respondent argues, ATTRIBUTE IT: "Respondent argues..." or "Respondent contends..."

{_build_drafting_protocol()}

{_build_writing_style()}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    final_brief = guardrail_brief(final_brief, 'reply', research_text, opening_brief_text=full_opening_text)

    return final_brief, {'drafting_mode': 'structured'}


def _gather_record_volumes(docs):
    """Collect all record volume texts"""
    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            vol_num = key.replace('record_vol_', '') if key.startswith('record_vol_') else '1'
            record_texts.append(f"--- RECORD VOL. {vol_num} ---\n{doc.get('text', '')}")
    return "\n\n".join(record_texts) if record_texts else ""


def _gather_legal_research(docs):
    """Collect all legal research texts (legal_research, legal_research_2, etc.)"""
    research_texts = []
    for key, doc in docs.items():
        if key == 'legal_research' or key.startswith('legal_research_'):
            label = key.replace('_', ' ').title()
            text = doc.get('text', '')
            if text:
                research_texts.append(f"--- {label} ---\n{text}")
    return "\n\n".join(research_texts) if research_texts else ""


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
    model = data.get('model', 'sonnet')
    brief_type = project.get('brief_type', 'reply')

    docs = project.get('documents', {})
    analysis = project.get('analysis', {})
    record_combined = _gather_record_volumes(docs)
    research_text = _gather_legal_research(docs)

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
        else:  # intro, conclusion — broad search across all point headings
            search_text = project.get('case_name', '')
            if structure_points:
                search_text += ' ' + ' '.join(pt.get('heading', '') for pt in structure_points)
        if search_text:
            terms = _extract_search_terms(search_text)
            if terms:
                record_combined = _search_record_pages(record_combined, terms, MAX_TOTAL_CHARS // 2)

    # Build document context based on brief type, with truncation
    if brief_type == 'appellant':
        brief_role = "an appellant's opening brief"
        doc_items = [
            ('LOWER COURT DECISION', docs.get('lower_court_decision', {}).get('text', ''), 'primary'),
            ('TRIAL TRANSCRIPT', docs.get('trial_transcript', {}).get('text', ''), 'secondary'),
            ('APPELLANT\'S APPENDIX', docs.get('appellant_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]
    elif brief_type == 'respondent':
        brief_role = "a respondent's brief"
        doc_items = [
            ('APPELLANT\'S OPENING BRIEF', docs.get('appellant_brief', {}).get('text', ''), 'primary'),
            ('LOWER COURT DECISION', docs.get('lower_court_decision', {}).get('text', ''), 'primary'),
            ('RESPONDENT\'S APPENDIX', docs.get('respondent_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]
    else:  # reply
        brief_role = "a reply brief"
        doc_items = [
            ('APPELLANT\'S OPENING BRIEF', docs.get('opening_brief', {}).get('text', ''), 'primary'),
        ] + _gather_respondent_briefs(docs) + [
            ('APPELLANT\'S APPENDIX', docs.get('appellant_appendix', {}).get('text', ''), 'secondary'),
            ('RESPONDENT\'S APPENDIX', docs.get('respondent_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]

    fitted = _fit_documents(doc_items)
    doc_context = "\n\n".join(f"--- {label} ---\n{text if text else '(Not uploaded)'}" for label, text in fitted)

    # Pre-process opening brief constraints for reply briefs
    reply_constraints = ''
    if brief_type == 'reply':
        full_opening = docs.get('opening_brief', {}).get('text', '')
        if full_opening:
            reply_constraints = _preprocess_opening_brief(full_opening)

    # Build task instruction
    if section_type == 'intro':
        if brief_type == 'appellant':
            task = "TASK: Draft the PRELIMINARY STATEMENT. Introduce the case and preview the errors that warrant reversal."
        elif brief_type == 'respondent':
            task = "TASK: Draft the PRELIMINARY STATEMENT. Introduce the case and preview why the lower court's decision should be affirmed."
        else:
            task = "TASK: Draft the INTRODUCTION section. Briefly state why the reply brief is necessary and preview the key responses."
    elif section_type == 'argument':
        task = f"TASK: Draft POINT {argument_number}. Use proper appellate brief formatting with point headings."
    elif section_type == 'conclusion':
        if brief_type == 'appellant':
            task = "TASK: Draft the CONCLUSION requesting reversal and specifying the relief sought."
        elif brief_type == 'respondent':
            task = "TASK: Draft the CONCLUSION requesting affirmance of the lower court's decision."
        else:
            task = "TASK: Draft the CONCLUSION section requesting specific relief."
    elif section_type == 'custom':
        task = f"""TASK: Draft the following section of the brief based on the attorney's instructions:

{custom_instructions}

Use the uploaded documents as your source material. Include proper record citations. Write in polished appellate prose. This is a standalone section — do NOT draft the entire brief, only the section described above."""
    else:
        task = ""

    prompt = f"""You are an expert appellate attorney drafting {brief_role}.

{reply_constraints}

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{argument_info}

DOCUMENTS PROVIDED:

{doc_context}

{_build_drafting_protocol()}

{_build_writing_style()}

{task}

REMINDER: Use ONLY cases and legal authorities found in the uploaded documents. NO outside research. If you need a case not in the documents, write [CASE CITE NEEDED]. Never fabricate citations.

FORMATTING REMINDER: Output PLAIN TEXT ONLY. NO markdown (no ##, no **, no *). Tab-indent body paragraphs. Section headings in ALL CAPS on their own line. Point headings: "POINT I" on one line, heading text in ALL CAPS on next line. Case names with _underscores_.

Draft the section now:"""

    max_tok = 8000 if section_type == 'custom' else 4000
    result = call_claude(prompt, max_tokens=max_tok, model=model)

    # Convert any bold case names to underscore format
    result = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', result)

    # Flag paragraphs where last sentence is missing a record cite
    result = enforce_paragraph_cites(result)

    # Insert full case citations from uploaded legal research
    result = enforce_case_cites(result, research_text)

    # Citation validation disabled - too many false positives
    # source_texts = [text for label, text in fitted if text]
    # result = validate_citations(result, *source_texts)

    section_key = f"{section_type}_{argument_number}" if section_type == 'argument' else section_type
    project['drafted_sections'][section_key] = {
        'content': result,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, project)

    return jsonify({
        'section': section_key,
        'content': result
    })


def _build_record_index(docs, opening_brief_text='', progress_callback=None):
    """Build a page-indexed evidence map from the record on appeal.

    Splits the record into pages by '--- PAGE X ---' markers, chunks them,
    and extracts key facts/testimony with correct RECORD page numbers.
    Returns a list of {record_page, fact, quote, witness, doc_type} dicts.
    """
    # Gather all record text
    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            record_texts.append(doc.get('text', ''))
    if not record_texts:
        # Fall back to appendix
        app_text = docs.get('appellant_appendix', {}).get('text', '')
        if app_text:
            record_texts.append(app_text)
    if not record_texts:
        return []

    full_record = '\n\n'.join(record_texts)

    # Split into pages
    import re as _re
    page_splits = _re.split(r'--- PAGE (\d+) ---', full_record)
    # page_splits = [preamble, page_num, content, page_num, content, ...]
    pages = []
    for i in range(1, len(page_splits) - 1, 2):
        page_num = int(page_splits[i])
        content = page_splits[i + 1].strip()
        if len(content) > 50:  # skip near-empty pages
            pages.append((page_num, content))

    if not pages:
        return []

    # Extract the issues from the opening brief to focus extraction
    focus = ''
    if opening_brief_text:
        points = _re.findall(r'(POINT\s+[IVX]+[^\n]*(?:\n[A-Z][^\n]+)*)', opening_brief_text)
        if points:
            focus = "LEGAL ISSUES ON APPEAL:\n" + "\n".join(p.strip() for p in points)

    # Chunk pages (20 per chunk)
    CHUNK_SIZE = 20
    chunks = []
    for i in range(0, len(pages), CHUNK_SIZE):
        group = pages[i:i + CHUNK_SIZE]
        text = "\n\n".join(f"[RECORD PAGE {pg}]\n{txt}" for pg, txt in group)
        page_range = f"{group[0][0]}-{group[-1][0]}"
        chunks.append({'text': text, 'range': page_range})

    total_chunks = len(chunks)
    if progress_callback:
        progress_callback('extraction', 0, total_chunks, f'Indexing record: {len(pages)} pages in {total_chunks} chunks')

    # Extract facts from each chunk
    api_key = os.getenv('ANTHROPIC_API_KEY')
    client = Anthropic(api_key=api_key)
    all_facts = []

    EXTRACTION_PROMPT = """You are a Legal Record Indexer. Extract key facts, testimony, and evidence from this chunk of an appellate record.

RULES:
1. OUTPUT: A JSON array of objects. Each object:
   {"record_page": <number>, "witness": "<name or empty>", "doc_type": "<testimony|decision|affirmation|exhibit|pleading>", "fact": "<brief description>", "quote": "<exact quoted text if testimony>"}
2. RECORD PAGE NUMBER: Use the number from [RECORD PAGE X] markers. This is CRITICAL — the page number must match exactly.
3. For TESTIMONY pages (Q&A format): extract key admissions, statements about facts, descriptions of events. Include the exact Q&A text in "quote".
4. For COURT DECISIONS: extract key findings, rulings, and legal conclusions.
5. For AFFIRMATIONS/AFFIDAVITS: extract factual assertions.
6. For PLEADINGS: skip — these are not useful for drafting.
7. HIGH RECALL: Extract everything potentially relevant to the legal issues.
8. Output ONLY the JSON array. No preamble, no markdown. Start with [ end with ]."""

    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback('extraction', i + 1, total_chunks, f'Indexing pages {chunk["range"]}...')

        user_prompt = f"""{focus}

RECORD CHUNK (Pages {chunk['range']}):
{chunk['text']}"""

        try:
            response = client.messages.create(
                model='claude-sonnet-4-20250514',
                max_tokens=8000,
                system=EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            result_text = response.content[0].text.strip()
            if result_text.startswith('```'):
                result_text = _re.sub(r'^```\w*\n?', '', result_text)
                result_text = _re.sub(r'\n?```$', '', result_text)
            try:
                facts = json.loads(result_text)
                all_facts.extend(facts)
            except json.JSONDecodeError:
                match = _re.search(r'\[.*\]', result_text, _re.DOTALL)
                if match:
                    try:
                        all_facts.extend(json.loads(match.group()))
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"Error indexing chunk {chunk['range']}: {e}")

    # Sort by page
    all_facts.sort(key=lambda f: int(f.get('record_page', 0)) if str(f.get('record_page', '')).isdigit() else 0)

    # Deduplicate
    seen = set()
    unique = []
    for fact in all_facts:
        key = (fact.get('record_page', 0), fact.get('fact', '')[:50].lower())
        if key not in seen:
            seen.add(key)
            unique.append(fact)

    if progress_callback:
        progress_callback('complete', total_chunks, total_chunks, f'Done: {len(unique)} facts indexed')

    return unique


def _format_record_index_for_prompt(record_index):
    """Format the record index as a text block for the drafting prompt."""
    if not record_index:
        return ''
    lines = []
    for fact in record_index:
        pg = fact.get('record_page', '?')
        doc_type = fact.get('doc_type', '')
        witness = fact.get('witness', '')
        fact_text = fact.get('fact', '')
        quote = fact.get('quote', '')

        if doc_type == 'pleading':
            continue  # skip pleadings

        line = f"(PAGE {pg})"
        if witness:
            line += f" [{witness}]"
        if doc_type:
            line += f" [{doc_type}]"
        line += f" {fact_text}"
        if quote:
            line += f' — "{quote}"'
        lines.append(line)

    return "=== RECORD INDEX (USE THESE PAGE NUMBERS) ===\n" + "\n".join(lines)


def _extract_record_evidence(docs):
    """Pass to extract key record/appendix evidence (shared across brief types)"""
    appellant_appendix_text = docs.get('appellant_appendix', {}).get('text', '')
    respondent_appendix_text = docs.get('respondent_appendix', {}).get('text', '')

    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            record_texts.append(doc.get('text', ''))
    record_combined = "\n\n".join(record_texts) if record_texts else ""

    record_source = appellant_appendix_text if appellant_appendix_text else record_combined
    if len(record_source) > 400000:
        record_source = record_source[:400000]

    prompt = f"""You are a legal research assistant. Extract KEY TESTIMONY and EVIDENCE from this appellate record/appendix.

Focus on:
- Direct quotes from testimony
- Key admissions or statements
- Documents referenced
- Timeline events

RECORD/APPENDIX:
{record_source}

{f"RESPONDENT'S APPENDIX:{chr(10)}{respondent_appendix_text[:100000] if len(respondent_appendix_text) > 100000 else respondent_appendix_text}" if respondent_appendix_text else ""}

FORMAT YOUR RESPONSE AS:

(page number): "[exact quote or description]"
SIGNIFICANCE: [why this matters]
---

IMPORTANT: Use ONLY the page number in parentheses. NO "R." or "A." prefix.
Example: (125): "The witness testified..."
NOT: (R. 125) or (A. 125)

Extract the most important moments with EXACT page numbers."""

    return call_claude(prompt, max_tokens=8000)


def _extract_transcript_quotes(docs, summaries=None):
    """Pass to extract key transcript quotes (shared across brief types).

    If a two-pass summary exists for any transcript document, uses the
    pre-processed narrative instead of raw extraction.
    """
    # Check for pre-processed summaries first
    if summaries:
        summary_parts = []
        for doc_type in ('trial_transcript', 'appellant_appendix', 'record'):
            summary = summaries.get(doc_type)
            if summary and summary.get('narrative'):
                summary_parts.append(summary['narrative'])
        # Also check record volumes
        for key in summaries:
            if key.startswith('record_vol_') and summaries[key].get('narrative'):
                summary_parts.append(summaries[key]['narrative'])

        if summary_parts:
            return '\n\n'.join(summary_parts)

    # Fall back to legacy extraction
    appellant_appendix_text = docs.get('appellant_appendix', {}).get('text', '')
    respondent_appendix_text = docs.get('respondent_appendix', {}).get('text', '')

    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            record_texts.append(doc.get('text', ''))
    record_combined = "\n\n".join(record_texts) if record_texts else ""

    source_text = appellant_appendix_text if appellant_appendix_text else record_combined

    if len(source_text) > 400000:
        transcript_pages = []
        pages = source_text.split('--- PAGE ')
        for page in pages:
            if any(marker in page for marker in ['THE COURT:', 'MR. ', 'MS. ', 'Q.', 'A.', 'BY MR.', 'BY MS.']):
                transcript_pages.append('--- PAGE ' + page if not page.startswith('---') else page)
        if transcript_pages:
            source_text = '\n\n'.join(transcript_pages[:100])
        else:
            source_text = source_text[:400000]

    prompt = f"""You are a legal research assistant extracting KEY TRANSCRIPT QUOTES from appellate record/appendix.

YOUR MISSION: Find the KILLER QUOTES - the exact words spoken that win or lose the argument.

Focus on extracting EXACT QUOTES of:
1. JUDGE STATEMENTS - What the judge said on the record
2. ATTORNEY STATEMENTS - What attorneys said during proceedings
3. KEY ADMISSIONS - Any party admitting damaging facts
4. COURT RULINGS - Exact words of any rulings
5. WITNESS TESTIMONY - Critical testimony quotes
6. PROCEDURAL STATEMENTS - Statements about stays, adjournments, withdrawals

RECORD/APPENDIX TO SEARCH:
{source_text}

{f"RESPONDENT'S APPENDIX:{chr(10)}{respondent_appendix_text[:100000] if len(respondent_appendix_text) > 100000 else respondent_appendix_text}" if respondent_appendix_text else ""}

FORMAT - USE EXACT QUOTES WITH PAGE NUMBERS:

**QUOTE ([page])**: "[EXACT words spoken - copy verbatim]"
**SPEAKER**: [Judge/Attorney name if known]
**CONTEXT**: [Brief description of what was happening]
**WHY IT MATTERS**: [How this quote helps or hurts the case]
---

Extract EVERY significant quote. Use EXACT WORDS - do not paraphrase. Include the page number in parentheses with period after: (91).

This is critical - these quotes will be used verbatim in the brief."""

    return call_claude(prompt, max_tokens=8000)


def _draft_appellant_brief(project, docs, drafting_instructions='', model='sonnet'):
    """4-pass drafting for appellant's brief"""
    structure = project.get('brief_structure')
    if structure and structure.get('points'):
        return _draft_appellant_brief_structured(project, docs, structure, drafting_instructions, model)

    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    transcript_text = _truncate(docs.get('trial_transcript', {}).get('text', ''), MAX_SECONDARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)

    # Pass 1: Extract record facts
    record_evidence = _extract_record_evidence(docs)

    # Pass 2: Extract lower court reasoning
    pass2_prompt = f"""You are a legal research assistant. Extract the COMPLETE REASONING of the lower court decision.

LOWER COURT DECISION:
{decision_text}

For EACH ruling or finding, extract:
1. The specific finding or ruling
2. The legal standard the court applied
3. The facts the court relied on
4. Any cases the court cited and what it said about them

FORMAT:
RULING: [What the court decided]
STANDARD APPLIED: [Legal test or standard used]
FACTS RELIED ON: [What evidence the court cited]
CASES CITED: [Cases and how court used them]
POTENTIAL ERROR: [Why this might be wrong]
---

Be exhaustive. Extract every significant ruling and finding."""

    court_reasoning = call_claude(pass2_prompt, max_tokens=8000, model=model)

    # Pass 3: Extract case law from research and transcript
    sources_for_cases = decision_text
    if research_text:
        sources_for_cases += f"\n\nLEGAL RESEARCH:\n{research_text}"

    pass3_prompt = f"""You are a legal research assistant. Extract EVERY case citation from these documents.

{sources_for_cases}

For EACH case cited, extract:
1. Full case citation exactly as written
2. The holding or proposition it supports
3. Where it appears in the document

FORMAT:
CASE: [Full citation]
HOLDING: "[what the case holds]"
CONTEXT: [How it's used in the document]
---

Extract ALL cases. Do not summarize - use exact quotes."""

    case_law = call_claude(pass3_prompt, max_tokens=8000, model=model)

    # Build attorney instructions block if provided
    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
The attorney has provided the following specific instructions for drafting this brief.
These instructions take priority over general drafting guidance. Follow them closely:

{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    # Pass 4: Draft the full brief (or complete existing draft)
    existing_draft_section = ""
    drafting_task = "Draft the complete appellant's brief now."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
The attorney has uploaded their work-in-progress brief. Your job is to:
1. PRESERVE all existing content that is well-written
2. COMPLETE any incomplete sections (marked with [...] or obviously unfinished)
3. STRENGTHEN weak arguments using the case law and record evidence provided
4. FIX any citation format issues to match the required format
5. ADD any missing sections required by the structure below

EXISTING DRAFT:
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft. Preserve their voice and arguments while completing unfinished sections and strengthening weak points."

    pass4_prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} an APPELLANT'S BRIEF arguing for reversal of the lower court decision.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{existing_draft_section}=== LOWER COURT REASONING (extracted) ===
{court_reasoning}

=== KEY RECORD EVIDENCE ===
{record_evidence}

=== CASE LAW FROM DOCUMENTS ===
{case_law}

=== LOWER COURT DECISION (full text) ===
{decision_text}

{f"=== TRIAL TRANSCRIPT ==={chr(10)}{transcript_text[:200000]}" if transcript_text else ""}

=== DRAFTING REQUIREMENTS ===

1. STRUCTURE:
   - QUESTIONS PRESENTED (numbered list of legal questions for the court)
   - PRELIMINARY STATEMENT (brief overview of the case and why reversal is warranted)
   - STATEMENT OF THE CASE (factual and procedural history from the record)
   - ARGUMENT
     - POINT I, II, III, etc. (one for EACH error identified)
     - Each point should have a point heading stating the argument as a proposition
   - CONCLUSION (requesting specific relief: reversal, remand, etc.)

2. CASE CITATIONS - NEW YORK OFFICIAL FORMAT:
   - Use NEW YORK OFFICIAL CITATION FORMAT: _Case Name_, 123 AD3d 456 [2d Dept 2020]
   - Case names must use UNDERSCORES for underlining: _Case Name v. Other Party_
   - DO NOT use **asterisks** - use _underscores_ only
   - Include full official citation: volume, reporter, page, and [court year] in brackets
   - The court and year MUST be in SQUARE BRACKETS [ ], NEVER parentheses ( )
   - WRONG: 123 AD3d 456 (2d Dept 2020) — parentheses are INCORRECT
   - CORRECT: 123 AD3d 456 [2d Dept 2020] — brackets are REQUIRED
   - Example: _Smith v. Jones_, 185 AD3d 789 [2d Dept 2020]
   - DO NOT use Westlaw or unofficial formats
   - Use ONLY cases found in the uploaded documents

3. RECORD CITATIONS:
   - Format: (page number). with period AFTER parenthesis
   - NEVER use "R." or "A." prefix - just the number
   - CORRECT: (45). CORRECT: (123).

4. LENGTH AND DEPTH:
   - This must be a COMPREHENSIVE brief, not a summary
   - Each POINT should be 2-4 pages of detailed argument
   - The brief should be 15-25 pages when formatted

5. FORMATTING - CRITICAL (PLAIN TEXT, NO MARKDOWN):
   - NEVER use ## or # or ** or * or any markdown syntax
   - Output PLAIN TEXT ONLY
   - Section headings: plain ALL CAPS on their own line (e.g., PRELIMINARY STATEMENT)
   - Point headings: "POINT I" on its own line, then the heading text in ALL CAPS on the next line
   - Sub-headings: tab + letter + tab + text (e.g., \tA.\tThe Court Erred...)
   - Body paragraphs: Start each paragraph with a tab character
   - Block quotes: Indent with two tabs
   - Blank line between paragraphs and before/after headings
   - Case names: _underscores_ only, NEVER **asterisks**

{_build_drafting_protocol()}

{_build_writing_style()}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(pass4_prompt, max_tokens=16000, model=model)

    # Convert any bold case names to underscore format
    final_brief = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', final_brief)

    # Citation validation disabled - too many false positives
    # final_brief = validate_citations(
    #     final_brief,
    #     decision_text,      # lower court decision
    #     existing_draft,     # existing draft (for case validation)
    #     research_text,      # legal research upload
    #     case_law,           # Pass 3 extracted cases
    # )

    return final_brief, {
        'court_reasoning': court_reasoning,
        'record_evidence': record_evidence,
        'case_law': case_law,
    }


def _draft_respondent_brief(project, docs, drafting_instructions='', model='sonnet'):
    """4-pass drafting for respondent's brief"""
    structure = project.get('brief_structure')
    if structure and structure.get('points'):
        return _draft_respondent_brief_structured(project, docs, structure, drafting_instructions, model)

    appellant_text = _truncate(docs.get('appellant_brief', {}).get('text', ''), MAX_PRIMARY_CHARS)
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)

    # Pass 1: Extract cases from appellant's brief
    pass1_prompt = f"""You are a legal research assistant. Extract EVERY case citation from this appellant's opening brief.

APPELLANT'S OPENING BRIEF:
{appellant_text}

For EACH case cited, extract:
1. Full case citation exactly as written
2. The EXACT QUOTE showing appellant's argument about this case
3. Page number in appellant's brief where cited

FORMAT:
CASE: [Full citation]
APPELLANT CLAIMS: "[exact quote from brief about what case holds]"
BRIEF PAGE: [page number]
---

Extract ALL cases. Do not summarize - use exact quotes."""

    appellant_cases = call_claude(pass1_prompt, max_tokens=8000, model=model)

    # Pass 2: Extract record evidence supporting affirmance
    record_evidence = _extract_record_evidence(docs)

    # Pass 3: Extract respondent's case law
    sources_for_cases = decision_text
    if research_text:
        sources_for_cases += f"\n\nLEGAL RESEARCH:\n{research_text}"

    pass3_prompt = f"""You are a legal research assistant. Extract EVERY case citation from these documents that could support AFFIRMING the lower court decision.

{sources_for_cases}

For EACH case cited, extract:
1. Full case citation exactly as written
2. The holding or proposition
3. How it supports affirmance

FORMAT:
CASE: [Full citation]
HOLDING: "[what the case holds]"
SUPPORTS AFFIRMANCE BECAUSE: [explanation]
---

Extract ALL cases."""

    respondent_cases = call_claude(pass3_prompt, max_tokens=8000, model=model)

    # Build attorney instructions block if provided
    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
The attorney has provided the following specific instructions for drafting this brief.
These instructions take priority over general drafting guidance. Follow them closely:

{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    # Pass 4: Draft the full brief (or complete existing draft)
    existing_draft_section = ""
    drafting_task = "Draft the complete respondent's brief now."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
The attorney has uploaded their work-in-progress brief. Your job is to:
1. PRESERVE all existing content that is well-written
2. COMPLETE any incomplete sections (marked with [...] or obviously unfinished)
3. STRENGTHEN weak arguments using the case law and record evidence provided
4. FIX any citation format issues to match the required format
5. ADD any missing sections required by the structure below

EXISTING DRAFT:
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft. Preserve their voice and arguments while completing unfinished sections and strengthening weak points."

    pass4_prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} a RESPONDENT'S BRIEF defending the lower court decision.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{existing_draft_section}=== CASES FROM APPELLANT'S BRIEF ===
{appellant_cases}

=== KEY RECORD EVIDENCE ===
{record_evidence}

=== CASES SUPPORTING AFFIRMANCE ===
{respondent_cases}

=== APPELLANT'S OPENING BRIEF (ADVOCACY — NOT EVIDENCE) ===
WARNING: This is the opposing party's ARGUMENT. It is NOT a factual source.
- Do NOT quote this brief and cite record page numbers as if you verified the record
- Do NOT adopt appellant's characterizations as fact
- When referencing what appellant argues, ATTRIBUTE IT: "Appellant argues..." or "Appellant claims..."
- If appellant quotes a record page, VERIFY against the actual record text before citing that page
{_truncate(appellant_text, MAX_PRIMARY_CHARS)}

=== LOWER COURT DECISION (EVIDENTIARY SOURCE — THIS IS FACTUAL) ===
{_truncate(decision_text, MAX_SECONDARY_CHARS)}

=== DRAFTING REQUIREMENTS ===

1. STRUCTURE:
   - PRELIMINARY STATEMENT (overview and why the decision below should be affirmed)
   - COUNTERSTATEMENT OF QUESTIONS PRESENTED (reframe appellant's questions favorably)
   - COUNTERSTATEMENT OF FACTS (present facts supporting affirmance with record cites)
   - ARGUMENT
     - POINT I, II, III, etc. (responding to each of appellant's arguments)
     - Each point should have a point heading stating why appellant's argument fails
   - CONCLUSION (requesting affirmance)

2. FOR EACH OF APPELLANT'S ARGUMENTS:
   - Quote what appellant claims, ALWAYS attributing: "Appellant argues..." or "Appellant contends..."
   - NEVER present appellant's characterizations as objective facts
   - NEVER quote language from appellant's brief and cite a record page as the source
   - Explain why their cases are distinguishable or support affirmance
   - Point to ACTUAL record evidence they ignore (verify against the record, not the brief)
   - Show the lower court correctly applied the law
   - Raise preservation/waiver issues where applicable

3. CASE CITATIONS - NEW YORK OFFICIAL FORMAT:
   - Use NEW YORK OFFICIAL CITATION FORMAT: _Case Name_, 123 AD3d 456 [2d Dept 2020]
   - Case names must use UNDERSCORES for underlining: _Case Name v. Other Party_
   - DO NOT use **asterisks** - use _underscores_ only
   - Include full official citation: volume, reporter, page, and [court year] in SQUARE BRACKETS
   - WRONG: _Smith v. Jones_, 185 AD3d 789 (2d Dept 2020) — parentheses are INCORRECT
   - CORRECT: _Smith v. Jones_, 185 AD3d 789 [2d Dept 2020] — brackets are REQUIRED
   - The court and year MUST be in [square brackets], NEVER (parentheses)
   - DO NOT use Westlaw or unofficial formats
   - Use ONLY cases found in the uploaded documents

4. RECORD CITATIONS:
   - Format: (page number). with period AFTER parenthesis
   - NEVER use "R." or "A." prefix

5. LENGTH AND DEPTH:
   - COMPREHENSIVE response to every argument
   - Each POINT should be 2-4 pages
   - 15-25 pages when formatted

6. FORMATTING - CRITICAL (PLAIN TEXT, NO MARKDOWN):
   - NEVER use ## or # or ** or * or any markdown syntax
   - Output PLAIN TEXT ONLY
   - Section headings: plain ALL CAPS on their own line (e.g., PRELIMINARY STATEMENT)
   - Point headings: "POINT I" on its own line, then the heading text in ALL CAPS on the next line
   - Sub-headings: tab + letter + tab + text (e.g., \tA.\tThe Court Correctly Found...)
   - Body paragraphs: Start each paragraph with a tab character
   - Block quotes: Indent with two tabs
   - Blank line between paragraphs and before/after headings
   - Case names: _underscores_ only, NEVER **asterisks**

{_build_drafting_protocol()}

{_build_writing_style()}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(pass4_prompt, max_tokens=16000, model=model)

    # Convert any bold case names to underscore format
    final_brief = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', final_brief)

    # Citation validation disabled - too many false positives
    # final_brief = validate_citations(
    #     final_brief,
    #     appellant_text,     # appellant's opening brief
    #     existing_draft,     # existing draft (for case validation)
    #     decision_text,      # lower court decision
    #     research_text,      # legal research upload
    #     appellant_cases,    # Pass 1 extracted cases
    #     respondent_cases,   # Pass 3 extracted cases
    # )

    return final_brief, {
        'appellant_cases': appellant_cases,
        'record_evidence': record_evidence,
        'respondent_cases': respondent_cases,
    }


def _draft_reply_brief(project, docs, drafting_instructions='', model='sonnet'):
    """5-pass drafting for reply brief — existing logic preserved"""
    structure = project.get('brief_structure')
    if structure and structure.get('points'):
        return _draft_reply_brief_structured(project, docs, structure, drafting_instructions, model)

    opening_text = _truncate(docs.get('opening_brief', {}).get('text', ''), MAX_PRIMARY_CHARS)
    # RAW respondent text for extraction passes (cases, arguments)
    respondent_briefs_raw = _gather_respondent_briefs(docs, sanitize=False)
    respondent_text_raw = '\n\n'.join(text for _, text, _ in respondent_briefs_raw)
    respondent_text_raw = _truncate(respondent_text_raw, MAX_PRIMARY_CHARS)
    # SANITIZED respondent text for drafting pass (no record cites or quoted testimony)
    respondent_briefs_sanitized = _gather_respondent_briefs(docs, sanitize=True)
    respondent_text_sanitized = '\n\n'.join(text for _, text, _ in respondent_briefs_sanitized)
    respondent_text_sanitized = _truncate(respondent_text_sanitized, MAX_PRIMARY_CHARS)
    respondent_appendix_text = _truncate(docs.get('respondent_appendix', {}).get('text', ''), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)

    # Pre-process opening brief to extract structure, terminology, scope constraints
    # Uses the FULL opening brief text (before truncation) for better extraction
    full_opening_text = docs.get('opening_brief', {}).get('text', '')
    opening_brief_constraints = _preprocess_opening_brief(full_opening_text)

    # Pass 1: Extract cases from respondent's brief(s) — uses RAW text
    pass1_prompt = f"""You are a legal research assistant. Extract EVERY case citation from this respondent's brief(s).

RESPONDENT'S BRIEF(S):
{respondent_text_raw}

For EACH case cited, extract:
1. Full case citation exactly as written
2. The EXACT QUOTE showing what respondent claims the case holds
3. Page number in respondent's brief where cited

FORMAT YOUR RESPONSE AS:

CASE: [Full citation]
RESPONDENT CLAIMS: "[exact quote from brief about what case holds]"
BRIEF PAGE: [page number]
---

Extract ALL cases. Do not summarize - use exact quotes."""

    respondent_cases = call_claude(pass1_prompt, max_tokens=8000, model=model)

    # Pass 2: Extract cases from appellant's brief
    pass2_prompt = f"""You are a legal research assistant. Extract EVERY case citation from this appellant's opening brief.

APPELLANT'S OPENING BRIEF:
{opening_text}

For EACH case cited, extract:
1. Full case citation exactly as written
2. The EXACT QUOTE showing appellant's argument about this case
3. Page number in appellant's brief where cited

FORMAT YOUR RESPONSE AS:

CASE: [Full citation]
APPELLANT ARGUES: "[exact quote from brief]"
BRIEF PAGE: [page number]
---

Extract ALL cases. Do not summarize - use exact quotes."""

    appellant_cases = call_claude(pass2_prompt, max_tokens=8000, model=model)

    # Pass 3: Extract record evidence
    record_evidence = _extract_record_evidence(docs)

    # Pass 4: Extract transcript quotes (uses pre-processed summary if available)
    summaries = project.get('summaries', {})
    transcript_quotes = _extract_transcript_quotes(docs, summaries=summaries)

    # Build attorney instructions block if provided
    atty_instructions = ""
    if drafting_instructions:
        atty_instructions = f"""
=== ATTORNEY'S DRAFTING INSTRUCTIONS (HIGHEST PRIORITY) ===
The attorney has provided the following specific instructions for drafting this brief.
These instructions take priority over general drafting guidance. Follow them closely:

{drafting_instructions}
=== END ATTORNEY'S INSTRUCTIONS ===
"""

    # Build record index block if available
    record_index = project.get('record_index', [])
    record_index_block = _format_record_index_for_prompt(record_index) if record_index else ''

    # Pass 5: Draft the brief (or complete existing draft)
    existing_draft_section = ""
    drafting_task = "Draft an EXHAUSTIVE reply brief FOR APPELLANTS arguing for REVERSAL. Do not summarize - argue thoroughly with full citations. Every claim must be supported. Every respondent argument must be addressed and REFUTED. The conclusion must request REVERSAL of the lower court's order."
    if existing_draft:
        existing_draft_section = f"""
=== ATTORNEY'S EXISTING DRAFT (COMPLETE OR REVISE THIS) ===
The attorney has uploaded their work-in-progress brief. Your job is to:
1. PRESERVE all existing content that is well-written
2. COMPLETE any incomplete sections (marked with [...] or obviously unfinished)
3. STRENGTHEN weak arguments using the case law and record evidence provided
4. FIX any citation format issues to match the required format
5. ADD any missing sections required by the structure below

EXISTING DRAFT:
{existing_draft}
=== END EXISTING DRAFT ===

"""
        drafting_task = "Complete and polish the attorney's existing draft. Preserve their voice and arguments while completing unfinished sections and strengthening weak points."

    pass5_prompt = f"""You are an expert appellate attorney {"completing" if existing_draft else "drafting"} a REPLY BRIEF FOR APPELLANTS.

{opening_brief_constraints}

STEP 1 — READ THE OPENING BRIEF FIRST:
Before writing ANYTHING, you MUST carefully read the APPELLANT'S OPENING BRIEF provided below.
The opening brief defines:
- What ISSUES are on appeal (only address these issues — nothing else)
- What RECORD PAGE NUMBERS look like (use the same page numbers the opening brief uses)
- What CASES the appellant relies on
- What ARGUMENTS the appellant is making
- What TERMINOLOGY the attorney uses — if the opening brief says "plaintiff" instead of "appellant", YOU say "plaintiff". Mirror the attorney's language exactly.
Your reply brief must address ONLY the issues raised in the opening brief. Do NOT introduce new issues, new causes of action, or topics the opening brief does not address.

STEP 2 — READ THE RESPONDENT'S BRIEF(S):
Read what arguments the respondent makes in response to the opening brief.
Identify each argument the respondent makes and prepare to refute it.

STEP 3 — DRAFT THE REPLY:
For each respondent argument, draft a point-by-point refutation using:
- The record evidence (with RECORD page numbers matching the opening brief's citations)
- The case law from the opening brief and respondent's brief
- Direct quotes from the record (verified against the actual record text)

CRITICAL RULES:
- You are writing for the APPELLANTS (the party that lost below). They want REVERSAL.
- This REPLY BRIEF responds to RESPONDENT'S BRIEF by showing why respondent's arguments fail.
- Every Point must REFUTE a respondent argument and explain why the lower court ERRED.
- The CONCLUSION must ask for REVERSAL (or reversal and remand), NEVER affirmance.
- Do NOT adopt respondent's framing, characterizations, or conclusions.
- Do NOT argue that the lower court was correct — argue that it was WRONG.
- Do NOT introduce issues not in the opening brief. If the opening brief is about liability only, do NOT discuss injuries or damages.
- RECORD PAGE NUMBERS: Use the page numbers from the top center of each record page (after "--- PAGE X ---"). These are the same numbers the opening brief uses. Do NOT use internal transcript/deposition page numbers.

YOUR JOB: {"Complete the attorney's existing draft" if existing_draft else "Draft a reply brief FOR APPELLANTS"} that:
- ADDRESSES ONLY THE ISSUES IN THE OPENING BRIEF
- REFUTES each of respondent's key arguments with record evidence and case law
- QUOTES cases directly (use the extracts provided)
- QUOTES the record directly using RECORD page numbers (match the opening brief's citations)
- Distinguishes respondent's cases with SPECIFIC factual/legal distinctions
- Points to SPECIFIC record evidence respondent ignores
- Argues that the lower court's decision was ERROR and must be REVERSED

{existing_draft_section}=== CASES FROM RESPONDENT'S BRIEF ===
{respondent_cases}

=== CASES FROM APPELLANT'S BRIEF ===
{appellant_cases}

=== KEY RECORD EVIDENCE ===
{record_evidence}

{record_index_block}

=== KEY TRANSCRIPT QUOTES (USE THESE VERBATIM) ===
{transcript_quotes}

=== APPELLANT'S OPENING BRIEF ===
{opening_text}

=== RESPONDENT'S BRIEF (ADVOCACY — NOT EVIDENCE) ===
WARNING: This is the opposing party's ARGUMENT. It is NOT a factual source.
- Record citations and quoted testimony have been REMOVED from this text to prevent copying
- Do NOT quote this brief and cite record page numbers as if you verified the record
- Do NOT adopt respondent's characterizations or conclusions as fact
- When referencing what respondent argues, ATTRIBUTE IT: "Respondent argues..." or "Respondent contends..."
- ONLY cite facts from the RECORD ON APPEAL and TRANSCRIPT QUOTES sections above
{respondent_text_sanitized}

=== DRAFTING REQUIREMENTS ===

1. QUOTE CASES DIRECTLY:
   - Use NEW YORK OFFICIAL CITATION FORMAT: 123 AD3d 456 [2d Dept 2020]
   - Case names must use UNDERSCORES for underlining: _Case Name v. Other Party_
   - DO NOT use **asterisks** for case names - use _underscores_ only
   - The court and year MUST be in [square brackets], NEVER (parentheses)
   - WRONG: 123 AD3d 456 (2d Dept 2020) — parentheses are INCORRECT
   - CORRECT: 123 AD3d 456 [2d Dept 2020] — brackets are REQUIRED
   - Example: As this Court held in _Fan v Sabin_, "further proceedings" (125 AD3d at 499-500).

2. RECORD CITATIONS - CRITICAL FORMAT:
   - NEVER use "R." prefix - that is WRONG
   - NEVER use "A." prefix - that is WRONG
   - CORRECT format: (page number). with period AFTER parenthesis
   - WRONG: (R. 45). WRONG: (A. 123). WRONG: (R. 529-530).
   - CORRECT: (45). CORRECT: (123). CORRECT: (529-530).
   - Example: The court stated: "you are accordingly relieved" (91).
   - CRITICAL: Use the RECORD page number (the number after "--- PAGE X ---"), NOT the internal transcript/deposition page number. The record has its own continuous pagination. A deposition transcript embedded in the record at record page 135 may show "Page 47" internally — you MUST cite (135), NOT (47). Match the record page numbers used in the OPENING BRIEF.

3. DISTINGUISH RESPONDENT'S CASES:
   - Quote what respondent claims the case holds, ALWAYS attributing: "Respondent argues..."
   - NEVER present respondent's characterizations of the record as objective facts
   - NEVER quote language from respondent's brief and cite a record page as the source
   - Explain specifically why the case doesn't apply here
   - Point to ACTUAL record evidence (verify against the record, not the opposing brief)

4. STRUCTURE — MUST MATCH THE OPENING BRIEF:
   - PRELIMINARY STATEMENT
   - Use EXACTLY the same number of Points as the opening brief (see OPENING BRIEF CONSTRAINTS above)
   - Use the SAME Point headings as the opening brief
   - Do NOT add extra Points, split Points, or reorganize the structure
   - CONCLUSION

5. LENGTH AND DEPTH — MINIMUM 4,000 WORDS:
   - This must be a COMPREHENSIVE reply brief, not a summary
   - Each POINT should be 3-5 pages of detailed argument with sub-sections
   - Address EVERY significant argument respondent makes on the issues in the opening brief
   - For each respondent argument: state what respondent claims, explain why it's wrong, cite the record evidence and case law that disproves it
   - Include MULTIPLE case citations per point — distinguish EVERY case respondent cites
   - Use EXTENSIVE record citations throughout — quote the record directly
   - DO NOT SUMMARIZE — argue thoroughly, develop each argument fully
   - A longer, thorough brief is ALWAYS better than a short, superficial one

CRITICAL - USE THE TRANSCRIPT QUOTES:
The KEY TRANSCRIPT QUOTES section above contains verbatim quotes from the record. USE THEM.
- Copy quotes exactly as provided
- These quotes are your most powerful evidence - deploy them strategically

CRITICAL - CITATION FORMAT REMINDERS:
- Record cites: (page). NOT (R. page). NOT (A. page). Just the number.
- Case names: _underscored_ NOT **bolded**
- Period goes AFTER the closing parenthesis: (91). NOT (91.)

6. FORMATTING - CRITICAL (PLAIN TEXT, NO MARKDOWN):
   - NEVER use ## or # or ** or * or any markdown syntax
   - Output PLAIN TEXT ONLY
   - Section headings: plain ALL CAPS on their own line (e.g., PRELIMINARY STATEMENT)
   - Point headings: "POINT I" on its own line, then the heading text in ALL CAPS on the next line
   - Sub-headings: tab + letter + tab + text (e.g., \tA.\tRespondent's Reliance On...)
   - Body paragraphs: Start each paragraph with a tab character
   - Block quotes: Indent with two tabs
   - Blank line between paragraphs and before/after headings
   - Case names: _underscores_ only, NEVER **asterisks**

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(pass5_prompt, max_tokens=16000, model=model)

    # Run guardrail: strip markdown, fix citations, enforce terminology
    research_text = _truncate(_gather_legal_research(docs), MAX_SECONDARY_CHARS)
    final_brief = guardrail_brief(final_brief, 'reply', research_text, opening_brief_text=full_opening_text)

    return final_brief, {
        'respondent_cases': respondent_cases,
        'appellant_cases': appellant_cases,
        'record_evidence': record_evidence,
        'transcript_quotes': transcript_quotes,
    }


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

    # Save all passes for reference
    for key, value in research.items():
        project['drafted_sections'][key] = value
    project['drafted_sections']['full_brief'] = {
        'content': final_brief,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, project)

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
    research_text = _gather_legal_research(docs)

    if brief_type == 'appellant':
        doc_items = [
            ('LOWER COURT DECISION', docs.get('lower_court_decision', {}).get('text', ''), 'primary'),
            ('TRIAL TRANSCRIPT', docs.get('trial_transcript', {}).get('text', ''), 'secondary'),
            ('APPELLANT\'S APPENDIX', docs.get('appellant_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]
    elif brief_type == 'respondent':
        doc_items = [
            ('APPELLANT\'S OPENING BRIEF', docs.get('appellant_brief', {}).get('text', ''), 'primary'),
            ('LOWER COURT DECISION', docs.get('lower_court_decision', {}).get('text', ''), 'primary'),
            ('RESPONDENT\'S APPENDIX', docs.get('respondent_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]
    else:  # reply
        doc_items = [
            ('APPELLANT\'S OPENING BRIEF', docs.get('opening_brief', {}).get('text', ''), 'primary'),
        ] + _gather_respondent_briefs(docs) + [
            ('APPELLANT\'S APPENDIX', docs.get('appellant_appendix', {}).get('text', ''), 'secondary'),
            ('RESPONDENT\'S APPENDIX', docs.get('respondent_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]

    fitted = _fit_documents(doc_items)
    source_docs = "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    # Pre-process opening brief constraints for reply brief revisions
    revise_constraints = ''
    if brief_type == 'reply':
        full_opening = docs.get('opening_brief', {}).get('text', '')
        if full_opening:
            revise_constraints = _preprocess_opening_brief(full_opening)

    prompt = f"""You are an expert appellate attorney revising a brief.

{revise_constraints}

=== EXISTING BRIEF ===
{existing_brief}

=== REVISION INSTRUCTIONS ===
{revision_instructions}

=== ORIGINAL SOURCE DOCUMENTS (for reference) ===
{source_docs}

REVISION RULES - CRITICAL:

1. Apply ONLY the changes described in the revision instructions
2. Preserve ALL existing content that is not affected by the revisions
3. Keep all existing case citations intact unless specifically told to change them
4. Keep all existing record/appendix citations intact unless specifically told to change them
5. Do NOT add new cases from your training data — only use cases from the source documents above
6. Return the COMPLETE revised brief (not just the changed parts)

*** NO OMISSIONS — MANDATORY ***
You may condense or tighten prose, but you must NEVER omit arguments, points, or content.
- Every argument point in the original MUST appear in the revision
- Every case citation and its discussion MUST be preserved
- Every factual assertion with a record cite MUST be preserved
- Condensing a paragraph into tighter prose is fine
- DROPPING a paragraph, argument, or case discussion is NOT fine
- If the original has Points I through IV, the revision must have Points I through IV
- Omitting content is as bad as hallucinating content — both are unacceptable

*** QUOTATION MARKS — NEVER REMOVE ***
- If text is in quotation marks, it is a DIRECT QUOTE from testimony, a court decision, or a statute
- NEVER remove quotation marks from quoted language
- NEVER paraphrase text that is in quotation marks — the quotes indicate EXACT WORDS
- NEVER convert a direct quote into a paraphrase by dropping the quotation marks
- You may move a quoted passage or tighten surrounding prose, but the quoted text itself must remain verbatim and in quotation marks
- Adding quotation marks to language that was not quoted is equally wrong — do not fabricate quotes

*** CASE CITATION GUARDRAILS — ZERO TOLERANCE FOR FABRICATION ***

YOU ARE FORBIDDEN FROM INVENTING CASE NAMES.

YOUR ONLY SOURCES FOR CASE LAW ARE:
a) Cases already in the existing brief you are revising
b) Cases in the uploaded Legal Research document
c) Cases in any other uploaded source documents above

THAT'S IT. NO OTHER SOURCES.

BEFORE YOU WRITE ANY NEW CASE CITATION, ASK:
"Is this case in the existing brief OR in the uploaded documents?"
If NO → DO NOT CITE IT. Write [CASE CITE NEEDED] instead.

YOU MUST NOT:
- Cite ANY case from your training data
- Invent a case name that "sounds right"
- Guess at case names
- Fabricate holdings for real cases

CASE CITATION FORMAT:
- Case names MUST use UNDERSCORES: _Case Name v. Party_
- DO NOT use **asterisks** for case names
- NY Official format: _Case Name_, 123 AD3d 456 [2d Dept 2020]
- The court and year MUST be in SQUARE BRACKETS [ ], NEVER parentheses ( )
- WRONG: 123 AD3d 456 (2d Dept 2020) — DO NOT USE PARENTHESES for court/year
- CORRECT: 123 AD3d 456 [2d Dept 2020] — ALWAYS USE BRACKETS for court/year
- This applies to ALL reporters: AD2d, AD3d, NY2d, NY3d, Misc 2d, Misc 3d

RECORD CITATIONS - CRITICAL FORMAT:
- NEVER use "R." prefix - that is WRONG
- NEVER use "A." prefix - that is WRONG
- CORRECT format: (page number). with period AFTER parenthesis
- WRONG: (R. 45). WRONG: (A. 123).
- CORRECT: (45). CORRECT: (123).

ANTI-HALLUCINATION — ABSOLUTE:
- NEVER invent facts not in the source documents
- NEVER invent case names or holdings — this is malpractice
- NEVER use your training data for legal citations
- If unsure, write [VERIFY] rather than guess
- A brief with [CASE CITE NEEDED] is useful; a brief with fabricated citations is malpractice

FORMATTING - CRITICAL (PLAIN TEXT, NO MARKDOWN):
- NEVER use ## or # or ** or * or any markdown syntax
- Output PLAIN TEXT ONLY — this is a legal brief, not a markdown document
- Section headings: plain ALL CAPS on their own line (e.g., PRELIMINARY STATEMENT)
- Point headings: "POINT I" on its own line, then heading text in ALL CAPS on next line
- Sub-headings: tab + letter + tab + text
- Body paragraphs: Start each paragraph with a tab character
- Block quotes: Indent with two tabs
- Blank line between paragraphs and before/after headings
- Case names: _underscores_ only, NEVER **asterisks**
- Preserve the existing formatting style of the brief you are revising

{_build_writing_style()}

OUTPUT ONLY THE COMPLETE REVISED BRIEF TEXT. No commentary. PLAIN TEXT ONLY — NO MARKDOWN:"""

    model = data.get('model', 'sonnet')
    revised_text = call_claude(prompt, max_tokens=16000, model=model)

    # Convert any bold case names to underscore format
    revised_text = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', revised_text)

    # Citation validation disabled - too many false positives
    # source_texts = [text for label, text in fitted if text]
    # revised_text = validate_citations(revised_text, *source_texts)

    # Initialize revision tracking if not present
    if 'revision_count' not in project:
        project['revision_count'] = 0
    if 'revision_history' not in project:
        project['revision_history'] = []

    # Update project
    project['drafted_sections']['full_brief'] = {
        'content': revised_text,
        'drafted_at': datetime.now().isoformat()
    }
    project['revision_count'] = project['revision_count'] + 1
    project['revision_history'].append({
        'instructions': revision_instructions,
        'timestamp': datetime.now().isoformat(),
        'previous_brief': existing_brief
    })
    save_project(project_id, project)

    return jsonify({
        'revised_brief': revised_text,
        'revision_count': project['revision_count']
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
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_LINE_SPACING, WD_ALIGN_PARAGRAPH
    import re

    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    brief_type = project.get('brief_type', 'reply')
    config = BRIEF_TYPE_CONFIG.get(brief_type, BRIEF_TYPE_CONFIG['reply'])

    # Create Word document
    doc = DocxDocument()

    # Set 1-inch margins on all sides
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    # Set default style to Courier New, 12pt, double-spaced
    style = doc.styles['Normal']
    style.font.name = 'Courier New'
    style.font.size = Pt(12)
    style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.space_before = Pt(0)

    def _clean_text(text):
        """Strip markdown and fix citation formatting"""
        # Strip markdown heading markers
        text = re.sub(r'^#{1,6}\s*', '', text)
        # Convert **bold** to plain text (bold handled separately for headings)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = text.replace('**', '')
        # Remove stray asterisks used for emphasis
        text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r'\1', text)
        # Fix record citation prefixes
        text = re.sub(r'\(R\.\s*(\d+[^)]*)\)', r'(\1)', text)
        text = re.sub(r'\(A\.\s*(\d+[^)]*)\)', r'(\1)', text)
        # Fix case citation periods: A.D.3d → AD3d, A.D.2d → AD2d, N.Y.S.2d → NYS2d, etc.
        text = re.sub(r'A\.D\.3d', 'AD3d', text)
        text = re.sub(r'A\.D\.2d', 'AD2d', text)
        text = re.sub(r'N\.Y\.S\.3d', 'NYS3d', text)
        text = re.sub(r'N\.Y\.S\.2d', 'NYS2d', text)
        text = re.sub(r'N\.Y\.3d', 'NY3d', text)
        text = re.sub(r'N\.Y\.2d', 'NY2d', text)
        text = re.sub(r'N\.E\.3d', 'NE3d', text)
        text = re.sub(r'N\.E\.2d', 'NE2d', text)
        text = re.sub(r'Misc\.?\s*3d', 'Misc 3d', text)
        text = re.sub(r'Misc\.?\s*2d', 'Misc 2d', text)
        return text

    def _is_heading(text):
        """Detect if a line is a section heading (ALL CAPS, short, no period at end)"""
        stripped = text.strip()
        if not stripped:
            return False
        # Remove leading tabs for detection
        clean = stripped.lstrip('\t').strip()
        if not clean:
            return False
        # Known heading patterns
        heading_patterns = [
            r'^POINT\s+[IVXLCDM]+',
            r'^PRELIMINARY STATEMENT',
            r'^STATEMENT OF THE CASE',
            r'^STATEMENT OF FACTS',
            r'^COUNTERSTATEMENT',
            r'^QUESTIONS PRESENTED',
            r'^ARGUMENT',
            r'^CONCLUSION',
            r'^DISCUSSION',
            r'^BRIEF FOR',
            r'^REPLY BRIEF',
            r'^SUPREME COURT',
            r'^APPELLATE DIVISION',
        ]
        for pattern in heading_patterns:
            if re.match(pattern, clean):
                return True
        # General ALL CAPS detection: mostly uppercase letters, no lowercase sentences
        alpha_chars = [c for c in clean if c.isalpha()]
        if len(alpha_chars) > 3:
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            if upper_ratio > 0.85 and len(clean) < 200:
                return True
        return False

    def _is_subheading(text):
        """Detect sub-headings like A., B., 1., 2. at start"""
        stripped = text.strip().lstrip('\t')
        return bool(re.match(r'^[A-Z]\.\s', stripped) or re.match(r'^\d+\.\s', stripped))

    def _add_hyperlink(paragraph, url, text, font_size_pt=12):
        """Add a clickable hyperlink run to a paragraph."""
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        part = paragraph.part
        r_id = part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
        hyperlink = OxmlElement('w:hyperlink')
        hyperlink.set(qn('r:id'), r_id)
        run_elem = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:ascii'), 'Courier New')
        rFonts.set(qn('w:hAnsi'), 'Courier New')
        rPr.append(rFonts)
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), str(font_size_pt * 2))
        rPr.append(sz)
        color = OxmlElement('w:color')
        color.set(qn('w:val'), '0000FF')
        rPr.append(color)
        u = OxmlElement('w:u')
        u.set(qn('w:val'), 'single')
        rPr.append(u)
        run_elem.append(rPr)
        t = OxmlElement('w:t')
        t.set(qn('xml:space'), 'preserve')
        t.text = text
        run_elem.append(t)
        hyperlink.append(run_elem)
        paragraph._p.append(hyperlink)

    def _add_run(p, text, is_bold=False):
        """Add a plain Courier New 12pt run."""
        run = p.add_run(text)
        run.font.name = 'Courier New'
        run.font.size = Pt(12)
        if is_bold:
            run.bold = True

    def _add_text_with_citations(p, text, nyscef_cfg, is_bold=False):
        """Split text on record citations, inserting hyperlinks where NYSCEF URLs resolve."""
        citation_pat = re.compile(r'(\(\d+(?:\s*-\s*\d+)?\))')
        segments = citation_pat.split(text)
        for segment in segments:
            m = re.match(r'\((\d+)(?:\s*-\s*\d+)?\)', segment)
            if m:
                page = int(m.group(1))
                url = resolve_nyscef_url(page, nyscef_cfg)
                if url:
                    _add_run(p, '(', is_bold)
                    _add_hyperlink(p, url, segment[1:-1])
                    _add_run(p, ')', is_bold)
                else:
                    _add_run(p, segment, is_bold)
            elif segment:
                _add_run(p, segment, is_bold)

    nyscef_cfg = project.get('nyscef_config')

    def _add_paragraph(doc, text, is_bold=False, alignment=None, link_citations=False):
        """Add a paragraph with Courier New 12pt, double-spaced, with underlined case names"""
        text = _clean_text(text)
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        if alignment:
            p.alignment = alignment

        use_links = link_citations and nyscef_cfg

        # Split on underscored case names
        parts = re.split(r'(_[^_]+_)', text)
        for part in parts:
            if part.startswith('_') and part.endswith('_') and len(part) > 2:
                run = p.add_run(part[1:-1])
                run.font.name = 'Courier New'
                run.font.size = Pt(12)
                run.underline = True
                if is_bold:
                    run.bold = True
            elif use_links:
                _add_text_with_citations(p, part, nyscef_cfg, is_bold)
            else:
                _add_run(p, part, is_bold)
        return p

    # --- Build the document ---

    # Title block
    _add_paragraph(doc, config['doc_title'], is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_paragraph(doc, project.get('case_name', ''), alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_paragraph(doc, f"Docket No. {project.get('docket_number', '')}", alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_paragraph(doc, "")

    # Add drafted sections
    sections = project.get('drafted_sections', {})

    if 'full_brief' in sections:
        content = sections['full_brief'].get('content', '')
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            elif _is_heading(line):
                _add_paragraph(doc, stripped, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            elif _is_subheading(line):
                _add_paragraph(doc, line, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.LEFT, link_citations=True)
            else:
                _add_paragraph(doc, line, link_citations=True)
    else:
        if 'intro' in sections:
            _add_paragraph(doc, "PRELIMINARY STATEMENT", is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            for line in sections['intro'].get('content', '').split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                elif _is_heading(line):
                    _add_paragraph(doc, stripped, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
                elif _is_subheading(line):
                    _add_paragraph(doc, line, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.LEFT, link_citations=True)
                else:
                    _add_paragraph(doc, line, link_citations=True)

        arg_sections = [(k, v) for k, v in sections.items() if k.startswith('argument_')]
        arg_sections.sort(key=lambda x: int(x[0].split('_')[1]) if x[0].split('_')[1].isdigit() else 0)

        for i, (key, section) in enumerate(arg_sections, 1):
            _add_paragraph(doc, f"POINT {i}", is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            for line in section.get('content', '').split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                elif _is_heading(line):
                    _add_paragraph(doc, stripped, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
                elif _is_subheading(line):
                    _add_paragraph(doc, line, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.LEFT, link_citations=True)
                else:
                    _add_paragraph(doc, line, link_citations=True)

        if 'conclusion' in sections:
            _add_paragraph(doc, "CONCLUSION", is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            for line in sections['conclusion'].get('content', '').split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                elif _is_heading(line):
                    _add_paragraph(doc, stripped, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.CENTER)
                elif _is_subheading(line):
                    _add_paragraph(doc, line, is_bold=True, alignment=WD_ALIGN_PARAGRAPH.LEFT, link_citations=True)
                else:
                    _add_paragraph(doc, line, link_citations=True)

    # Dynamic signature block
    _add_paragraph(doc, "")
    _add_paragraph(doc, "Respectfully submitted,")
    _add_paragraph(doc, "")
    _add_paragraph(doc, "_______________________")
    _add_paragraph(doc, project.get('attorney_name', ''))
    _add_paragraph(doc, project.get('attorney_firm', ''))
    _add_paragraph(doc, config['signature_role'])

    # Save with dynamic filename
    output_filename = config['output_filename']
    output_path = PROJECTS_DIR / project_id / output_filename
    doc.save(output_path)

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


# ---------------------------------------------------------------------------
# Transcript Summarization (Two-Pass Processor)
# ---------------------------------------------------------------------------

# In-memory job tracker for background summarization
_summarize_jobs = {}  # job_id -> {status, stage, current, total, message, result, error}


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

        # Save summary into project
        project = get_project(project_id)
        if project:
            if 'summaries' not in project:
                project['summaries'] = {}
            project['summaries'][doc_type] = {
                'narrative': result['narrative'],
                'fact_count': result['fact_count'],
                'word_count': result['word_count'],
                'created_at': datetime.now().isoformat(),
                'model': model,
            }
            save_project(project_id, project)

        _summarize_jobs[job_id]['status'] = 'complete'
        _summarize_jobs[job_id]['result'] = {
            'narrative': result['narrative'],
            'fact_count': result['fact_count'],
            'word_count': result['word_count'],
        }

    except Exception as e:
        _summarize_jobs[job_id]['status'] = 'error'
        _summarize_jobs[job_id]['error'] = str(e)


@app.route('/project/<project_id>/summarize/<doc_type>', methods=['POST'])
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


@app.route('/project/<project_id>/summarize-status/<job_id>')
def summarize_status(project_id, job_id):
    """Poll summarization progress."""
    job = _summarize_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/project/<project_id>/summary/<doc_type>')
def get_summary(project_id, doc_type):
    """Get stored summary for a document."""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    summary = project.get('summaries', {}).get(doc_type)
    if not summary:
        return jsonify({'error': 'No summary found'}), 404

    return jsonify(summary)


_index_jobs = {}

@app.route('/project/<project_id>/index-record', methods=['POST'])
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
            # Save to project
            proj = get_project(pid)
            proj['record_index'] = index
            save_project(pid, proj)
            _index_jobs[jid]['status'] = 'complete'
            _index_jobs[jid]['result'] = {'fact_count': len(index)}
        except Exception as e:
            _index_jobs[jid]['status'] = 'error'
            _index_jobs[jid]['error'] = str(e)

    thread = threading.Thread(
        target=run_index,
        args=(job_id, project_id, docs, opening_text),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id, 'status': 'starting'})


@app.route('/project/<project_id>/index-record-status/<job_id>')
def index_record_status(project_id, job_id):
    """Poll record indexing progress."""
    job = _index_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


if __name__ == '__main__':
    print("\n" + "="*60)
    print("BRIEF DRAFTER")
    print("="*60)
    print(f"\nServer starting at: http://127.0.0.1:5003")
    print("\nUpload your documents, then let Claude draft your brief.")
    print("Press Ctrl+C to stop.\n")

    app.run(debug=True, host='127.0.0.1', port=5003)
