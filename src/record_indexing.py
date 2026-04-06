"""
BriefDrafter record indexing: record index, evidence/quote extraction.
"""

import os
import re
import json

from anthropic import Anthropic
from src.config import MAX_PRIMARY_CHARS, MAX_SECONDARY_CHARS
from src.claude_client import call_claude


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
    page_splits = re.split(r'--- PAGE (\d+) ---', full_record)
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
        points = re.findall(r'(POINT\s+[IVX]+[^\n]*(?:\n[A-Z][^\n]+)*)', opening_brief_text)
        if points:
            focus = "LEGAL ISSUES ON APPEAL:\n" + "\n".join(p.strip() for p in points)

    # Chunk pages (20 per chunk for Claude's context window)
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

    claude_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback('extraction', i + 1, total_chunks, f'Indexing pages {chunk["range"]}...')

        user_prompt = f"""{focus}

RECORD CHUNK (Pages {chunk['range']}):
{chunk['text']}"""

        try:
            print(f"[INDEX] Record chunk {i+1}/{total_chunks}, pages {chunk['range']}", flush=True)
            response = claude_client.messages.create(
                model='claude-sonnet-4-20250514',
                max_tokens=8000,
                system=EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            result_text = response.content[0].text.strip()
            if result_text.startswith('```'):
                result_text = re.sub(r'^```\w*\n?', '', result_text)
                result_text = re.sub(r'\n?```$', '', result_text)
            try:
                facts = json.loads(result_text)
                all_facts.extend(facts)
            except json.JSONDecodeError:
                match = re.search(r'\[.*\]', result_text, re.DOTALL)
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

    # Tag affirmation/affidavit entries with source_party using page content
    _tag_source_parties(unique, pages)

    if progress_callback:
        progress_callback('complete', total_chunks, total_chunks, f'Done: {len(unique)} facts indexed')

    return unique


def _tag_source_parties(facts, pages):
    """Tag affirmation/affidavit entries with source_party: 'plaintiff' or 'defendant'.

    Uses page header text to detect which side filed the document.
    Only tags doc_type == 'affirmation'. Everything else (testimony, exhibit,
    decision) is neutral and gets no party tag.

    Detection strategy: scan the first page of each affirmation document for
    party indicators in the header/title text. The record typically has headers
    like:
      "Affirmation of Sheldon E. Green, for Plaintiff, in Opposition"
      "Affirmation of Jonathan R. Janofsky, for Doyban, in Support of Motion"
      "Exhibit A to Green Affirmation" (plaintiff's expert)
      "Exhibit A to Borbet Affirmation" (defendant's expert)
      "Reply Affirmation" (always defendant in opposition context)
    """
    # Build page_num -> content lookup
    page_content = {pg: txt for pg, txt in pages}

    # Defendant indicators checked FIRST (higher priority — "reply affirmation"
    # and "in support of motion" are unambiguous defendant markers)
    defendant_patterns = [
        r'reply\s+affirmation',
        r'in support of\s+(?:the\s+)?(?:instant\s+)?motion',
        r'in support of\s+(?:the\s+)?(?:motion|summary)',
        r'in further support of\s+(?:the\s+)?motion',
        r'for\s+(?:doyban|konig|katz|northwell|defendant)',
        r'exhibit\s+\w+\s+to\s+(?:janofsky|borbet)\s+affirmation',
        r'on behalf of\s+(?:the\s+)?(?:moving\s+)?defendant',
    ]
    # Plaintiff indicators (case-insensitive)
    plaintiff_patterns = [
        r'for plaintiff',
        r'in opposition',
        r'plaintiff.s?.+(?:affirmation|affidavit)',
        r'exhibit\s+\w+\s+to\s+green\s+affirmation',
    ]

    # Build a cache: for each page, determine party from header context
    # We scan the page itself and a few pages before it (to catch exhibit pages
    # that don't repeat the header)
    party_cache = {}

    def _detect_party_from_text(text):
        text_lower = text[:600].lower()
        # Check defendant first — "reply affirmation" and "in support of motion"
        # are unambiguous and must take priority over mixed-content pages
        for pat in defendant_patterns:
            if re.search(pat, text_lower):
                return 'defendant'
        for pat in plaintiff_patterns:
            if re.search(pat, text_lower):
                return 'plaintiff'
        return None

    for fact in facts:
        if fact.get('doc_type') != 'affirmation':
            continue

        pg = fact.get('record_page')
        if pg is None:
            continue

        # Check cache first
        if pg in party_cache:
            fact['source_party'] = party_cache[pg]
            continue

        # Try the page itself
        content = page_content.get(pg, '')
        party = _detect_party_from_text(content)

        # If not found, scan up to 5 pages back for the document header
        if not party:
            for lookback in range(1, 6):
                prev_content = page_content.get(pg - lookback, '')
                if prev_content:
                    party = _detect_party_from_text(prev_content)
                    if party:
                        break

        if party:
            party_cache[pg] = party
            fact['source_party'] = party
        # If not found from page text, leave for propagation pass below

    # Propagation pass: for untagged affirmation pages, inherit the party from
    # the nearest tagged page before it (same document runs contiguously)
    aff_pages_sorted = sorted(set(
        f.get('record_page') for f in facts
        if f.get('doc_type') == 'affirmation' and f.get('record_page') is not None
    ))

    # Build ordered list of tagged pages
    last_party = None
    for pg in aff_pages_sorted:
        if pg in party_cache:
            last_party = party_cache[pg]
        elif last_party:
            # Check that this page is close to previous affirmation pages
            # (within 20 pages — documents don't span more than that typically)
            prev_tagged = max((p for p in party_cache if p < pg), default=None)
            if prev_tagged and (pg - prev_tagged) <= 20:
                party_cache[pg] = last_party

    # Apply propagated tags
    for fact in facts:
        if fact.get('doc_type') != 'affirmation':
            continue
        pg = fact.get('record_page')
        if pg and not fact.get('source_party') and pg in party_cache:
            fact['source_party'] = party_cache[pg]

    # Log summary
    tagged_p = sum(1 for f in facts if f.get('source_party') == 'plaintiff')
    tagged_d = sum(1 for f in facts if f.get('source_party') == 'defendant')
    untagged = sum(1 for f in facts if f.get('doc_type') == 'affirmation' and not f.get('source_party'))
    print(f"[INDEX] Party tagging: {tagged_p} plaintiff, {tagged_d} defendant, {untagged} untagged affirmation entries", flush=True)


def _format_record_index_for_prompt(record_index, representing='appellant'):
    """Format the record index as text blocks for the drafting prompt.

    Splits affirmation/affidavit entries by party:
    - Our side's affirmations + all neutral evidence (testimony, exhibits) → main block
    - Opposing side's affirmations → separate rebuttal-only block

    Args:
        record_index: list of fact dicts from _build_record_index
        representing: 'appellant' or 'respondent' — determines which side is "ours"
    """
    if not record_index:
        return ''

    # Determine our party label
    if representing in ('appellant', 'plaintiff'):
        our_party = 'plaintiff'
        their_party = 'defendant'
    else:
        our_party = 'defendant'
        their_party = 'plaintiff'

    main_lines = []
    rebuttal_lines = []

    for fact in record_index:
        pg = fact.get('record_page', '?')
        doc_type = fact.get('doc_type', '')
        witness = fact.get('witness', '')
        fact_text = fact.get('fact', '')
        quote = fact.get('quote', '')
        source_party = fact.get('source_party', '')

        if doc_type == 'pleading':
            continue

        line = f"(PAGE {pg})"
        if witness:
            line += f" [{witness}]"
        if doc_type:
            line += f" [{doc_type}]"
        line += f" {fact_text}"
        if quote:
            line += f' — "{quote}"'

        # Route: opposing affirmations go to rebuttal block
        if doc_type == 'affirmation' and source_party == their_party:
            rebuttal_lines.append(line)
        else:
            main_lines.append(line)

    result = "=== RECORD INDEX (USE THESE PAGE NUMBERS FOR CITATIONS) ===\n"
    result += "\n".join(main_lines)

    if rebuttal_lines:
        result += "\n\n=== OPPOSING PARTY'S AFFIRMATIONS (FOR REBUTTAL ONLY) ===\n"
        result += "USE THESE ONLY when describing what the opposing party argues, NOT as evidence for your client's narrative.\n"
        result += "Always attribute: \"Defendants' expert argues...\" or \"Defendants contend...\"\n"
        result += "\n".join(rebuttal_lines)

    return result


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
    if len(record_source) > MAX_PRIMARY_CHARS:
        record_source = record_source[:MAX_PRIMARY_CHARS]

    prompt = f"""You are a legal research assistant. Extract KEY TESTIMONY and EVIDENCE from this appellate record/appendix.

Focus on:
- Direct quotes from testimony
- Key admissions or statements
- Documents referenced
- Timeline events

RECORD/APPENDIX:
{record_source}

{f"RESPONDENT'S APPENDIX:{chr(10)}{respondent_appendix_text[:MAX_SECONDARY_CHARS] if len(respondent_appendix_text) > MAX_SECONDARY_CHARS else respondent_appendix_text}" if respondent_appendix_text else ""}

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

    if len(source_text) > MAX_PRIMARY_CHARS:
        transcript_pages = []
        pages = source_text.split('--- PAGE ')
        for page in pages:
            if any(marker in page for marker in ['THE COURT:', 'MR. ', 'MS. ', 'Q.', 'A.', 'BY MR.', 'BY MS.']):
                transcript_pages.append('--- PAGE ' + page if not page.startswith('---') else page)
        if transcript_pages:
            source_text = '\n\n'.join(transcript_pages[:400])
        else:
            source_text = source_text[:MAX_PRIMARY_CHARS]

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

{f"RESPONDENT'S APPENDIX:{chr(10)}{respondent_appendix_text[:MAX_SECONDARY_CHARS] if len(respondent_appendix_text) > MAX_SECONDARY_CHARS else respondent_appendix_text}" if respondent_appendix_text else ""}

FORMAT - USE EXACT QUOTES WITH PAGE NUMBERS:

**QUOTE ([page])**: "[EXACT words spoken - copy verbatim]"
**SPEAKER**: [Judge/Attorney name if known]
**CONTEXT**: [Brief description of what was happening]
**WHY IT MATTERS**: [How this quote helps or hurts the case]
---

Extract EVERY significant quote. Use EXACT WORDS - do not paraphrase. Include the page number in parentheses with period after: (91).

This is critical - these quotes will be used verbatim in the brief."""

    return call_claude(prompt, max_tokens=8000)
