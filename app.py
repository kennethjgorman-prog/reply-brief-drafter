#!/usr/bin/env python3
"""
Reply Brief Drafter
Drafts appellate reply briefs based on uploaded documents
"""

import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from anthropic import Anthropic
import pdfplumber
from docx import Document as DocxDocument

load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

# Configuration
BASE_DIR = Path(__file__).parent
PROJECTS_DIR = BASE_DIR / 'projects'
PROJECTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
                        text_parts.append(f"--- PAGE {i} ---\n{page_text}")
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
    """Load project data"""
    project_file = PROJECTS_DIR / project_id / 'project.json'
    if project_file.exists():
        with open(project_file, 'r') as f:
            return json.load(f)
    return None


def save_project(project_id: str, data: dict):
    """Save project data"""
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(exist_ok=True)
    with open(project_dir / 'project.json', 'w') as f:
        json.dump(data, f, indent=2)


def call_claude(prompt: str, max_tokens: int = 4000) -> str:
    """Call Claude API"""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY not set in .env file"

    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"ERROR: {str(e)}"


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
                    projects.append({
                        'id': p.name,
                        'case_name': proj.get('case_name', 'Untitled'),
                        'created': proj.get('created', ''),
                        'status': proj.get('status', 'draft')
                    })

    projects.sort(key=lambda x: x.get('created', ''), reverse=True)
    return render_template('index.html', projects=projects)


@app.route('/project/new', methods=['POST'])
def create_project():
    """Create new reply brief project"""
    data = request.json or {}

    project_id = str(uuid.uuid4())[:8]
    project_data = {
        'id': project_id,
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
    return render_template('workspace.html', project=project)


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

    save_project(project_id, project)

    return jsonify({
        'success': True,
        'doc_type': doc_type,
        'filename': filename,
        'char_count': len(text)
    })


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


@app.route('/project/<project_id>/analyze', methods=['POST'])
def analyze_arguments(project_id):
    """Analyze both briefs to identify arguments requiring response"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check required documents
    docs = project.get('documents', {})
    if 'respondent_brief' not in docs:
        return jsonify({'error': 'Respondent\'s brief not uploaded'}), 400
    if 'opening_brief' not in docs:
        return jsonify({'error': 'Opening brief not uploaded'}), 400

    respondent_text = docs['respondent_brief'].get('text', '')
    opening_text = docs.get('opening_brief', {}).get('text', '')

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

    result = call_claude(prompt, max_tokens=6000)

    # Parse JSON response
    try:
        # Find JSON in response
        start = result.find('{')
        end = result.rfind('}') + 1
        if start >= 0 and end > start:
            analysis = json.loads(result[start:end])
        else:
            analysis = {'arguments': [], 'error': 'Could not parse response'}
    except json.JSONDecodeError:
        analysis = {'arguments': [], 'raw_response': result}

    project['analysis'] = analysis
    project['status'] = 'analyzed'
    save_project(project_id, project)

    return jsonify(analysis)


@app.route('/project/<project_id>/draft', methods=['POST'])
def draft_section(project_id):
    """Draft a section of the reply brief"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.json or {}
    section_type = data.get('section_type', 'argument')  # intro, argument, conclusion
    argument_number = data.get('argument_number', 1)

    docs = project.get('documents', {})
    analysis = project.get('analysis', {})

    # Build context from documents
    opening_text = docs.get('opening_brief', {}).get('text', '')
    respondent_text = docs.get('respondent_brief', {}).get('text', '')
    appellant_appendix_text = docs.get('appellant_appendix', {}).get('text', '')

    # Gather all record volumes
    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_'):
            vol_num = key.replace('record_vol_', '')
            record_texts.append(f"--- RECORD VOL. {vol_num} ---\n{doc.get('text', '')}")
    record_combined = "\n\n".join(record_texts) if record_texts else ""

    # Get optional documents
    respondent_appendix_text = docs.get('respondent_appendix', {}).get('text', '')
    research_text = docs.get('legal_research', {}).get('text', '')

    # Get specific argument if drafting argument section
    argument_info = ""
    if section_type == 'argument' and analysis.get('arguments'):
        args = analysis['arguments']
        if 0 < argument_number <= len(args):
            arg = args[argument_number - 1]
            argument_info = f"""
ARGUMENT TO ADDRESS:
Title: {arg.get('title', '')}
Your Original Argument (from opening brief): {arg.get('appellant_argument', arg.get('summary', ''))}
Respondent's Counter-Argument: {arg.get('respondent_counter', '')}
Cases Cited by Respondent: {', '.join(arg.get('cases_cited', []))}
Weaknesses to Exploit in Reply: {arg.get('weaknesses', '')}
"""

    prompt = f"""You are an expert appellate attorney drafting a reply brief.

CASE INFORMATION:
Case: {project.get('case_name', '')}
Court: {project.get('court', '')}
Docket: {project.get('docket_number', '')}
Appellant: {project.get('appellant', '')}
Respondent: {project.get('respondent', '')}

{argument_info}

DOCUMENTS PROVIDED:

--- RECORD ON APPEAL ---
{record_combined if record_combined else "(No record uploaded)"}

--- APPELLANT'S APPENDIX ---
{appellant_appendix_text if appellant_appendix_text else "(No appellant appendix uploaded)"}

{f"--- RESPONDENT'S APPENDIX ---{chr(10)}{respondent_appendix_text}" if respondent_appendix_text else ""}

--- LEGAL RESEARCH ---
{research_text if research_text else "(No legal research uploaded)"}

--- APPELLANT'S OPENING BRIEF ---
{opening_text if opening_text else "(No opening brief uploaded)"}

--- RESPONDENT'S BRIEF ---
{respondent_text if respondent_text else "(No respondent brief uploaded)"}

DRAFTING PROTOCOL - CRITICAL REQUIREMENTS:

1. SOURCE-BOUND DRAFTING: Every factual assertion MUST cite to the source document.
   - Record/Appendix citation format: ([page]). - Period goes AFTER the closing parenthesis
   - NO prefixes like "R." or "A." - just the page number in parentheses
   - Example: "The plaintiff fell on the stairs" (125).
   - Example: "The court dismissed the case" (91).
   - NO facts may be stated without a citation to Record, Appendix, or RA
   - If you cannot find support in any source document, write "[CITE NEEDED]"

2. LEGAL CITATIONS - STRICT GUARDRAILS:
   - You may ONLY cite cases and legal authorities that appear in the uploaded documents
   - Extract cases from: opening brief, respondent's brief, legal research (if provided)
   - DO NOT use any case from your training data or general knowledge
   - DO NOT cite any case unless you can find it verbatim in the uploaded documents
   - DO NOT paraphrase holdings - use only what the briefs state about the case
   - If a case would strengthen the argument but is NOT in the documents, write "[CASE CITE NEEDED: description of case needed]"
   - NEVER invent a citation - if you cannot find it in the documents, flag it

3. ANTI-HALLUCINATION - ABSOLUTE REQUIREMENTS:
   - Do NOT invent facts, quotes, or case holdings
   - Do NOT assume facts not in the record
   - Do NOT create or fabricate case citations
   - Do NOT use outside legal knowledge - ONLY what is in the uploaded documents
   - Do NOT supplement with cases from your training data
   - If you need authority not in the documents, flag with [CASE CITE NEEDED]
   - If unsure about any fact, flag with [VERIFY]
   - When in doubt, flag it rather than guess

{"TASK: Draft the INTRODUCTION section. Briefly state why the reply brief is necessary and preview the key responses." if section_type == 'intro' else ""}
{"TASK: Draft POINT " + str(argument_number) + " responding to respondent's argument. Use proper appellate brief formatting with point headings." if section_type == 'argument' else ""}
{"TASK: Draft the CONCLUSION section requesting specific relief." if section_type == 'conclusion' else ""}

REMINDER: Use ONLY cases and legal authorities found in the uploaded documents. NO outside research. If you need a case not in the documents, write [CASE CITE NEEDED]. Never fabricate citations.

Draft the section now:"""

    result = call_claude(prompt, max_tokens=4000)

    # Save drafted section
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


@app.route('/project/<project_id>/draft-all', methods=['POST'])
def draft_entire_brief(project_id):
    """Draft the entire reply brief using multi-pass approach"""
    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    docs = project.get('documents', {})

    # Get FULL document text - no truncation for research extraction
    opening_text = docs.get('opening_brief', {}).get('text', '')
    respondent_text = docs.get('respondent_brief', {}).get('text', '')
    appellant_appendix_text = docs.get('appellant_appendix', {}).get('text', '')
    respondent_appendix_text = docs.get('respondent_appendix', {}).get('text', '')

    # Gather all record volumes
    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            record_texts.append(doc.get('text', ''))
    record_combined = "\n\n".join(record_texts) if record_texts else ""

    # ============ PASS 1: Extract cases from RESPONDENT'S brief ============
    pass1_prompt = f"""You are a legal research assistant. Extract EVERY case citation from this respondent's brief.

RESPONDENT'S BRIEF:
{respondent_text}

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

    respondent_cases = call_claude(pass1_prompt, max_tokens=8000)

    # ============ PASS 2: Extract cases from APPELLANT'S brief ============
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

    appellant_cases = call_claude(pass2_prompt, max_tokens=8000)

    # ============ PASS 3: Extract KEY RECORD MOMENTS ============
    # Handle large documents
    record_source = appellant_appendix_text if appellant_appendix_text else record_combined
    if len(record_source) > 400000:
        record_source = record_source[:400000]  # First ~100K tokens

    pass3_prompt = f"""You are a legal research assistant. Extract KEY TESTIMONY and EVIDENCE from this appellate record/appendix.

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

    record_evidence = call_claude(pass3_prompt, max_tokens=8000)

    # ============ PASS 4: Extract KEY TRANSCRIPT QUOTES ============
    # This pass specifically extracts "killer quotes" - the exact words that win arguments
    # Handle large documents by focusing on transcript pages

    # Try to find transcript sections (look for hearing/transcript markers)
    source_text = appellant_appendix_text if appellant_appendix_text else record_combined

    # If document is very large, extract just transcript pages (pages with "THE COURT:", "MR.", "MS.", etc.)
    if len(source_text) > 400000:  # ~100K tokens
        # Extract pages that look like transcripts
        transcript_pages = []
        pages = source_text.split('--- PAGE ')
        for page in pages:
            # Look for transcript indicators
            if any(marker in page for marker in ['THE COURT:', 'MR. ', 'MS. ', 'Q.', 'A.', 'BY MR.', 'BY MS.']):
                transcript_pages.append('--- PAGE ' + page if not page.startswith('---') else page)

        if transcript_pages:
            source_text = '\n\n'.join(transcript_pages[:100])  # Take up to 100 transcript pages
        else:
            # Fall back to reasonable size
            source_text = source_text[:400000]

    pass4_prompt = f"""You are a legal research assistant extracting KEY TRANSCRIPT QUOTES from appellate record/appendix.

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

EXAMPLES OF WHAT TO FIND:
- "you are accordingly relieved by this court and the case is dismissed" (91).
- "we wish not to take this case anymore" (26).
- "I told her we could only take the case if the Court was willing to stay it" (77).

Extract EVERY significant quote. Use EXACT WORDS - do not paraphrase. Include the page number in parentheses with period after: (91).

This is critical - these quotes will be used verbatim in the reply brief."""

    transcript_quotes = call_claude(pass4_prompt, max_tokens=8000)

    # ============ PASS 5: Draft the brief using extracted research ============
    pass5_prompt = f"""You are an expert appellate attorney drafting a REPLY BRIEF.

You have been provided with:
1. All cases from respondent's brief with their exact claims
2. All cases from appellant's brief
3. Key record evidence with page citations

YOUR JOB: Draft a reply brief that:
- QUOTES cases directly (use the extracts provided)
- QUOTES the record directly (use page cites like (45).)
- Distinguishes respondent's cases with SPECIFIC factual/legal distinctions
- Points to SPECIFIC record evidence respondent ignores

=== CASES FROM RESPONDENT'S BRIEF ===
{respondent_cases}

=== CASES FROM APPELLANT'S BRIEF ===
{appellant_cases}

=== KEY RECORD EVIDENCE ===
{record_evidence}

=== KEY TRANSCRIPT QUOTES (USE THESE VERBATIM) ===
{transcript_quotes}

=== RESPONDENT'S BRIEF (for direct quotes) ===
{respondent_text}

=== DRAFTING REQUIREMENTS ===

1. QUOTE CASES DIRECTLY:
   - Use NEW YORK OFFICIAL CITATION FORMAT: 123 AD3d 456 [2d Dept 2020]
   - Case names must use UNDERSCORES for underlining: _Case Name v. Other Party_
   - DO NOT use **asterisks** for case names - use _underscores_ only
   - Example: As this Court held in _Fan v Sabin_, "further proceedings" (125 AD3d at 499-500).

2. RECORD CITATIONS - CRITICAL FORMAT:
   - NEVER use "R." prefix - that is WRONG
   - NEVER use "A." prefix - that is WRONG
   - CORRECT format: (page number). with period AFTER parenthesis
   - WRONG: (R. 45). WRONG: (A. 123). WRONG: (R. 529-530).
   - CORRECT: (45). CORRECT: (123). CORRECT: (529-530).
   - Example: The court stated: "you are accordingly relieved" (91).

3. DISTINGUISH RESPONDENT'S CASES:
   - Quote what respondent claims the case holds
   - Explain specifically why it doesn't apply here
   - Point to factual distinctions with record cites

4. STRUCTURE:
   - PRELIMINARY STATEMENT
   - POINT I, II, III, etc. (one for EACH major argument - be thorough)
   - CONCLUSION

5. LENGTH AND DEPTH:
   - This must be a COMPREHENSIVE reply brief, not a summary
   - Each POINT should be 2-4 pages of detailed argument
   - Address EVERY significant argument respondent makes
   - Include MULTIPLE case citations per point
   - Use EXTENSIVE record citations throughout
   - The brief should be 15-25 pages when formatted

CRITICAL - USE THE TRANSCRIPT QUOTES:
The KEY TRANSCRIPT QUOTES section above contains verbatim quotes from the record. USE THEM.
- Copy quotes exactly as provided
- These quotes are your most powerful evidence - deploy them strategically

CRITICAL - CITATION FORMAT REMINDERS:
- Record cites: (page). NOT (R. page). NOT (A. page). Just the number.
- Case names: _underscored_ NOT **bolded**
- Period goes AFTER the closing parenthesis: (91). NOT (91.)

Draft an EXHAUSTIVE reply brief. Do not summarize - argue thoroughly with full citations. Every claim must be supported. Every respondent argument must be addressed and refuted."""

    final_brief = call_claude(pass5_prompt, max_tokens=16000)

    # Save all passes for reference
    project['drafted_sections']['respondent_cases'] = respondent_cases
    project['drafted_sections']['appellant_cases'] = appellant_cases
    project['drafted_sections']['record_evidence'] = record_evidence
    project['drafted_sections']['transcript_quotes'] = transcript_quotes
    project['drafted_sections']['full_brief'] = {
        'content': final_brief,
        'drafted_at': datetime.now().isoformat()
    }
    save_project(project_id, project)

    return jsonify({
        'full_brief': final_brief,
        'research': {
            'respondent_cases': respondent_cases,
            'appellant_cases': appellant_cases,
            'record_evidence': record_evidence,
            'transcript_quotes': transcript_quotes
        }
    })


@app.route('/project/<project_id>/generate', methods=['POST'])
def generate_brief(project_id):
    """Generate complete reply brief as Word document"""
    from docx.shared import Pt
    from docx.enum.text import WD_LINE_SPACING
    import re

    project = get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Create Word document
    doc = DocxDocument()

    # Set default style to Courier New, 12pt, double-spaced
    style = doc.styles['Normal']
    style.font.name = 'Courier New'
    style.font.size = Pt(12)
    style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE

    # Title
    doc.add_heading(f"REPLY BRIEF FOR APPELLANT", 0)
    doc.add_paragraph(f"{project.get('case_name', '')}")
    doc.add_paragraph(f"Docket No. {project.get('docket_number', '')}")
    doc.add_paragraph("")

    # Helper function to add paragraph with underlined case names
    def add_formatted_paragraph(doc, text):
        """Add paragraph, converting _text_ to underlined text and cleaning markdown"""
        p = doc.add_paragraph()
        # Remove ** bold markers (AI sometimes uses these)
        text = re.sub(r'\*\*([^*]+)\*\*', r'_\1_', text)  # Convert **bold** to _underline_
        text = text.replace('**', '')  # Remove any stray **
        # Also fix any (R. X) citations to just (X)
        text = re.sub(r'\(R\.\s*(\d+[^)]*)\)', r'(\1)', text)
        text = re.sub(r'\(A\.\s*(\d+[^)]*)\)', r'(\1)', text)
        # Split on underscores to find case names
        parts = re.split(r'(_[^_]+_)', text)
        for part in parts:
            if part.startswith('_') and part.endswith('_') and len(part) > 2:
                # This is a case name - underline it
                run = p.add_run(part[1:-1])  # Remove the underscores
                run.underline = True
            else:
                p.add_run(part)
        return p

    # Add drafted sections
    sections = project.get('drafted_sections', {})

    # If full brief was drafted, use that
    if 'full_brief' in sections:
        content = sections['full_brief'].get('content', '')
        for para in content.split('\n'):
            if para.strip():
                add_formatted_paragraph(doc, para)
    else:
        # Otherwise combine individual sections
        if 'intro' in sections:
            doc.add_heading("PRELIMINARY STATEMENT", level=1)
            for para in sections['intro'].get('content', '').split('\n'):
                if para.strip():
                    add_formatted_paragraph(doc, para)

        # Add argument sections
        arg_sections = [(k, v) for k, v in sections.items() if k.startswith('argument_')]
        arg_sections.sort(key=lambda x: int(x[0].split('_')[1]) if x[0].split('_')[1].isdigit() else 0)

        for i, (key, section) in enumerate(arg_sections, 1):
            doc.add_heading(f"POINT {i}", level=1)
            for para in section.get('content', '').split('\n'):
                if para.strip():
                    add_formatted_paragraph(doc, para)

        if 'conclusion' in sections:
            doc.add_heading("CONCLUSION", level=1)
            for para in sections['conclusion'].get('content', '').split('\n'):
                if para.strip():
                    add_formatted_paragraph(doc, para)

    # Signature block
    doc.add_paragraph("")
    doc.add_paragraph("Respectfully submitted,")
    doc.add_paragraph("")
    doc.add_paragraph(f"_______________________")
    doc.add_paragraph(f"{project.get('attorney_name', '')}")
    doc.add_paragraph(f"{project.get('attorney_firm', '')}")
    doc.add_paragraph("Attorney for Appellant")

    # Save document
    output_path = PROJECTS_DIR / project_id / 'Reply_Brief.docx'
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
    """Download generated reply brief"""
    project = get_project(project_id)
    if not project:
        return "Project not found", 404

    output_path = PROJECTS_DIR / project_id / 'Reply_Brief.docx'
    if not output_path.exists():
        return "Brief not generated yet", 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"Reply_Brief_{project.get('case_name', 'draft').replace(' ', '_')}.docx",
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


if __name__ == '__main__':
    print("\n" + "="*60)
    print("REPLY BRIEF DRAFTER")
    print("="*60)
    print(f"\nServer starting at: http://127.0.0.1:5003")
    print("\nUpload your briefs and record, then let Claude draft your reply.")
    print("Press Ctrl+C to stop.\n")

    app.run(debug=True, host='127.0.0.1', port=5003)
