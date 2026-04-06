"""
BriefDrafter document gathering: collect, sanitize, preprocess source documents.
"""

import re
from collections import Counter


def _gather_additional_docs(docs):
    """Collect memo of law, reply affirmation, and other non-standard docs"""
    additional = []
    for key in ('memo_of_law', 'reply_affirmation'):
        text = docs.get(key, {}).get('text', '')
        if text:
            label = key.replace('_', ' ').upper()
            additional.append((label, text, 'secondary'))
    return additional


def _sanitize_respondent_brief(text):
    """Strip record citations and quoted testimony from respondent's brief
    so the AI cannot mine it for facts. The respondent's brief is ADVOCACY —
    it should only be used to identify what arguments to refute, not as a
    source of facts or testimony quotes."""
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

    constraints = []

    # --- 1. TERMINOLOGY ---
    plaintiff_count = len(re.findall(r'\bplaintiff\b', opening_text, re.IGNORECASE))
    appellant_count = len(re.findall(r'\bappellant\b', opening_text, re.IGNORECASE))
    # Don't double-count compound forms like "plaintiff-appellant"
    compound_count = len(re.findall(r'\bplaintiff[- ]appellant\b', opening_text, re.IGNORECASE))
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
    respondent_count = len(re.findall(r'\brespondent\b', opening_text, re.IGNORECASE))
    defendant_count = len(re.findall(r'\bdefendant\b', opening_text, re.IGNORECASE))
    compound_rd = len(re.findall(r'\bdefendant[- ]respondent\b', opening_text, re.IGNORECASE))
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
        point_match = re.match(r'^POINT\s+([IVX]+):?\s*$', stripped)
        if point_match:
            # Save previous point
            if current_point:
                points.append({'heading': current_point, 'subs': current_subs})
            # Collect the heading text from subsequent ALL-CAPS lines only
            heading_lines = [re.sub(r':$', '', stripped)]  # strip trailing colon
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
        sub_match = re.match(r'^(?:\t)?([A-C])\.\s+(.+)', stripped)
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
        count = len(re.findall(pattern, opening_text, re.IGNORECASE))
        if count == 0:
            absent_topics.append(topic)

    if absent_topics:
        constraints.append(
            'SCOPE NOTE: The following topics are NOT raised in the opening brief:\n  - '
            + '\n  - '.join(absent_topics)
            + '\nDo NOT create standalone Points for these topics. However, if respondents '
            'raise these as defenses, you MUST address and rebut them within the relevant Point.'
        )

    # --- 4. CASE LAW FROM OPENING BRIEF ---
    # Extract the most prominent cases (cited multiple times)
    # Two patterns: underscored (_Name v. Name_) and bare citation (Name v. Name, Vol Reporter)
    # Both restricted to reasonable lengths to avoid runaway matches
    case_mentions = []
    # Pattern 1: underscored case names (single line, max 80 chars per side)
    for m in re.finditer(r'_([^_\n]{1,80}?v\.?\s+[^_\n]{1,80}?)_', opening_text):
        case_mentions.append(m.group(1))
    # Pattern 2: bare case names followed by a legal reporter citation
    _entity_suffixes = {'LLC', 'Inc.', 'Inc', 'Corp.', 'Corp', 'Ltd.', 'Ltd', 'Co.', 'Co', 'L.P.'}
    for m in re.finditer(
        r'\b([A-Z][a-zA-Z\'\.\-]+(?:\s+[A-Z][a-zA-Z\'\.\-]+){0,5}\s+v\.?\s+'
        r'[A-Z][a-zA-Z\'\.\-,]+(?:\s+[A-Za-z\'\.\-,]+){0,5}?)'
        r',?\s+\d+\s+(?:A\.?D\.?\s*\d|N\.?Y\.?\s*\d|Misc|F\.\s*\d|S\.?\s*Ct)',
        opening_text
    ):
        name = m.group(1).strip().rstrip(',')
        # Skip matches where the first "party" is just an entity suffix (LLC v X)
        first_word = name.split()[0] if name else ''
        if len(name) < 120 and first_word not in _entity_suffixes:
            case_mentions.append(name)
    if case_mentions:
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


def _doc_entry(label, doc_dict, default_priority):
    """Detect imported summaries and promote to primary priority."""
    if doc_dict.get('source') == 'transcript_summarizer':
        return (f'TRANSCRIPT SUMMARY: {doc_dict.get("filename", label)}', doc_dict.get('text', ''), 'primary')
    return (label, doc_dict.get('text', ''), default_priority)


def _classify_source_doc(doc):
    """Classify a source document by its filename and content.
    Returns a prompt label that tells the AI exactly what this document is,
    so it never cites an affirmation as testimony or a defense paper as fact."""
    fname = doc.get('filename', '').lower()
    text = doc.get('text', '')[:2000].lower()
    original_fname = doc.get('filename', 'Unknown')

    # Transcript summaries from summarizer
    if doc.get('source') == 'transcript_summarizer':
        return f'TRANSCRIPT SUMMARY: {original_fname}'

    # Expert affirmations — detect by filename patterns
    if 'affirmation' in fname or 'affidavit' in fname:
        # Determine plaintiff vs defendant expert
        if any(w in fname for w in ['plaintiff', 'pl_', 'pltf']):
            return (f'PLAINTIFF\'S EXPERT AFFIRMATION: {original_fname} '
                    f'(THIS IS AN EXPERT OPINION — cite for expert analysis, '
                    f'NOT as testimony by a fact witness)')
        if any(w in fname for w in ['defendant', 'def_', 'deft', 'reply']):
            return (f'DEFENDANT\'S EXPERT AFFIRMATION: {original_fname} '
                    f'(THIS IS THE OPPOSING PARTY\'S EXPERT — '
                    f'do NOT state their claims as established facts. '
                    f'Use "defendant\'s expert claims" or "purportedly")')
        # Check full content for clues (not just first 2000 chars)
        full_text = doc.get('text', '').lower()
        if 'i disagree' in full_text or 'i respectfully disagree' in full_text or 'plaintiffs\' expert' in text:
            return (f'PLAINTIFF\'S EXPERT AFFIRMATION: {original_fname} '
                    f'(THIS IS AN EXPERT OPINION — cite for expert analysis, '
                    f'NOT as testimony by a fact witness)')
        if 'defendant respectfully' in text or 'plaintiff fails' in text or 'should be denied' in text:
            return (f'DEFENDANT\'S EXPERT AFFIRMATION: {original_fname} '
                    f'(THIS IS THE OPPOSING PARTY\'S EXPERT — '
                    f'do NOT state their claims as established facts)')
        return (f'EXPERT AFFIRMATION: {original_fname} '
                f'(EXPERT OPINION — cite for expert analysis, NOT as fact witness testimony)')

    # Reply affirmations / opposition papers
    if 'reply' in fname and ('aff' in fname or 'memo' in fname):
        return (f'DEFENDANT\'S REPLY PAPERS: {original_fname} '
                f'(OPPOSING PARTY\'S ADVOCACY — do NOT state as fact. '
                f'Use "defendant contends" or "purportedly")')

    # Lab results, radiology, medical records
    if any(w in fname for w in ['lab', 'test_result', 'ct_scan', 'radiology', 'mri', 'xray']):
        return f'MEDICAL RECORDS/LAB RESULTS: {original_fname} (objective medical evidence — cite as fact)'

    # Progress notes
    if 'progress_note' in fname or 'clinical_note' in fname:
        return f'MEDICAL RECORDS: {original_fname} (clinical records — cite as fact)'

    # Deposition transcripts
    if any(w in fname for w in ['deposition', 'depo', 'transcript', 'ebt']):
        return f'DEPOSITION TRANSCRIPT: {original_fname} (sworn testimony — cite as "[witness] testified")'

    # Default — generic with warning
    return (f'SOURCE DOCUMENT: {original_fname} '
            f'(verify document type before citing as "testimony" or "fact")')


def build_doc_items_for_brief_type(docs, record_combined, research_text, brief_type):
    """Build the (label, text, priority) list for _fit_documents based on brief type.
    Shared by draft_section and revise_brief to eliminate duplication."""
    from src.text_processing import _strip_opposing_brief_chrome

    tt_doc = docs.get('trial_transcript', {})

    if brief_type == 'appellant':
        doc_items = [
            ('LOWER COURT DECISION', docs.get('lower_court_decision', {}).get('text', ''), 'primary'),
            _doc_entry('TRIAL TRANSCRIPT', tt_doc, 'secondary'),
            ('APPELLANT\'S APPENDIX', docs.get('appellant_appendix', {}).get('text', ''), 'secondary'),
            ('RECORD ON APPEAL', record_combined, 'primary'),
            ('LEGAL RESEARCH', research_text, 'secondary'),
        ]
    elif brief_type == 'respondent':
        doc_items = [
            ('APPELLANT\'S OPENING BRIEF (OPPOSING PARTY — REBUT THIS)', _strip_opposing_brief_chrome(docs.get('appellant_brief', {}).get('text', '')), 'primary'),
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

    # Add source documents with accurate type labels
    # Source docs uploaded by the attorney are CRITICAL — never truncated
    for key, val in docs.items():
        if key.startswith('source_doc_'):
            if val.get('source') == 'transcript_summarizer':
                doc_items.append((f'TRANSCRIPT SUMMARY: {val.get("filename", key)}', val.get('text', ''), 'primary'))
            else:
                label = _classify_source_doc(val)
                doc_items.append((f"ATTORNEY'S SOURCE DOCUMENT: {label}", val.get('text', ''), 'critical'))
        elif key.startswith('transcript_digest_'):
            doc_items.append((f'TRANSCRIPT SUMMARY: {val.get("filename", key)}', val.get('text', ''), 'primary'))

    return doc_items


def _gather_record_volumes(docs):
    """Collect all record volume texts"""
    record_texts = []
    for key, doc in docs.items():
        if key.startswith('record_vol_') or key == 'record':
            vol_num = key.replace('record_vol_', '') if key.startswith('record_vol_') else '1'
            record_texts.append(f"--- RECORD VOL. {vol_num} ---\n{doc.get('text', '')}")
    return "\n\n".join(record_texts) if record_texts else ""


def _gather_legal_research(docs, case_law_issues=None):
    """Collect all legal research texts (legal_research, legal_research_2, etc.)
    Groups by issue name if case_law_issues mapping is provided."""
    case_law_issues = case_law_issues or {}

    # Collect research docs with their issue tags
    by_issue = {}  # issue_name -> list of (label, text)
    ungrouped = []  # (label, text) for docs without an issue

    for key, doc in docs.items():
        if key == 'legal_research' or key.startswith('legal_research_'):
            label = doc.get('filename', key.replace('_', ' ').title())
            text = doc.get('text', '')
            if not text:
                continue
            issue = case_law_issues.get(key, '')
            if issue:
                if issue not in by_issue:
                    by_issue[issue] = []
                by_issue[issue].append((label, text))
            else:
                ungrouped.append((label, text))

    parts = []

    # Grouped research first
    for issue_name, entries in by_issue.items():
        section = f"{'=' * 60}\nISSUE: {issue_name}\n{'=' * 60}"
        for label, text in entries:
            section += f"\n\n--- {label} ---\n{text}"
        parts.append(section)

    # Ungrouped research
    for label, text in ungrouped:
        parts.append(f"--- {label} ---\n{text}")

    return "\n\n".join(parts) if parts else ""
