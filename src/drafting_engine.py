"""
BriefDrafter drafting engine: all 6 _draft_*_brief functions.
"""

import re

from src.config import MAX_PRIMARY_CHARS, MAX_SECONDARY_CHARS, MAX_TOTAL_CHARS
from src.text_processing import _strip_opposing_brief_chrome, _truncate, _fit_documents, _extract_search_terms, _search_record_pages
from src.claude_client import call_claude, call_claude_with_docs
from src.guardrails import validate_citations, enforce_paragraph_cites, enforce_case_cites, guardrail_brief
from src.prompt_builders import (
    _build_drafting_protocol, _build_anti_hallucination_block,
    _build_writing_style, _build_exemplars, _build_structure_prompt,
    _build_party_label_constraint,
)
from src.document_gathering import (
    _gather_additional_docs, _gather_respondent_briefs, _preprocess_opening_brief,
    _gather_record_volumes, _gather_legal_research,
)
from src.record_indexing import _format_record_index_for_prompt, _extract_record_evidence, _extract_transcript_quotes
from src.routes.witness import _build_witness_constraint_for_project
from src.utils.qc_reporter import BriefQC, generate_qc_report
from src.utils.citation_validator import (
    extract_all_citations, validate_page_ranges, flag_violations,
    get_record_page_ranges, generate_validation_report,
)
from src.utils.transcript_parser import verify_attribution_framing


def _draft_appellant_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for appellant's brief — skips extraction passes"""
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    transcript_text = _truncate(docs.get('trial_transcript', {}).get('text', ''), MAX_SECONDARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)
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

{_build_anti_hallucination_block()}

{_build_drafting_protocol()}

{_build_writing_style()}

{_build_exemplars('appellant')}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    all_source_text = '\n\n'.join(doc['text'] for doc in docs.values() if isinstance(doc, dict) and doc.get('text'))
    final_brief = guardrail_brief(final_brief, 'appellant', research_text, all_source_text=all_source_text, project=project)

    # Run QC report
    qc = BriefQC()
    qc_results = qc.run_qc(final_brief)
    qc_report = generate_qc_report(qc_results)
    print(f"[QC] {qc_report}", flush=True)

    return final_brief, {'drafting_mode': 'structured', 'qc_report': qc_report}


def _draft_respondent_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for respondent's brief — skips extraction passes"""
    appellant_text = _truncate(_strip_opposing_brief_chrome(docs.get('appellant_brief', {}).get('text', '')), MAX_PRIMARY_CHARS)
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)
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

    # Gather co-respondent briefs (friendly party — source of arguments and cases)
    co_respondent_briefs = _gather_respondent_briefs(docs, sanitize=False)
    co_respondent_text = '\n\n'.join(text for _, text, _ in co_respondent_briefs)
    co_respondent_text = _truncate(co_respondent_text, MAX_SECONDARY_CHARS) if co_respondent_text else ''

    # Fit supplementary documents
    doc_items = [
        ('APPELLANT\'S OPENING BRIEF (ADVOCACY — NOT EVIDENCE)', appellant_text, 'primary'),
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('CO-RESPONDENT\'S BRIEF (FRIENDLY PARTY — USE THEIR ARGUMENTS AND CASES)', co_respondent_text, 'secondary'),
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

{_build_anti_hallucination_block()}

{_build_drafting_protocol()}

{_build_writing_style()}

{_build_exemplars('respondent')}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    all_source_text = '\n\n'.join(doc['text'] for doc in docs.values() if isinstance(doc, dict) and doc.get('text'))
    final_brief = guardrail_brief(final_brief, 'respondent', research_text, all_source_text=all_source_text, project=project)

    # Run QC report
    qc = BriefQC()
    qc_results = qc.run_qc(final_brief)
    qc_report = generate_qc_report(qc_results)
    print(f"[QC] {qc_report}", flush=True)

    return final_brief, {'drafting_mode': 'structured', 'qc_report': qc_report}


def _draft_reply_brief_structured(project, docs, structure, drafting_instructions='', model='sonnet'):
    """Structured drafting for reply brief — skips extraction passes"""
    opening_text = _truncate(docs.get('opening_brief', {}).get('text', ''), MAX_PRIMARY_CHARS)
    respondent_briefs = _gather_respondent_briefs(docs)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)
    record_combined = _gather_record_volumes(docs)
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)

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

    witness_constraint = _build_witness_constraint_for_project(project)

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

{witness_constraint}

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

{_build_anti_hallucination_block()}

{_build_drafting_protocol()}

{_build_writing_style()}

{_build_exemplars('reply')}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(prompt, max_tokens=16000, model=model)
    all_source_text = '\n\n'.join(doc['text'] for doc in docs.values() if isinstance(doc, dict) and doc.get('text'))
    respondent_text = '\n\n'.join(text for _, text, _ in _gather_respondent_briefs(docs, sanitize=False))
    final_brief = guardrail_brief(final_brief, 'reply', research_text, opening_brief_text=full_opening_text, all_source_text=all_source_text, respondent_text=respondent_text, project=project)

    # Verify witness attribution framing
    if project.get('witness_map'):
        final_brief = verify_attribution_framing(final_brief, {'entries': project['witness_map']})

    # Run QC report
    qc = BriefQC()
    qc_results = qc.run_qc(final_brief)
    qc_report = generate_qc_report(qc_results)
    print(f"[QC] {qc_report}", flush=True)

    # Run citation validation
    record_ranges = get_record_page_ranges(docs)
    if record_ranges:
        cites = extract_all_citations(final_brief)
        validation = validate_page_ranges(cites, record_ranges)
        final_brief = flag_violations(final_brief, validation)
        cite_report = generate_validation_report(validation)
        print(f"[CITE VALIDATION] {cite_report}", flush=True)
    else:
        cite_report = ''

    return final_brief, {'drafting_mode': 'structured', 'qc_report': qc_report, 'citation_report': cite_report}


def _draft_appellant_brief(project, docs, drafting_instructions='', model='sonnet'):
    """4-pass drafting for appellant's brief"""
    structure = project.get('brief_structure')
    if structure and structure.get('points'):
        return _draft_appellant_brief_structured(project, docs, structure, drafting_instructions, model)

    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    transcript_text = _truncate(docs.get('trial_transcript', {}).get('text', ''), MAX_SECONDARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)
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

    court_reasoning = call_claude(pass2_prompt, max_tokens=8000)

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

    case_law = call_claude(pass3_prompt, max_tokens=8000)

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

{_build_anti_hallucination_block()}

{_build_drafting_protocol()}

{_build_writing_style()}

{_build_exemplars('appellant')}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(pass4_prompt, max_tokens=16000, model=model)

    # Convert any bold case names to underscore format
    final_brief = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', final_brief)

    # Citation validation — checks case names AND reporter numbers against sources
    final_brief = validate_citations(
        final_brief,
        decision_text,
        existing_draft,
        research_text,
        case_law,
    )

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

    appellant_text = _truncate(_strip_opposing_brief_chrome(docs.get('appellant_brief', {}).get('text', '')), MAX_PRIMARY_CHARS)
    decision_text = _truncate(docs.get('lower_court_decision', {}).get('text', ''), MAX_PRIMARY_CHARS)
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)
    existing_draft = _truncate(docs.get('existing_draft', {}).get('text', ''), MAX_PRIMARY_CHARS)

    # Gather co-respondent briefs (friendly party — source of arguments and cases)
    co_respondent_briefs = _gather_respondent_briefs(docs, sanitize=False)
    co_respondent_text = '\n\n'.join(text for _, text, _ in co_respondent_briefs)
    co_respondent_text = _truncate(co_respondent_text, MAX_SECONDARY_CHARS) if co_respondent_text else ''

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

    appellant_cases = call_claude(pass1_prompt, max_tokens=8000)

    # Pass 2: Extract record evidence supporting affirmance
    record_evidence = _extract_record_evidence(docs)

    # Pass 3: Extract respondent's case law
    sources_for_cases = decision_text
    if co_respondent_text:
        sources_for_cases += f"\n\nCO-RESPONDENT'S BRIEF (FRIENDLY PARTY — USE THEIR ARGUMENTS AND CASES):\n{co_respondent_text}"
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

    respondent_cases = call_claude(pass3_prompt, max_tokens=8000)

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

    # Build co-respondent block if available
    co_respondent_block = ''
    if co_respondent_text:
        co_respondent_block = f"""=== CO-RESPONDENT'S BRIEF (FRIENDLY PARTY — USE THEIR ARGUMENTS AND CASES) ===
This brief was filed by a co-respondent on the SAME SIDE as you. Their arguments SUPPORT your position.
- Draw on their legal arguments, case citations, and factual analysis
- Do NOT duplicate their brief — complement it with additional arguments or different emphasis
- You may cite the same cases but develop different points
- Reference their arguments where useful: "As the [co-respondent] correctly notes..."
{co_respondent_text}
=== END CO-RESPONDENT'S BRIEF ==="""

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

{co_respondent_block}

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

{_build_anti_hallucination_block()}

{_build_drafting_protocol()}

{_build_writing_style()}

{_build_exemplars('respondent')}

{atty_instructions}

{drafting_task} OUTPUT PLAIN TEXT ONLY — NO MARKDOWN:"""

    final_brief = call_claude(pass4_prompt, max_tokens=16000, model=model)

    # Convert any bold case names to underscore format
    final_brief = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', final_brief)

    # Citation validation — checks case names AND reporter numbers against sources
    final_brief = validate_citations(
        final_brief,
        appellant_text,
        existing_draft,
        decision_text,
        research_text,
        appellant_cases,
        respondent_cases,
    )

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

    respondent_cases = call_claude(pass1_prompt, max_tokens=8000)

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

    appellant_cases = call_claude(pass2_prompt, max_tokens=8000)

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

    # Build witness constraint if witness map exists
    witness_constraint = _build_witness_constraint_for_project(project)

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

{witness_constraint}

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

{record_index_block}

SOURCE DOCUMENTS (provided as structured document blocks for citation tracking):
- "Key Record Evidence" — use these record excerpts with RECORD page numbers
- "Key Transcript Quotes" — use these VERBATIM, copy quotes exactly
- "Appellant's Opening Brief" — defines the issues, record page numbers, and terminology
- "Respondent's Brief" — this is ADVOCACY, NOT EVIDENCE:
  WARNING: Record citations and quoted testimony have been REMOVED to prevent copying.
  Do NOT quote this brief and cite record page numbers as if you verified the record.
  Do NOT adopt respondent's characterizations or conclusions as fact.
  When referencing what respondent argues, ATTRIBUTE IT: "Respondent argues..." or "Respondent contends..."
  ONLY cite facts from the record evidence and transcript quotes documents.

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

4. STRUCTURE:
   - PRELIMINARY STATEMENT
   - POINT I, II, III, etc. (one for EACH major argument - be thorough)
   - You MUST address EVERY argument defendants raise, even if it falls outside the opening brief's Points
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

    # Build source document blocks for citation tracking
    pass5_docs = []
    if record_evidence:
        pass5_docs.append({"text": record_evidence, "title": "Key Record Evidence"})
    if transcript_quotes:
        pass5_docs.append({"text": transcript_quotes, "title": "Key Transcript Quotes"})
    if opening_text:
        pass5_docs.append({"text": opening_text, "title": "Appellant's Opening Brief"})
    if respondent_text_sanitized:
        pass5_docs.append({"text": respondent_text_sanitized, "title": "Respondent's Brief"})

    if pass5_docs:
        final_brief, pass5_citations = call_claude_with_docs(pass5_prompt, pass5_docs, max_tokens=16000, model=model)
        print(f"[CITATIONS] Pass 5 returned {len(pass5_citations)} source citations", flush=True)
    else:
        final_brief = call_claude(pass5_prompt, max_tokens=16000, model=model)

    # Run guardrail: strip markdown, fix citations, enforce terminology
    research_text = _truncate(_gather_legal_research(docs, project.get('case_law_issues', {})), MAX_SECONDARY_CHARS)
    all_source_text = '\n\n'.join(doc['text'] for doc in docs.values() if isinstance(doc, dict) and doc.get('text'))
    respondent_text = respondent_text_raw  # unsanitized for characterization verification
    final_brief = guardrail_brief(final_brief, 'reply', research_text, opening_brief_text=full_opening_text, all_source_text=all_source_text, respondent_text=respondent_text, project=project)

    # Run QC report
    qc = BriefQC()
    qc_results = qc.run_qc(final_brief)
    qc_report = generate_qc_report(qc_results)
    print(f"[QC] {qc_report}", flush=True)

    # Run citation validation against record page ranges
    record_ranges = get_record_page_ranges(docs)
    if record_ranges:
        cites = extract_all_citations(final_brief)
        validation = validate_page_ranges(cites, record_ranges)
        final_brief = flag_violations(final_brief, validation)
        cite_report = generate_validation_report(validation)
        print(f"[CITE VALIDATION] {cite_report}", flush=True)
    else:
        cite_report = ''

    # Verify witness attribution framing
    if project.get('witness_map'):
        final_brief = verify_attribution_framing(final_brief, {'entries': project['witness_map']})

    return final_brief, {
        'respondent_cases': respondent_cases,
        'appellant_cases': appellant_cases,
        'record_evidence': record_evidence,
        'transcript_quotes': transcript_quotes,
        'qc_report': qc_report,
        'citation_report': cite_report,
    }
