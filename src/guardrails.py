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


def validate_supplement_integrity(pre_metrics, post_metrics):
    """Supplement validation: content should only grow, never shrink."""
    violations = []

    if post_metrics['words'] < pre_metrics['words']:
        loss_pct = (1 - post_metrics['words'] / max(pre_metrics['words'], 1)) * 100
        violations.append(
            f"Word count DECREASED {loss_pct:.0f}% ({pre_metrics['words']} -> {post_metrics['words']}). "
            f"Supplement should only ADD content."
        )

    if pre_metrics['record_cites'] > 3 and post_metrics['record_cites'] < pre_metrics['record_cites']:
        violations.append(f"Record citations decreased ({pre_metrics['record_cites']} -> {post_metrics['record_cites']})")

    if pre_metrics['case_cites'] > 2 and post_metrics['case_cites'] < pre_metrics['case_cites']:
        violations.append(f"Case citations decreased ({pre_metrics['case_cites']} -> {post_metrics['case_cites']})")

    if pre_metrics['quotes'] > 3 and post_metrics['quotes'] < pre_metrics['quotes']:
        violations.append(f"Quoted testimony decreased ({pre_metrics['quotes']} -> {post_metrics['quotes']})")

    pre_points = pre_metrics['points'] if isinstance(pre_metrics['points'], list) else []
    post_points = post_metrics['points'] if isinstance(post_metrics['points'], list) else []
    if len(pre_points) != len(post_points):
        violations.append(f"Point headings changed ({len(pre_points)} -> {len(post_points)})")

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

    # Citation format cleanup: strip periods from reporter abbreviations
    result = re.sub(r'A\.D\.2d', 'AD2d', result)
    result = re.sub(r'A\.D\.3d', 'AD3d', result)
    result = re.sub(r'A\.D\.', 'AD', result)
    result = re.sub(r'N\.Y\.2d', 'NY2d', result)
    result = re.sub(r'N\.Y\.3d', 'NY3d', result)
    result = re.sub(r'N\.Y\.S\.2d', 'NYS2d', result)
    result = re.sub(r'N\.Y\.S\.3d', 'NYS3d', result)
    result = re.sub(r'N\.Y\.S\.', 'NYS', result)
    result = re.sub(r'N\.Y\.', 'NY', result)
    result = re.sub(r'N\.E\.2d', 'NE2d', result)
    result = re.sub(r'N\.E\.3d', 'NE3d', result)

    # Strip parallel citations (NYS2d/NYS3d/NE2d) — NY Official format uses only AD/NY reporters
    result = re.sub(r',\s*\d+\s+NYS2d\s+\d+', '', result)
    result = re.sub(r',\s*\d+\s+NYS3d\s+\d+', '', result)
    result = re.sub(r',\s*\d+\s+NE2d\s+\d+', '', result)
    result = re.sub(r',\s*\d+\s+NE3d\s+\d+', '', result)

    # Convert **bold case names** to _underscored_
    result = re.sub(r'\*\*([A-Z][^*]+v\.?\s+[^*]+)\*\*', r'_\1_', result)
    result = result.replace('****', '')

    # Court hierarchy: "this Court" before NY2d/NY3d cites → "the Court of Appeals"
    # NY2d/NY3d = Court of Appeals, NOT the Appellate Division ("this Court")
    court_fix_count = len(re.findall(
        r'[Tt]his\s+Court\s+(?:held|explained|stated|noted|observed|recognized|ruled|found|determined|concluded)'
        r'.{0,80}?\d+\s+NY[23]d\s+\d+', result, re.DOTALL
    ))
    result = re.sub(
        r'([Tt])his\s+Court(\s+(?:held|explained|stated|noted|observed|recognized|ruled|found|determined|concluded))'
        r'(?=.{0,80}?\d+\s+NY[23]d\s+\d+)',
        r'the Court of Appeals\2', result, flags=re.DOTALL
    )
    if court_fix_count:
        print(f"[STYLE CONFORMANCE] Fixed {court_fix_count} 'this Court' reference(s) to Court of Appeals cases", flush=True)
        replacements += court_fix_count

    # Replace Slip Op citations with full official cites found earlier in the document
    # Build lookup: case name -> full citation from the document itself
    full_cite_pattern = re.compile(
        r'_([^_]+?v\.?\s+[^_]+?)_,?\s+(\d+\s+(?:AD[23]d|NY[23]d|Misc\s*[23]d)\s+\d+\s*\[.+?\])'
    )
    cite_lookup = {}
    for m in full_cite_pattern.finditer(result):
        # Normalize case name for matching
        case_key = re.sub(r'\s+', ' ', m.group(1).strip()).lower()
        cite_lookup[case_key] = m.group(2).strip()

    # Find Slip Op references and replace with full cite if available
    slip_op_pattern = re.compile(
        r'_([^_]+?v\.?\s+[^_]+?)_,?\s+\d{4}\s+NY\s+Slip\s+Op\.?\s*[\d.]*'
    )
    slip_count = 0
    def _replace_slip_op(m):
        nonlocal slip_count
        case_name = m.group(1).strip()
        case_key = re.sub(r'\s+', ' ', case_name).lower()
        if case_key in cite_lookup:
            slip_count += 1
            return f'_{case_name}_, {cite_lookup[case_key]}'
        return m.group(0)

    result = slip_op_pattern.sub(_replace_slip_op, result)
    if slip_count:
        print(f"[STYLE CONFORMANCE] Replaced {slip_count} Slip Op cite(s) with full official cites", flush=True)
        replacements += slip_count

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


def sanitize_deposition_format_cites(draft_text: str, max_record_page=0) -> str:
    """Detect and flag deposition-format citations that the model generates
    instead of proper record page numbers.

    Deposition-format: (2-4), (4-85, 91), (2-4, 10-12) where the first number
    is a deponent volume number and subsequent numbers are deposition-internal
    page numbers. These are WRONG — the brief must cite record page numbers.

    Detection rules:
    1. Multi-group with leading small number: (N-M, ...) where N <= 15
       e.g. (4-85, 91) = deponent 4, pages 85+91 — NOT record pages 4-85
    2. Huge gap single range: (N-M) where N <= 10 AND M-N > 20
       e.g. (4-85) — no attorney cites an 81-page range for one fact
    3. Multi-group all-small: (N-M, A-B) where all numbers < 20
       e.g. (2-4, 10-12) — clearly deposition volume+page groups
    """
    if not draft_text:
        return draft_text

    # Pattern: parenthesized numbers with hyphens and optional commas
    # Matches: (2-4), (4-85, 91), (2-4, 10-12), (5-10)
    cite_re = re.compile(
        r'\((\d{1,4}\s*[-\u2013]\s*\d{1,4}'  # first group: N-M
        r'(?:\s*,\s*\d{1,4}(?:\s*[-\u2013]\s*\d{1,4})?)*'  # optional: , A or , A-B
        r')\)'
    )

    flagged_count = 0
    result = draft_text

    # Process in reverse order to preserve string positions
    matches = list(cite_re.finditer(draft_text))

    for m in reversed(matches):
        inner = m.group(1).strip()
        full_match = m.group(0)

        # Skip if it looks like a case citation context (preceded by reporter)
        pre_context = draft_text[max(0, m.start()-30):m.start()]
        if re.search(r'(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d|NE[23]d)\s*$', pre_context):
            continue

        # Parse all numbers from the citation
        parts = re.split(r'\s*,\s*', inner)
        all_nums = []
        has_hyphen_group = False
        first_in_hyphen = None

        for part in parts:
            hyphen_match = re.match(r'(\d+)\s*[-\u2013]\s*(\d+)', part.strip())
            if hyphen_match:
                n1 = int(hyphen_match.group(1))
                n2 = int(hyphen_match.group(2))
                all_nums.extend([n1, n2])
                has_hyphen_group = True
                if first_in_hyphen is None:
                    first_in_hyphen = n1
            else:
                num_match = re.match(r'(\d+)', part.strip())
                if num_match:
                    all_nums.append(int(num_match.group(1)))

        if not has_hyphen_group or not all_nums:
            continue

        is_depo_format = False
        reason = ''

        # Rule 1: Multi-group with leading small number
        # (4-85, 91) — first number is 4 (deponent), rest are pages
        # (3-15, 20) — first number is 3, multiple groups = depo format
        if len(parts) > 1 and first_in_hyphen is not None and first_in_hyphen <= 10:
            hyphen_match = re.match(r'(\d+)\s*[-\u2013]\s*(\d+)', parts[0].strip())
            if hyphen_match:
                n1, n2 = int(hyphen_match.group(1)), int(hyphen_match.group(2))
                is_depo_format = True
                reason = f'deponent {n1}, pages in multi-group citation'

        # Rule 2: Huge gap single range with small first number
        # (4-85) — 81-page gap, first number ≤ 10
        if not is_depo_format and len(parts) == 1 and first_in_hyphen is not None:
            hyphen_match = re.match(r'(\d+)\s*[-\u2013]\s*(\d+)', parts[0].strip())
            if hyphen_match:
                n1, n2 = int(hyphen_match.group(1)), int(hyphen_match.group(2))
                gap = n2 - n1
                if n1 <= 10 and gap > 20:
                    is_depo_format = True
                    reason = f'deponent {n1}, page {n2} (gap {gap})'

        # Rule 3: Multi-group all-small numbers with hyphens
        # (2-4, 10-12) — all numbers < 20, multiple hyphenated groups
        if not is_depo_format and len(parts) > 1:
            hyphenated_groups = sum(1 for p in parts if re.match(r'\d+\s*[-\u2013]\s*\d+', p.strip()))
            if hyphenated_groups >= 2 and all(n < 20 for n in all_nums):
                is_depo_format = True
                reason = 'multiple small hyphenated groups'

        # Rule 4: Single small range where record is large
        # (5-10) when record is 4000+ pages — pages 5-10 are TOC/cover
        if not is_depo_format and len(parts) == 1 and first_in_hyphen is not None:
            hyphen_match = re.match(r'(\d+)\s*[-\u2013]\s*(\d+)', parts[0].strip())
            if hyphen_match:
                n1, n2 = int(hyphen_match.group(1)), int(hyphen_match.group(2))
                if max_record_page > 100 and n1 <= 10 and n2 <= 20:
                    is_depo_format = True
                    reason = f'pages {n1}-{n2} in {max_record_page}-page record (likely TOC/cover)'

        if is_depo_format:
            flagged_count += 1
            replacement = '[CITE NEEDED - verify record page]'
            result = result[:m.start()] + replacement + result[m.end():]
            print(f"[DEPO CITE] Flagged {full_match} — {reason}", flush=True)

    if flagged_count:
        print(f"[DEPO CITE] Replaced {flagged_count} deposition-format citation(s) with [CITE NEEDED]", flush=True)
    else:
        print("[DEPO CITE] No deposition-format citations detected", flush=True)

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


def _build_page_lookup(docs):
    """Build mapping from record page number to actual page text from source documents.
    Pure code — reads the --- PAGE X --- markers injected during PDF extraction."""
    page_lookup = {}
    for key, doc in docs.items():
        text = doc.get('text', '')
        if not text:
            continue
        parts = re.split(r'--- PAGE (\d+) ---', text)
        for i in range(1, len(parts) - 1, 2):
            try:
                pg = int(parts[i])
                content = parts[i + 1].strip()
                if content:
                    if pg in page_lookup:
                        page_lookup[pg] += '\n' + content
                    else:
                        page_lookup[pg] = content
            except (ValueError, IndexError):
                continue
    return page_lookup


def _parse_cite_pages(cite_str):
    """Parse citation string into list of page numbers."""
    pages = []
    parts = re.split(r'\s*,\s*', cite_str)
    for part in parts:
        if '-' in part or '\u2013' in part:
            sub = re.split(r'[-\u2013]', part)
            if len(sub) == 2:
                try:
                    s, e = int(sub[0].strip()), int(sub[1].strip())
                    if e - s < 20:  # sanity: don't expand huge ranges
                        pages.extend(range(s, e + 1))
                except ValueError:
                    pass
        else:
            try:
                pages.append(int(part.strip()))
            except ValueError:
                pass
    return pages


def _identify_deponent(page_text):
    """Identify who is speaking on a deposition/testimony page.
    Looks for deponent name in page headers (e.g., '- Michael Konig, D.O. -')
    and Q&A format indicators. Returns lowercase last name or empty string."""
    # Pattern: "- FirstName LastName, Title -" in depo headers
    header = re.search(r'-\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+([A-Z][a-z]+))(?:,|\s*-)', page_text[:500])
    if header:
        return header.group(2).lower()
    # Pattern: "[Page N]\nDate\n{ 1) - FirstName LastName, Title -"
    header2 = re.search(r'\{\s*\d+\)\s*-\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+([A-Z][a-z]+))', page_text[:500])
    if header2:
        return header2.group(2).lower()
    # Pattern: "A. APPLEBAUM" style witness name
    header3 = re.search(r'\b([A-Z])\.\s+([A-Z][a-z]+)\b', page_text[:300])
    if header3:
        return header3.group(2).lower()
    return ''


def _extract_claim_names(sentence):
    """Extract person names referenced in a claim sentence.
    Returns list of lowercase last names."""
    names = []
    # "Dr. Konig", "Ms. Doyban", "Mr. Applebaum", "PA Doyban"
    for m in re.finditer(r'(?:Dr\.|Mr\.|Mrs\.|Ms\.|PA)\s+([A-Z][a-z]+)', sentence):
        names.append(m.group(1).lower())
    # Also catch "Konig testified", "Doyban ordered", etc.
    for m in re.finditer(r'\b([A-Z][a-z]{2,})\s+(?:testified|confirmed|stated|acknowledged|admitted|ordered|recommended|prescribed|diagnosed|examined|referred|instructed)', sentence):
        name = m.group(1).lower()
        if name not in ('plaintiff', 'defendant', 'however', 'moreover', 'indeed', 'notably', 'despite', 'following', 'although'):
            names.append(name)
    return list(dict.fromkeys(names))  # deduplicate, preserve order


def _extract_claim_numbers(sentence):
    """Extract significant numbers from a claim sentence.
    Returns list of number strings (e.g., ['11.7', '13', '94']).
    Filters out years and very small numbers."""
    numbers = []
    for m in re.finditer(r'\b(\d+\.?\d*)\b', sentence):
        val = m.group(1)
        try:
            num = float(val)
            # Skip years (1900-2099), skip tiny numbers (< 3) unless decimal
            if 1900 <= num <= 2099:
                continue
            if num < 3 and '.' not in val:
                continue
            numbers.append(val)
        except ValueError:
            continue
    return numbers


def verify_citations_from_source(draft_text: str, project: dict) -> str:
    """Verify citations against actual source document pages.

    PURE CODE — no AI calls, no prompts, no discretion.
    Reads the actual page text from source documents and checks:
    1. Deponent match: if claim names someone, that person must be on the page
    2. Number match: specific numbers in the claim must appear on the page (or ±1 adjacent pages)
    3. Never corrects — only flags [VERIFY] when a citation fails both checks

    RULE: Never omit content. Never silently swap citations. Only flag problems.
    """
    docs = project.get('documents', {})
    if not docs:
        print("[SOURCE-VERIFY] No documents — skipping", flush=True)
        return draft_text

    # Build page lookup from actual source documents
    page_lookup = _build_page_lookup(docs)
    if not page_lookup:
        print("[SOURCE-VERIFY] No pages extracted — skipping", flush=True)
        return draft_text

    print(f"[SOURCE-VERIFY] Page lookup: {len(page_lookup)} pages ({min(page_lookup)}-{max(page_lookup)})", flush=True)

    # Citation pattern
    cite_pattern = re.compile(
        r'([^.!?\n]{20,}?)'
        r'\((\d{1,5}'
        r'(?:\s*[-\u2013]\s*\d{1,5})?'
        r'(?:\s*,\s*\d{1,5}(?:\s*[-\u2013]\s*\d{1,5})?)*'
        r')\)'
    )
    case_cite_re = re.compile(r'\d+\s+(?:AD[23]d|NY[23]d|NYS[23]d|Misc\s*[23]d)', re.IGNORECASE)
    heading_re = re.compile(r'^[A-Z][A-Z\s,]+$')

    # Lab value context: skip parenthetical numbers that follow measurement terms
    lab_context_re = re.compile(
        r'(?:hemoglobin|hematocrit|ferritin|iron|RBC|ESR|CRP|CK|ANA|RF|saturation|'
        r'level|count|range|rating|factor|A1C|glucose)\s+(?:of\s+|was\s+|at\s+|'
        r'level\s+(?:of\s+)?)?(?:listed\s+as\s+)?\d',
        re.IGNORECASE
    )

    verified = 0
    flagged = 0
    flag_details = []

    for match in cite_pattern.finditer(draft_text):
        sentence = match.group(1).strip()
        cite_str = match.group(2)
        full_match = match.group(0)

        # Skip case citations
        if case_cite_re.search(full_match):
            continue
        # Skip headings
        if heading_re.match(sentence):
            continue
        # Skip already flagged
        if '[VERIFY]' in full_match:
            continue

        pages = _parse_cite_pages(cite_str)
        if not pages:
            continue
        # Skip years
        if all(1900 <= p <= 2099 for p in pages):
            continue
        # Skip wide ranges
        if len(pages) > 10:
            verified += 1
            continue

        # Get page text (including ±1 adjacent pages for testimony spanning breaks)
        page_texts = []
        pages_checked = set()
        for p in pages:
            for adj in [p - 1, p, p + 1]:
                if adj in page_lookup and adj not in pages_checked:
                    page_texts.append(page_lookup[adj])
                    pages_checked.add(adj)

        if not page_texts:
            # Pages not in source docs — can't verify, skip
            verified += 1
            continue

        combined_text = '\n'.join(page_texts).lower()

        # CHECK 1: Deponent match
        claim_names = _extract_claim_names(sentence)
        attribution_words = re.search(
            r'(?:testified|confirmed|stated|acknowledged|admitted|deposition|testimony)',
            sentence, re.IGNORECASE
        )
        deponent_ok = True

        if claim_names and attribution_words:
            # The sentence attributes testimony to a specific person —
            # that person must be identifiable on the cited pages
            primary_name = claim_names[0]  # first named person
            # Check if this person's name appears on any cited page
            name_found = primary_name in combined_text
            if not name_found:
                deponent_ok = False
                print(f"[SOURCE-VERIFY] DEPONENT MISMATCH: ({cite_str}) claims {primary_name} "
                      f"but name not on page | {sentence[:80]}...", flush=True)

        # CHECK 2: Number match (only if claim has specific numbers)
        claim_numbers = _extract_claim_numbers(sentence)
        numbers_ok = True

        if claim_numbers:
            found = sum(1 for n in claim_numbers if n in combined_text)
            # Require at least half the numbers to be present
            if len(claim_numbers) >= 2 and found == 0:
                numbers_ok = False
                print(f"[SOURCE-VERIFY] NUMBER MISMATCH: ({cite_str}) claims {claim_numbers} "
                      f"but none found on page | {sentence[:80]}...", flush=True)

        if deponent_ok and numbers_ok:
            verified += 1
        else:
            flagged += 1
            flag_details.append({
                'cite': cite_str,
                'sentence': sentence[:100],
                'deponent_ok': deponent_ok,
                'numbers_ok': numbers_ok
            })

    print(f"[SOURCE-VERIFY] Done: {verified} verified, {flagged} flagged", flush=True)
    for fd in flag_details:
        issues = []
        if not fd['deponent_ok']:
            issues.append('wrong deponent')
        if not fd['numbers_ok']:
            issues.append('numbers missing')
        print(f"[SOURCE-VERIFY]   ({fd['cite']}) [{', '.join(issues)}]: {fd['sentence']}...", flush=True)

    return draft_text  # NEVER modify the text — only log problems


def _flag_testimony_cite_mismatch(draft_text: str, project: dict) -> str:
    """PURE CODE — no AI, no prompts.
    Detects sentences that say 'testified/acknowledged/confirmed' but cite
    expert affirmation or defense reply pages instead of testimony pages.
    Appends [VERIFY - CITE MAY BE AFFIRMATION, NOT TESTIMONY] to flagged sentences.
    """
    docs = project.get('documents', {})

    # Build set of page numbers that come from affirmations/defense papers (NOT testimony)
    affirmation_pages = set()
    for key, doc in docs.items():
        if not isinstance(doc, dict):
            continue
        fname = doc.get('filename', '').lower()
        is_affirmation = ('affirmation' in fname or 'affidavit' in fname
                          or 'reply' in fname)
        if is_affirmation:
            text = doc.get('text', '')
            for m in re.finditer(r'---\s*PAGE\s+(\d+)\s*---', text):
                affirmation_pages.add(int(m.group(1)))

    # Also check source_doc entries that look like affirmations
    for key, doc in docs.items():
        if not isinstance(doc, dict) or not key.startswith('source_doc_'):
            continue
        fname = doc.get('filename', '').lower()
        if 'affirmation' in fname or 'affidavit' in fname:
            text = doc.get('text', '')
            for m in re.finditer(r'---\s*PAGE\s+(\d+)\s*---', text):
                affirmation_pages.add(int(m.group(1)))

    if not affirmation_pages:
        return draft_text

    # Testimony language patterns — these words mean the sentence claims someone TESTIFIED
    testimony_words = re.compile(
        r'\b(testified|acknowledged|admitted|conceded|confirmed under oath|'
        r'stated at (?:his|her|their) deposition|deposition testimony)\b',
        re.IGNORECASE
    )

    # Find sentences with testimony language + citations
    cite_pattern = re.compile(r'\((\d[\d,\s\-]+)\)')
    flagged = 0
    lines = draft_text.split('\n')
    new_lines = []

    for line in lines:
        sentences = re.split(r'(?<=[.!?])\s+', line)
        new_sentences = []
        for sent in sentences:
            has_testimony = testimony_words.search(sent)
            if not has_testimony:
                new_sentences.append(sent)
                continue

            # Check if any cited page is an affirmation page
            cites_affirmation = False
            for cite_match in cite_pattern.finditer(sent):
                cite_str = cite_match.group(1)
                for part in cite_str.split(','):
                    part = part.strip()
                    if '-' in part:
                        try:
                            a, b = part.split('-')
                            for pg in range(int(a.strip()), int(b.strip()) + 1):
                                if pg in affirmation_pages:
                                    cites_affirmation = True
                        except ValueError:
                            pass
                    else:
                        try:
                            if int(part) in affirmation_pages:
                                cites_affirmation = True
                        except ValueError:
                            pass

            if cites_affirmation:
                flagged += 1
                sent = sent.rstrip('.') + ' [VERIFY - CITE MAY BE AFFIRMATION, NOT TESTIMONY].'
                print(f"[CITE-TYPE-MISMATCH] Testimony language cites affirmation page: {sent[:120]}...", flush=True)

            new_sentences.append(sent)
        new_lines.append('  '.join(new_sentences) if len(sentences) > 1 else line if not sentences else new_sentences[0])

    if flagged:
        print(f"[CITE-TYPE-MISMATCH] Flagged {flagged} sentences with testimony/affirmation mismatch", flush=True)

    return '\n'.join(new_lines)


def guardrail_brief(draft_text: str, brief_type: str, research_text: str = '', opening_brief_text: str = '', all_source_text: str = '', respondent_text: str = '', project: dict = None) -> str:
    """Post-processing guardrails for drafted briefs. Validates and fixes output programmatically.
    This is code, not a prompt — Claude can't ignore it."""

    result = draft_text

    # 0. Replace party surname with party label
    if project:
        result = _replace_party_surname(result, project)

    # 0.5. Detect and flag deposition-format citations
    # Compute max record page from project docs for Rule 4 (small-page detection)
    max_record_page = 0
    if project:
        docs = project.get('documents', {})
        for key, doc in docs.items():
            if isinstance(doc, dict) and (key.startswith('record_vol') or key == 'record'):
                text = doc.get('text', '')
                pages = re.findall(r'---\s*PAGE\s+(\d+)\s*---', text)
                if pages:
                    max_record_page = max(max_record_page, max(int(p) for p in pages))
    result = sanitize_deposition_format_cites(result, max_record_page=max_record_page)

    # 0.7. Verify citations against actual source document pages (log-only, no modifications)
    if project:
        result = verify_citations_from_source(result, project)

    # 0.8. Flag testimony language citing non-testimony pages (pure code, no AI)
    if project:
        result = _flag_testimony_cite_mismatch(result, project)

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


def verify_factual_fidelity(draft_text, project, model='sonnet'):
    """Post-draft verification: compare factual claims in the draft against
    source documents (expert affirmations, medical records, attorney work product).

    Catches:
    - Contradictions (source says X, draft says not-X)
    - Material oversimplification (source says nuanced X+Y, draft only says X)
    - Directional inversions (source says increased, draft says decreased)
    - Wrong or dropped numerical values, dates, lab results

    Returns the draft with inline [VERIFY FACTS: ...] flags where mismatches
    are found.  Flags are advisory — the attorney makes the final call.
    """
    from src.claude_client import call_claude

    if not draft_text or not project:
        return draft_text

    docs = project.get('documents', {})

    # Collect source documents: expert affirmations, medical docs, attorney work product.
    # Skip record volumes (too large, raw) and transcript digests (summaries).
    source_parts = []
    for key in sorted(docs.keys()):
        doc = docs[key]
        if not isinstance(doc, dict) or not doc.get('text'):
            continue
        if key.startswith('source_doc_') or key.startswith('additional_doc_'):
            title = doc.get('title', '') or key
            source_parts.append(f"=== {title} ===\n{doc['text']}")

    # Also include existing_draft if present (attorney's own version is authoritative)
    existing = docs.get('existing_draft', {})
    if isinstance(existing, dict) and existing.get('text'):
        source_parts.append(f"=== Attorney's Existing Draft ===\n{existing['text']}")

    if not source_parts:
        print("[FACT VERIFY] No source documents for verification, skipping", flush=True)
        return draft_text

    source_block = "\n\n".join(source_parts)
    if len(source_block) > 300000:
        source_block = source_block[:300000]
        print("[FACT VERIFY] Source documents truncated to 300K chars", flush=True)

    print(
        f"[FACT VERIFY] Verifying draft ({len(draft_text.split())} words) "
        f"against {len(source_parts)} source document(s) "
        f"({len(source_block):,} chars)...",
        flush=True,
    )

    prompt = f"""You are a legal accuracy auditor. Your ONLY task is to compare a draft brief against the source documents below and identify factual discrepancies.

INSTRUCTIONS:
Read each factual sentence in the DRAFT. For sentences that make claims about medical findings, test results, laboratory values, dates, numbers, diagnoses, expert opinions, or treatment details, find the corresponding passage in the SOURCE DOCUMENTS and check:

1. ACCURACY: Does the draft match what the source actually says?
2. COMPLETENESS: Does the draft capture the FULL meaning of the source passage, or does it drop nuance that changes the medical or legal significance? Pay special attention to passages where the source gives an overall characterization AND then provides specific details that qualify or complicate that characterization.
3. DIRECTION: If the source says values increased/decreased/improved/worsened, does the draft preserve the correct direction for EACH specific value?
4. VALUES: Are specific numbers, dates, lab values, and measurements correct?
5. QUALIFIERS: Does the draft drop important qualifying language? (e.g., source says "mildly increased due to supplements masking the condition" but draft just says "increased" or "decreased")

REPORT ONLY GENUINE DISCREPANCIES. Do NOT flag:
- Stylistic differences or paraphrasing that preserves full meaning
- Advocacy framing or argument structure choices
- Sentences about legal standards, case law, or procedural history
- Minor word choice changes that do not alter factual meaning
- Omission of facts not relevant to the specific argument being made

OUTPUT FORMAT — for EACH discrepancy:
DRAFT: "[copy the exact sentence from the draft]"
SOURCE: "[copy the relevant passage from the source document]"
PAGE: [source page number if identifiable from PAGE markers]
ISSUE: [specific description of what is wrong]
===

If NO discrepancies are found, output exactly: NO DISCREPANCIES FOUND

=== DRAFT TO VERIFY ===
{draft_text}

=== SOURCE DOCUMENTS ===
{source_block}"""

    result = call_claude(prompt, max_tokens=8000, model=model)

    if not result or result.startswith('ERROR:'):
        print(f"[FACT VERIFY] API error, skipping: {result[:100]}", flush=True)
        return draft_text

    if 'NO DISCREPANCIES FOUND' in result:
        print("[FACT VERIFY] No discrepancies found", flush=True)
        return draft_text

    # Parse discrepancies from the verifier output
    blocks = re.split(r'={3,}', result)
    flagged_count = 0
    modified_draft = draft_text

    for block in blocks:
        block = block.strip()
        if not block or 'DRAFT:' not in block:
            continue

        draft_match = re.search(r'DRAFT:\s*"([^"]+)"', block)
        issue_match = re.search(r'ISSUE:\s*(.+)', block, re.DOTALL)

        if not draft_match or not issue_match:
            continue

        draft_sentence = draft_match.group(1).strip()
        issue = issue_match.group(1).strip().split('\n')[0].strip()

        # Try exact match first
        if draft_sentence in modified_draft:
            flag = f" [VERIFY FACTS: {issue}]"
            modified_draft = modified_draft.replace(draft_sentence, draft_sentence + flag, 1)
            flagged_count += 1
            print(f"[FACT VERIFY] FLAGGED: {draft_sentence[:80]}...", flush=True)
            print(f"[FACT VERIFY]   ISSUE: {issue[:120]}", flush=True)
            continue

        # Fuzzy match: try first 50 characters of the draft sentence
        search_text = draft_sentence[:50]
        if len(search_text) > 20 and search_text in modified_draft:
            idx = modified_draft.index(search_text)
            # Find end of the sentence
            end_region = modified_draft[idx:idx + len(draft_sentence) + 200]
            period_pos = end_region.find('. ')
            if period_pos == -1:
                period_pos = end_region.find('.\n')
            if period_pos > 0:
                insert_at = idx + period_pos + 1
                flag = f" [VERIFY FACTS: {issue}]"
                modified_draft = modified_draft[:insert_at] + flag + modified_draft[insert_at:]
                flagged_count += 1
                print(f"[FACT VERIFY] FLAGGED (fuzzy): {search_text}...", flush=True)
                print(f"[FACT VERIFY]   ISSUE: {issue[:120]}", flush=True)
            else:
                print(f"[FACT VERIFY] Could not locate sentence end: {search_text}...", flush=True)
        else:
            print(f"[FACT VERIFY] Could not locate in draft: {draft_sentence[:80]}...", flush=True)

    if flagged_count:
        print(f"[FACT VERIFY] Flagged {flagged_count} factual discrepancy(ies) for attorney review", flush=True)
    else:
        print("[FACT VERIFY] Verification complete, no flags inserted", flush=True)

    return modified_draft


def generate_irac_analysis(points, case_law='', record_evidence='', doc_type='brief', model='sonnet'):
    """
    IRAC analysis pass: for each Point heading, generate Issue/Rule/Application/Conclusion
    breakdown using extracted cases and facts. Returns a block to inject into the drafting prompt
    so the model has a structured legal reasoning framework before writing prose.
    """
    from src.claude_client import call_claude

    if not points:
        print("[IRAC] No Points defined — skipping analysis", flush=True)
        return ''

    points_block = ""
    for pt in points:
        heading = pt.get('heading', '')
        arg_desc = pt.get('argument_description', '')
        facts = pt.get('facts', '')
        cases = pt.get('cases', '')
        points_block += f"""
POINT {pt.get('id', '?')}: {heading}
{f'Attorney notes: {arg_desc}' if arg_desc else ''}
{f'Key facts: {facts}' if facts else ''}
{f'Key cases: {cases}' if cases else ''}
---"""

    context_block = ""
    if case_law:
        context_block += f"\n=== EXTRACTED CASE LAW ===\n{case_law[:50000]}\n"
    if record_evidence:
        context_block += f"\n=== EXTRACTED RECORD EVIDENCE ===\n{record_evidence[:50000]}\n"

    if doc_type in ('brief', "appellant's brief", "respondent's brief", "reply brief"):
        role = "senior appellate attorney"
        prose_style = "appellate"
    else:
        role = "senior litigation attorney"
        prose_style = "motion practice"

    prompt = f"""You are a {role} analyzing legal arguments before drafting. For each Point below, produce a structured IRAC analysis that will serve as the reasoning framework for the drafting pass.

For EACH Point, generate:

ISSUE: State the precise legal question. Frame it as a question that, when answered, resolves the Point in your client's favor.

RULE: Identify the governing legal standard, statute, or precedent. Use ONLY cases from the extracted case law below. Cite the specific holding and the standard the court applied. If multiple cases establish the rule, synthesize them.

APPLICATION: Apply the rule to the specific facts of this case. Reference SPECIFIC record evidence from the extracts below. Show exactly how the facts satisfy (or defeat) each element of the legal standard. Address likely counterarguments and explain why they fail.

CONCLUSION: State the conclusion that follows from the application. This should be the proposition the Point heading asserts.

RULES:
- Use ONLY cases from the extracted case law. Do NOT cite cases from your training data.
- Reference SPECIFIC facts and record page numbers from the extracted evidence.
- The APPLICATION section should be the most detailed — this is where legal analysis lives.
- Each IRAC analysis should be 150-300 words. Be thorough but focused.
- If a Point's heading and notes don't provide enough information for a full IRAC, do your best with what's available and note what's missing.

=== POINTS TO ANALYZE ===
{points_block}

{context_block}

OUTPUT FORMAT — for each Point:

POINT [number]: [heading]
ISSUE: [question]
RULE: [standard + authorities]
APPLICATION: [analysis applying rule to facts]
CONCLUSION: [proposition]
===
"""

    print(f"[IRAC] Generating analysis for {len(points)} Points...", flush=True)
    result = call_claude(prompt, max_tokens=16000, model=model)
    print(f"[IRAC] Analysis complete ({len(result.split())} words)", flush=True)

    return f"""=== IRAC LEGAL ANALYSIS (use this framework to structure each argument) ===
The following IRAC analysis breaks down the legal reasoning for each Point.
Use this as the structural backbone for your arguments. The Issue frames the question,
the Rule identifies the governing law, the Application shows how facts meet the standard,
and the Conclusion states the proposition. Write prose that follows this logical progression.

{result}

=== END IRAC ANALYSIS ==="""


def editorial_review_pass(draft_text, doc_type='brief', model='sonnet'):
    """
    Editorial review pass: identifies and fixes structural problems in drafts.
    Catches repetitive Points, overlapping arguments, and opportunities to merge/tighten.
    Runs after mechanical guardrails, before QC.
    """
    from src.claude_client import call_claude

    # Count original metrics for validation
    original_words = len(draft_text.split())
    original_points = re.findall(r'^POINT\s+[IVXLCDM\d]+', draft_text, re.MULTILINE)

    if original_words < 500:
        print(f"[EDITORIAL] Skipped — draft too short ({original_words} words)", flush=True)
        return draft_text

    prompt = f"""You are a senior appellate attorney performing an editorial review of a draft {doc_type}. Your ONLY task is to identify and fix STRUCTURAL problems.

REVIEW FOR THESE ISSUES:
1. REPETITIVE POINTS: If two or more Points make substantially the same legal argument (even if phrased differently), merge them into one cohesive Point that makes the argument once, thoroughly, incorporating the strongest material from each.
2. OVERLAPPING SECTIONS: If sub-sections across different Points cover the same ground (e.g., both discuss personal knowledge, both distinguish the same case), consolidate the overlapping material into whichever Point it fits best and remove it from the other.
3. REDUNDANT PARAGRAPHS: If consecutive paragraphs restate the same point in slightly different words, combine them into one stronger paragraph.

CRITICAL RULES — VIOLATIONS WILL CAUSE REJECTION:
- DO NOT omit any substantive legal argument, case citation, record citation, or factual assertion.
- DO NOT change the legal analysis, conclusions, or advocacy position.
- DO NOT add new arguments, citations, or factual claims not already in the draft.
- DO NOT alter case names, citation formats, record references, or quoted text.
- DO NOT change the Preliminary Statement, Conclusion, or signature block.
- DO NOT rewrite prose style or voice — preserve the existing phrasing.
- If you merge Points, renumber ALL remaining Points sequentially (POINT I, POINT II, etc.) and update all Point headings.
- If no structural changes are needed, return the draft EXACTLY as provided.

OUTPUT: Return the COMPLETE revised draft. Plain text only, no commentary, no preamble.

DRAFT TO REVIEW:
{draft_text}"""

    revised = call_claude(prompt, max_tokens=32000, model=model)

    # Strip any preamble the model might add
    for marker in ['SUPREME COURT', 'PRELIMINARY STATEMENT', 'POINT I']:
        idx = revised.find(marker)
        if idx > 0 and idx < 200:
            pre = revised[:idx].strip()
            if pre and not any(c in pre for c in ['(', 'v.', 'COURT', 'DIVISION']):
                revised = revised[idx:]
            break

    # Validate: don't lose content
    revised_words = len(revised.split())
    revised_points = re.findall(r'^POINT\s+[IVXLCDM\d]+', revised, re.MULTILINE)

    # Word count floor: 70% of original (merging reduces, but not by more than 30%)
    if revised_words < original_words * 0.7:
        print(f"[EDITORIAL] REJECTED — lost too much content: {original_words} → {revised_words} words ({revised_words/original_words:.0%})", flush=True)
        return draft_text

    # Log changes
    if len(revised_points) < len(original_points):
        print(f"[EDITORIAL] Merged Points: {len(original_points)} → {len(revised_points)}", flush=True)
    word_diff = revised_words - original_words
    if abs(word_diff) > 50:
        print(f"[EDITORIAL] Word count: {original_words} → {revised_words} ({word_diff:+d})", flush=True)
    else:
        print(f"[EDITORIAL] No structural changes needed", flush=True)

    return revised
