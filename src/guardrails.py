"""
BriefDrafter guardrails: all validators + guardrail_brief orchestrator.
"""

import re


def count_brief_metrics(text):
    """Count words, record cites, case cites, quotes, and point headings."""
    return {
        'words': len(text.split()),
        'record_cites': len(re.findall(r'\(\d+\)', text)),
        'case_cites': len(re.findall(r'(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d|NE[23]d)', text)),
        'quotes': len(re.findall(r'"[^"]{20,}"', text)),
        'points': re.findall(r'POINT\s+[IVXLCDM\d]+', text),
    }


def validate_revision_integrity(pre_metrics, post_metrics):
    """Check that a revision didn't gut the brief content.
    Returns a list of violation strings (empty = passed).
    Thresholds: 70% word count, 50% record cites, 60% case cites,
    60% quotes, all point headings retained."""
    violations = []

    # Word count floor: revision must be at least 70% of original
    if pre_metrics['words'] > 0:
        word_ratio = post_metrics['words'] / pre_metrics['words']
        if word_ratio < 0.70:
            violations.append(f"Word count dropped {(1-word_ratio)*100:.0f}% ({pre_metrics['words']} -> {post_metrics['words']})")

    # Record citations floor: must retain at least 50%
    if pre_metrics['record_cites'] > 5:
        cite_ratio = post_metrics['record_cites'] / pre_metrics['record_cites']
        if cite_ratio < 0.50:
            violations.append(f"Record citations dropped {(1-cite_ratio)*100:.0f}% ({pre_metrics['record_cites']} -> {post_metrics['record_cites']})")

    # Case citations floor: must retain at least 60%
    if pre_metrics['case_cites'] > 3:
        case_ratio = post_metrics['case_cites'] / pre_metrics['case_cites']
        if case_ratio < 0.60:
            violations.append(f"Case citations dropped {(1-case_ratio)*100:.0f}% ({pre_metrics['case_cites']} -> {post_metrics['case_cites']})")

    # Quote preservation: must retain at least 60%
    if pre_metrics['quotes'] > 5:
        quote_ratio = post_metrics['quotes'] / pre_metrics['quotes']
        if quote_ratio < 0.60:
            violations.append(f"Quoted testimony dropped {(1-quote_ratio)*100:.0f}% ({pre_metrics['quotes']} -> {post_metrics['quotes']})")

    # Point headings: must retain all points
    pre_points = pre_metrics['points'] if isinstance(pre_metrics['points'], list) else []
    post_points = post_metrics['points'] if isinstance(post_metrics['points'], list) else []
    if len(pre_points) > len(post_points):
        violations.append(f"Point headings lost ({pre_points} -> {post_points})")

    # Refusal detection: reject if AI wrote meta-commentary instead of revising
    return violations


def validate_citations(memo_text: str, *source_texts) -> str:
    """Validate case citations against source materials.
    Two checks:
      1. Case NAME: plaintiff or defendant must appear in sources
      2. Reporter NUMBERS: the exact reporter string must appear in sources
    """
    combined_sources = '\n'.join(t for t in source_texts if t)
    combined_lower = combined_sources.lower()

    if not combined_lower.strip():
        print("[CITATION CHECK] No source materials to validate against, skipping", flush=True)
        return memo_text

    flagged_names = []
    flagged_reporters = []

    reporter_pattern = r'(\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d|NE[23]d)\s+\d+)'
    source_reporters = set()
    for m in re.finditer(reporter_pattern, combined_sources):
        source_reporters.add(re.sub(r'\s+', ' ', m.group(1).strip()))

    print(f"[CITATION CHECK] Found {len(source_reporters)} reporter citations in source documents", flush=True)

    def check_case(match):
        full_match = match.group(0)
        case_name = match.group(1).strip()

        name_verified = False
        reporter_verified = False

        v_match = re.search(r'^(.+?)\s+v\.?\s+', case_name)
        if not v_match:
            return full_match

        plaintiff = v_match.group(1).strip()
        skip = {'matter', 'of', 'in', 're', 'the', 'ex', 'rel', 'people', 'state'}
        significant_p = [w for w in plaintiff.split() if w.lower() not in skip]

        if significant_p:
            p_name = significant_p[0].lower().rstrip('.,;:')
            if len(p_name) >= 3 and re.search(r'\b' + re.escape(p_name) + r'\b', combined_lower):
                name_verified = True

        if not name_verified:
            d_match = re.search(r'v\.?\s+(.+)', case_name)
            if d_match:
                defendant = d_match.group(1).strip()
                significant_d = [w for w in defendant.split() if w.lower() not in skip]
                if significant_d:
                    d_name = significant_d[0].lower().rstrip('.,;:')
                    if len(d_name) >= 3 and re.search(r'\b' + re.escape(d_name) + r'\b', combined_lower):
                        name_verified = True

        after_pos = match.end()
        after_text = memo_text[after_pos:after_pos + 80]
        reporter_match = re.search(reporter_pattern, after_text)
        if reporter_match:
            draft_reporter = re.sub(r'\s+', ' ', reporter_match.group(1).strip())
            if draft_reporter in source_reporters:
                reporter_verified = True

        result = full_match
        if not name_verified:
            flagged_names.append(case_name)
            print(f"[CITATION CHECK] UNVERIFIED NAME: {case_name}", flush=True)
            result += ' [UNVERIFIED CITATION]'
        elif not reporter_verified and reporter_match:
            flagged_reporters.append(f"{case_name} -> {reporter_match.group(1)}")
            print(f"[CITATION CHECK] UNVERIFIED REPORTER: {case_name} cited as {reporter_match.group(1)}", flush=True)
            result += ' [CITE NUMBER UNVERIFIED]'

        return result

    result = re.sub(r'_([^_]+?v\.?\s+[^_]+?)_', check_case, memo_text)

    bare_case_pattern = r'(?<![_"])([A-Z][A-Za-z\'\.\s]+?\s+v\.?\s+[A-Z][A-Za-z\'\.\s]+?,\s+\d+\s+(?:AD3d|AD2d|NY3d|NY2d|NYS3d|NYS2d|Misc\s*3d|Misc\s*2d)\s+\d+)'
    result = re.sub(bare_case_pattern, check_case, result)

    total_flagged = len(flagged_names) + len(flagged_reporters)
    if total_flagged:
        print(f"[CITATION CHECK] Flagged {len(flagged_names)} unverified name(s), {len(flagged_reporters)} unverified reporter(s)", flush=True)
    else:
        print("[CITATION CHECK] All citations verified against source materials", flush=True)

    return result


def enforce_paragraph_cites(draft_text: str) -> str:
    """Check every FACTUAL paragraph's last sentence for a record citation.
    If missing, append [CITE NEEDED]."""
    record_cite_pattern = re.compile(r'\(\d[\d\-\u2013, ]*\)|\([Tt]r\.?\s+at\s+\d+[:\d\-\u2013, ]*\)')
    case_cite_pattern = re.compile(r'\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d)\s+\d+')
    paragraphs = draft_text.split('\n\n')
    fixed = []
    flagged_count = 0

    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            fixed.append(para)
            continue

        if stripped.isupper() or (len(stripped) < 120 and not stripped.endswith('.')):
            fixed.append(para)
            continue

        if len(stripped) < 200:
            fixed.append(para)
            continue

        if case_cite_pattern.search(stripped):
            fixed.append(para)
            continue

        if re.search(r'_[^_]+v\.?\s+[^_]+_', stripped):
            fixed.append(para)
            continue

        if re.search(r'(?i)\b(?:respondent|defendant|global|lomma)\s+(?:argues?|contends?|claims?|asserts?|relies)', stripped):
            fixed.append(para)
            continue

        sentences = re.split(r'(?<=[.!?])\s+', stripped)
        if not sentences:
            fixed.append(para)
            continue

        last_sentence = sentences[-1]
        if not record_cite_pattern.search(last_sentence):
            flagged_count += 1
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

    cite_lookup = {}
    for line in research_text.split('\n'):
        if not re.search(r'\bv\.?\s', line):
            continue
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
        v_match = re.match(r'(.+?)\s+v\.?\s+(.+)', case_name, re.IGNORECASE)
        if v_match:
            plaintiff = v_match.group(1).strip()
            defendant = v_match.group(2).strip()
            normalized_cite = full_cite.replace('A.D.3d', 'AD3d').replace('A.D.2d', 'AD2d')
            normalized_cite = normalized_cite.replace('N.Y.3d', 'NY3d').replace('N.Y.2d', 'NY2d')
            normalized_cite = normalized_cite.replace('N.Y.S.2d', 'NYS2d').replace('N.Y.S.3d', 'NYS3d')
            normalized_cite = re.sub(r'\((\d{4})\)', r'[\1]', normalized_cite)
            key = f"{plaintiff.lower()} v {defendant.lower()}".replace('.', '')
            cite_lookup[key] = (case_name, normalized_cite)

    if not cite_lookup:
        return draft_text

    print(f"[CASE CITE] Found {len(cite_lookup)} case citations in legal research", flush=True)

    def insert_cite(match):
        full_match = match.group(0)
        inner = match.group(1).strip()

        end_pos = match.end()
        after = draft_text[end_pos:end_pos + 30]
        if re.match(r',?\s*\d+\s+(?:AD|NY|Misc)', after):
            return full_match

        inner_lower = inner.lower().replace('.', '')
        for key, (orig_name, cite) in cite_lookup.items():
            v_match_draft = re.match(r'(.+?)\s+v\.?\s+(.+)', inner, re.IGNORECASE)
            v_match_key = re.match(r'(.+?)\s+v\s+(.+)', key)
            if v_match_draft and v_match_key:
                p_draft = v_match_draft.group(1).strip().lower().replace('.', '')
                d_draft = v_match_draft.group(2).strip().lower().replace('.', '').rstrip(',')
                p_key = v_match_key.group(1).strip()
                d_key = v_match_key.group(2).strip()
                p_words_draft = [w for w in p_draft.split() if len(w) > 2]
                p_words_key = [w for w in p_key.split() if len(w) > 2]
                if p_words_draft and p_words_key and p_words_draft[0] == p_words_key[0]:
                    print(f"[CASE CITE] Inserting full cite for {inner}", flush=True)
                    return f"_{inner}_, {cite}"

        return full_match

    result = re.sub(r'_([^_]+?v\.?\s+[^_]+?)_', insert_cite, draft_text)
    return result


def verify_attributions(draft_text: str, all_source_text: str) -> str:
    """Verify witness attributions in the draft against source documents."""
    if not draft_text or not all_source_text:
        return draft_text

    source_lower = all_source_text.lower()

    attribution_re = re.compile(
        r'(?:Dr\.\s+|Mr\.\s+|Ms\.\s+|Mrs\.\s+)?'
        r'([A-Z][a-z]{2,})'
        r'\s+'
        r'(?:testified|explained|stated|noted|indicated|acknowledged|admitted|confirmed|recalled|described|observed|opined|conceded|reported|related|recounted|clarified)'
        r'\s+(?:that\s+|at\s+(?:his|her|their)\s+)?'
        r'(.+?)'
        r'(?:\([^)]*\)|\.(?:\s|$))',
        re.DOTALL
    )

    skip_names = {
        'plaintiff', 'defendant', 'movant', 'respondent', 'petitioner',
        'appellant', 'court', 'justice', 'judge', 'honor', 'counsel',
    }

    all_matches = list(attribution_re.finditer(draft_text))
    attributed_names = set()
    for m in all_matches:
        name = m.group(1)
        if name.lower() not in skip_names:
            attributed_names.add(name)

    if len(attributed_names) < 2:
        print(f"[ATTRIBUTION CHECK] Only {len(attributed_names)} attributed name(s), skipping cross-check", flush=True)
        return draft_text

    print(f"[ATTRIBUTION CHECK] Found {len(attributed_names)} attributed witnesses: {sorted(attributed_names)}", flush=True)

    flagged = []
    result = draft_text

    for m in all_matches:
        name = m.group(1)
        content = m.group(2).strip()

        if name.lower() in skip_names:
            continue
        if len(content) < 20:
            continue

        content_normalized = re.sub(r'\s+', ' ', content.lower()).strip()

        words = content_normalized.split()
        search_phrase = content_normalized
        if len(words) > 8:
            trim = max(1, len(words) // 5)
            search_phrase = ' '.join(words[trim:-trim]) if trim < len(words) - trim else content_normalized

        if len(search_phrase) < 15:
            continue

        pos = source_lower.find(search_phrase)
        if pos == -1:
            continue

        window_start = max(0, pos - 5000)
        window_end = min(len(source_lower), pos + len(search_phrase) + 1000)
        window = source_lower[window_start:window_end]

        name_lower = name.lower()
        if name_lower in window:
            continue

        suggested = None
        for other_name in attributed_names:
            if other_name == name:
                continue
            if other_name.lower() in window:
                suggested = other_name
                break

        if suggested:
            flag_text = f" [VERIFY ATTRIBUTION: source may be {suggested}, not {name}]"
            flagged.append((name, suggested, content[:60]))
            print(f"[ATTRIBUTION CHECK] MISMATCH: draft credits {name}, source window has {suggested}", flush=True)
        else:
            flag_text = f" [VERIFY ATTRIBUTION: {name} not found near this content in sources]"
            flagged.append((name, None, content[:60]))
            print(f"[ATTRIBUTION CHECK] WARNING: {name} not found near attributed content", flush=True)

        full_match = m.group(0)
        flagged_version = full_match + flag_text
        result = result.replace(full_match, flagged_version, 1)

    if flagged:
        print(f"[ATTRIBUTION CHECK] Flagged {len(flagged)} potential misattribution(s)", flush=True)
    else:
        print("[ATTRIBUTION CHECK] All attributions verified against source documents", flush=True)

    return result


def enforce_style_conformance(text: str) -> str:
    """Strip AI-isms and enforce attorney voice patterns. Code-level, Claude can't ignore."""
    result = text
    replacements = 0

    # Em dashes -> commas (dead giveaway of AI)
    if '\u2014' in result:
        count = result.count('\u2014')
        result = re.sub(r'\s*\u2014\s*', ', ', result)
        replacements += count

    # AI filler phrases -> remove or replace
    ai_phrases = [
        (r'\bIt is important to note that ', 'Indeed, '),
        (r'\bIt bears noting that ', 'Indeed, '),
        (r'\bSignificantly, ', 'Indeed, '),
        (r'\bNotably, ', 'Indeed, '),
        (r'\bFirst and foremost, ', 'First, '),
        (r'\bIn conclusion, ', 'Based upon the foregoing, '),
        (r'\bTo summarize, ', 'Based upon the foregoing, '),
        (r'\bIn summary, ', 'Based upon the foregoing, '),
        (r'\bIt should be noted that ', 'Indeed, '),
        (r'\bIt is worth noting that ', 'Indeed, '),
        (r'\bIt is noteworthy that ', 'Indeed, '),
        (r'\bThis is because ', 'Indeed, '),
        (r'\bAs such, ', 'Under these circumstances, '),
    ]
    for pattern, replacement in ai_phrases:
        new_result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        if new_result != result:
            replacements += 1
            result = new_result

    # Fix period placement before citations
    result = re.sub(r'\.\s+\(([Ss]ee,?\s)', r' (\1', result)
    result = re.sub(r'\.\s+\((\d[\d\-\u2013, ]*)\)', r' (\1).', result)

    if replacements:
        print(f"[STYLE CONFORMANCE] Fixed {replacements} AI-ism(s)", flush=True)

    return result


def consolidate_transcript_cites(draft_text: str) -> str:
    """Merge adjacent/overlapping line ranges within transcript citations."""
    if not draft_text:
        return draft_text

    cite_re = re.compile(
        r'\(Tr\.(\s+\d{1,2}/\d{1,2}/\d{2,4})?\s+at\s+'
        r'([\d:,\s\u2013\-]+)'
        r'\)'
    )

    ref_re = re.compile(r'(\d+):(\d+)(?:\s*[-\u2013]\s*(\d+))?')

    count = 0

    def _consolidate(match):
        nonlocal count
        date_part = match.group(1) or ''
        inner = match.group(2).strip()

        refs = []
        for rm in ref_re.finditer(inner):
            page = int(rm.group(1))
            start = int(rm.group(2))
            end = int(rm.group(3)) if rm.group(3) else start
            refs.append((page, start, end))

        if len(refs) < 2:
            return match.group(0)

        refs.sort(key=lambda r: (r[0], r[1]))

        merged = [refs[0]]
        for page, start, end in refs[1:]:
            prev_page, prev_start, prev_end = merged[-1]
            if page == prev_page and start <= prev_end + 2:
                merged[-1] = (prev_page, prev_start, max(prev_end, end))
            else:
                merged.append((page, start, end))

        if len(merged) == len(refs):
            return match.group(0)

        count += 1

        parts = []
        for page, start, end in merged:
            if start == end:
                parts.append(f'{page}:{start}')
            else:
                parts.append(f'{page}:{start}-{end}')

        return f'(Tr.{date_part} at {", ".join(parts)})'

    result = cite_re.sub(_consolidate, draft_text)

    if count:
        print(f"[CITE CONSOLIDATE] Merged line ranges in {count} transcript citation(s)", flush=True)

    return result


def consolidate_bare_page_cites(draft_text: str) -> str:
    """Merge adjacent bare page citations for appellate records."""
    if not draft_text:
        return draft_text

    cite_re = re.compile(r'\((\d{1,4}(?:\s*,\s*\d{1,4})+)\)')

    count = 0

    def _consolidate_pages(match):
        nonlocal count
        inner = match.group(1)
        pages = [int(p.strip()) for p in inner.split(',')]

        if len(pages) < 2:
            return match.group(0)

        pages.sort()

        ranges = [(pages[0], pages[0])]
        for p in pages[1:]:
            prev_start, prev_end = ranges[-1]
            if p == prev_end + 1:
                ranges[-1] = (prev_start, p)
            else:
                ranges.append((p, p))

        if len(ranges) == len(pages):
            return match.group(0)

        count += 1

        parts = []
        for start, end in ranges:
            if start == end:
                parts.append(str(start))
            else:
                parts.append(f'{start}-{end}')

        return f'({", ".join(parts)})'

    result = cite_re.sub(_consolidate_pages, draft_text)

    if count:
        print(f"[CITE CONSOLIDATE] Merged {count} bare page citation range(s)", flush=True)

    return result


def _replace_party_surname(text, project):
    """Replace the represented party's surname with their party label."""
    representing = project.get('representing', '')
    if not representing:
        return text

    if representing == 'respondent':
        party_name = project.get('respondent', '').strip().rstrip(',')
        label = 'plaintiff'
    else:
        party_name = project.get('appellant', '').strip().rstrip(',')
        label = 'plaintiff'

    if not party_name:
        return text

    parts = party_name.split()
    surname = parts[-1].strip().title() if parts else ''
    if not surname or len(surname) < 3:
        return text

    if surname not in text:
        return text

    first_idx = text.find(surname)
    if first_idx < 0:
        return text

    first_end = first_idx + len(surname)

    before = text[:first_end]
    after = text[first_end:]
    after = after.replace(surname, label)

    text = before + after

    text = re.sub(r'(\.\s+)plaintiff\b', r'\1Plaintiff', text)
    text = re.sub(r'(\.\s*\n\s*)plaintiff\b', r'\1Plaintiff', text)
    text = re.sub(r'(\t)plaintiff\b', r'\1Plaintiff', text)

    return text


def verify_opposition_characterizations(draft_text: str, opposition_text: str,
                                         party_label: str = 'respondent') -> str:
    """Verify that 'respondent argues X' characterizations actually appear in
    the opposing party's papers. Flags fabricated characterizations where quoted
    phrases or a majority of specific terms (numbers, percentages, specialized
    terminology) are not found in the opposing party's actual papers.

    Pure code — no API calls."""
    if not draft_text or not opposition_text:
        return draft_text

    # Normalize opposition text for searching
    opp_normalized = re.sub(r'\s+', ' ', opposition_text.lower()).strip()

    # Build party variants for regex
    party_variants = [party_label.lower()]
    label_lower = party_label.lower()
    if label_lower == 'respondent':
        party_variants += ['respondent', "respondent's", 'respondents', "respondents'"]
    elif label_lower == 'defendant':
        party_variants += ['defendant', "defendant's", 'defendants', "defendants'"]
    elif label_lower == 'plaintiff':
        party_variants += ['plaintiff', "plaintiff's", 'plaintiffs', "plaintiffs'"]
    elif label_lower == 'appellant':
        party_variants += ['appellant', "appellant's", 'appellants', "appellants'"]
    party_pattern = '|'.join(re.escape(v) for v in set(party_variants))

    # Attribution patterns
    attribution_patterns = [
        rf'(?:(?:{party_pattern})\s+(?:argue|contend|claim|assert|suggest|maintain|insist|state|submit|posit|urge|allege)s?\s+that\s+)',
        rf'(?:according\s+to\s+(?:{party_pattern})\s*,\s*)',
        rf"(?:(?:{party_pattern})(?:'s|s')?\s+(?:argument|contention|position|claim|assertion|theory)\s+(?:that|is\s+that)\s+)",
        rf"(?:in\s+(?:{party_pattern})(?:'s|s')?\s+(?:view|estimation|submission)\s*,\s*)",
    ]
    combined_pattern = '|'.join(attribution_patterns)

    # Common legal terms to skip
    skip_terms = {
        'plaintiff', 'defendant', 'respondent', 'movant', 'petitioner', 'appellant',
        'court', 'motion', 'summary', 'judgment', 'negligence', 'negligent',
        'liability', 'damages', 'cause', 'action', 'evidence', 'testimony',
        'trial', 'deposition', 'witness', 'injury', 'injuries', 'accident',
        'the', 'that', 'this', 'which', 'their', 'there', 'where', 'when',
        'should', 'would', 'could', 'must', 'shall', 'have', 'has', 'had',
        'been', 'were', 'was', 'are', 'not', 'any', 'all', 'also', 'such',
        'upon', 'with', 'from', 'into', 'under', 'over', 'only', 'made',
        'failed', 'established', 'sufficient', 'prima', 'facie',
    }

    # Case citation pattern
    case_cite_re = re.compile(r'\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d|NE[23]d|US|F[23]d)\s+\d+')

    # Split into sentences — protect abbreviations from splitting
    _protected = draft_text
    for abbr in ['Dr.', 'Mr.', 'Ms.', 'Mrs.', 'Jr.', 'Sr.', 'St.', 'Inc.', 'Ltd.', 'No.', 'Dept.', 'Ct.', 'Supp.', 'vs.', 'v.']:
        _protected = _protected.replace(abbr, abbr.replace('.', '\x00'))
    sentence_re = re.compile(r'(?<=[.!?])\s+(?=[A-Z\t])')
    _raw_sentences = sentence_re.split(_protected)
    sentences = [s.replace('\x00', '.') for s in _raw_sentences]

    flagged_count = 0
    result = draft_text

    for sentence in sentences:
        sentence_stripped = sentence.strip()
        if not sentence_stripped:
            continue

        attr_match = re.search(combined_pattern, sentence_stripped, re.IGNORECASE)
        if not attr_match:
            continue

        claim = sentence_stripped[attr_match.end():].strip()
        if len(claim) < 10:
            continue

        # --- Extract verifiable specifics ---
        verifiable_quotes = []
        verifiable_numbers = []
        verifiable_terms = []

        # 1. Quoted phrases
        quote_re = re.compile(r'["\u201c]([^"\u201d]{4,}?)["\u201d]')
        for qm in quote_re.finditer(claim):
            verifiable_quotes.append(qm.group(1).strip())

        # 2. Numbers and percentages
        claim_no_cites = case_cite_re.sub('', claim)
        pct_re = re.compile(r'(\d+)\s*(?:percent|%)')
        for pm in pct_re.finditer(claim_no_cites):
            verifiable_numbers.append(pm.group(0).strip())
        dollar_re = re.compile(r'\$[\d,]+(?:\.\d{2})?')
        for dm in dollar_re.finditer(claim_no_cites):
            verifiable_numbers.append(dm.group(0).strip())
        num_context_re = re.compile(r'(\d{2,})\s+([a-z]+)')
        for nm in num_context_re.finditer(claim_no_cites):
            context_word = nm.group(2).lower()
            if context_word not in skip_terms and context_word not in {'at', 'to', 'of', 'in', 'on', 'or', 'and', 'is', 'be'}:
                verifiable_numbers.append(nm.group(0).strip())

        # 3. Specialized terms
        spec_re = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Za-z]+){1,2})\b')
        for sm in spec_re.finditer(claim):
            term = sm.group(1).strip()
            words = term.lower().split()
            if all(w in skip_terms for w in words):
                continue
            if any(v.lower() in term.lower() for v in party_variants):
                continue
            verifiable_terms.append(term)

        # --- Check specifics against opposition papers ---
        quotes_missing = []
        for quote in verifiable_quotes:
            quote_norm = re.sub(r'\s+', ' ', quote.lower()).strip()
            if quote_norm not in opp_normalized:
                words = quote_norm.split()
                found = False
                if len(words) >= 4:
                    trim = max(1, len(words) // 5)
                    core = ' '.join(words[trim:-trim]) if trim < len(words) - trim else quote_norm
                    if len(core) >= 10 and core in opp_normalized:
                        found = True
                if not found:
                    quotes_missing.append(quote)

        numbers_found = 0
        numbers_total = len(verifiable_numbers)
        for num_phrase in verifiable_numbers:
            num_norm = re.sub(r'\s+', ' ', num_phrase.lower()).strip()
            just_num = re.search(r'\d+', num_phrase)
            if num_norm in opp_normalized:
                numbers_found += 1
            elif just_num and just_num.group() in opp_normalized:
                numbers_found += 1

        # --- Decide whether to flag ---
        should_flag = False

        if quotes_missing:
            should_flag = True
            print(f"[OPP CHECK] Quote not in opposition papers: \"{quotes_missing[0][:60]}...\"", flush=True)

        total_specifics = numbers_total + len(verifiable_terms)
        if not should_flag and total_specifics >= 2:
            specifics_found = numbers_found
            for term in verifiable_terms:
                term_norm = re.sub(r'\s+', ' ', term.lower()).strip()
                if term_norm in opp_normalized:
                    specifics_found += 1
            if specifics_found < total_specifics * 0.5:
                should_flag = True
                print(f"[OPP CHECK] Only {specifics_found}/{total_specifics} specifics found in opposition papers", flush=True)

        if should_flag:
            flagged_count += 1
            flag_tag = ' [ARGUMENT NOT FOUND IN OPPOSITION PAPERS]'
            anchor = sentence_stripped[:80]
            if anchor in result and flag_tag not in result[result.index(anchor):result.index(anchor)+len(sentence_stripped)+100]:
                pos = result.index(anchor)
                sent_end = result.find('.', pos + len(sentence_stripped) - 20)
                if sent_end == -1:
                    sent_end = pos + len(sentence_stripped)
                else:
                    sent_end += 1
                result = result[:sent_end] + flag_tag + result[sent_end:]

    if flagged_count:
        print(f"[OPP CHECK] Flagged {flagged_count} unverified opposition characterization(s)", flush=True)
    else:
        print("[OPP CHECK] All opposition characterizations verified", flush=True)

    return result


def guardrail_brief(draft_text: str, brief_type: str, research_text: str = '', opening_brief_text: str = '', all_source_text: str = '', respondent_text: str = '', project: dict = None) -> str:
    """Post-processing guardrails for drafted briefs. Validates and fixes output programmatically.
    This is code, not a prompt — Claude can't ignore it."""

    result = draft_text

    # 0. Replace party surname with party label
    if project:
        result = _replace_party_surname(result, project)

    # 1. Strip any markdown that slipped through
    result = re.sub(r'^#{1,4}\s+', '', result, flags=re.MULTILINE)
    result = re.sub(r'\*\*([^*]+)\*\*', r'\1', result)
    result = re.sub(r'(?<![_])\*([^*]+)\*(?![_])', r'\1', result)

    # 2. Fix case name formatting: bold to underscore
    result = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', result)

    # 3. Fix wrong section headings based on brief type
    if brief_type == 'appellant':
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
        result = re.sub(r'(?i)requesting affirmance', 'requesting reversal', result)
        result = re.sub(r'(?i)should be affirmed', 'should be reversed', result)

    elif brief_type == 'respondent':
        result = re.sub(
            r'(?im)^[\t ]*STATEMENT\s+OF\s+THE\s+CASE\s*$',
            'COUNTERSTATEMENT OF FACTS',
            result
        )
        result = re.sub(r'(?i)requesting reversal', 'requesting affirmance', result)

    elif brief_type == 'reply':
        pass

    # 4. Fix citation format: periods in reporters
    result = re.sub(r'A\.D\.3d', 'AD3d', result)
    result = re.sub(r'A\.D\.2d', 'AD2d', result)
    result = re.sub(r'N\.Y\.3d', 'NY3d', result)
    result = re.sub(r'N\.Y\.2d', 'NY2d', result)
    result = re.sub(r'N\.Y\.S\.2d', 'NYS2d', result)
    result = re.sub(r'N\.Y\.S\.3d', 'NYS3d', result)

    # 5. Fix bracket format
    result = re.sub(r'(\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d)\s+\d+)\s*\((\d{1,2}(?:st|d|th)\s+Dept\s+\d{4})\)',
                    r'\1 [\2]', result)

    # 5.5. Wrap bare case citations in parentheses
    reporters = r'(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d|NE[23]d)'
    bare_cite = rf'(?<!\()(_[^_]+_,?\s+\d+\s+{reporters}\s+\d+\s*\[[^\]]+\])'
    result = re.sub(bare_cite, r'(\1)', result)
    bare_cite_plain = rf'([A-Z][A-Za-z\s\'.]+\s+v\.?\s+[A-Za-z\s\'.]+,\s+\d+\s+{reporters}\s+\d+\s*\[[^\]]+\])'
    def wrap_if_not_in_parens(match):
        start = match.start()
        preceding = result[:start]
        open_count = preceding.count('(') - preceding.count(')')
        if open_count > 0:
            return match.group(0)
        return f'({match.group(1)})'
    result = re.sub(bare_cite_plain, wrap_if_not_in_parens, result)

    # 5.6. Consolidate adjacent transcript line ranges
    result = consolidate_transcript_cites(result)
    result = consolidate_bare_page_cites(result)

    # 6. Enforce paragraph cites (only for appellant/respondent briefs, not reply)
    if brief_type != 'reply':
        result = enforce_paragraph_cites(result)

    # 7. Enforce case cites from research
    if research_text:
        result = enforce_case_cites(result, research_text)

    # 8. Enforce terminology from opening brief
    if opening_brief_text and brief_type == 'reply':
        plaintiff_ct = len(re.findall(r'\bplaintiff\b', opening_brief_text, re.IGNORECASE))
        appellant_ct = len(re.findall(r'\bappellant\b', opening_brief_text, re.IGNORECASE))
        compound_ct = len(re.findall(r'\bplaintiff[- ]appellant\b', opening_brief_text, re.IGNORECASE))
        p_only = plaintiff_ct - compound_ct
        a_only = appellant_ct - compound_ct

        if p_only > a_only * 3:
            replaced = 0
            def _fix_appellant(m):
                nonlocal replaced
                word = m.group(0)
                start = m.start()
                before = result[:start]
                underscore_count = before.count('_')
                if underscore_count % 2 == 1:
                    return word
                prev_chars = result[max(0, start-11):start]
                if 'plaintiff-' in prev_chars.lower() or 'plaintiff ' in prev_chars.lower():
                    return word
                next_chars = result[m.end():m.end()+12].lower()
                if next_chars.startswith('-appellant') or next_chars.startswith(' appellant'):
                    return word
                replaced += 1
                if word[0].isupper():
                    return 'Plaintiff' if word == word.capitalize() else 'PLAINTIFF'
                return 'plaintiff'
            result = re.sub(r'\b[Aa]ppellant\b(?![\-])', _fix_appellant, result)
            result = re.sub(r'\bAPPELLANTS\b', 'PLAINTIFFS', result)
            result = re.sub(r'\bAppellants\b', 'Plaintiffs', result)
            result = re.sub(r'\bappellants\b', 'plaintiffs', result)
            if replaced:
                print(f"[TERMINOLOGY] Replaced {replaced} 'appellant' \u2192 'plaintiff' to match opening brief", flush=True)

    # 9. Verify witness attributions
    if all_source_text:
        result = verify_attributions(result, all_source_text)

    # 9b. Verify opposition characterizations (reply briefs only)
    if brief_type == 'reply' and respondent_text:
        result = verify_opposition_characterizations(result, respondent_text, party_label='respondent')

    # 10. Style conformance
    result = enforce_style_conformance(result)

    return result
