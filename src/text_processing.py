"""
BriefDrafter text processing: pure text manipulation utilities.
"""

import re
from src.config import MAX_TOTAL_CHARS, _STOP_WORDS


def _strip_opposing_brief_chrome(text):
    """Strip cover page, TOC, attorney block, and printing specs from opposing brief.
    These are formatting elements that the AI should never see or copy."""
    if not text:
        return text
    lines = text.split('\n')
    cleaned_lines = []
    skip_until_substance = True
    in_toc = False
    in_attorney_block = False
    in_printing_specs = False

    for i, line in enumerate(lines):
        upper = line.strip().upper()

        # Skip printing specifications statement at the end
        if 'PRINTING SPECIFICATIONS' in upper or 'PRINTING SPECIFICATION' in upper:
            in_printing_specs = True
            continue
        if in_printing_specs:
            continue

        # Detect and skip TOC sections
        if upper in ('TABLE OF CONTENTS', 'TABLE OF AUTHORITIES'):
            in_toc = True
            continue
        if in_toc:
            # TOC ends when we hit a substantive heading
            if upper in ('PRELIMINARY STATEMENT', 'SUMMARY OF ARGUMENT', 'STATEMENT OF QUESTIONS PRESENTED',
                         'STATEMENT OF THE CASE', 'STATEMENT OF FACTS', 'FACTS', 'ARGUMENT',
                         'COUNTERSTATEMENT OF FACTS', 'COUNTERSTATEMENT OF QUESTIONS PRESENTED',
                         'PROCEDURAL HISTORY', 'INTRODUCTION'):
                in_toc = False
                skip_until_substance = False
                cleaned_lines.append(line)
                continue
            # Still in TOC — skip lines with page number patterns (dots or numbers at end)
            if re.search(r'\.{3,}|…+|\d+\s*$', line.strip()) or not line.strip():
                continue
            # Non-TOC-looking line while in_toc — might be end of TOC
            if len(line.strip()) > 5 and not re.search(r'\d+$', line.strip()):
                in_toc = False
                skip_until_substance = False

        # Skip cover page / caption block (everything before first substantive heading)
        if skip_until_substance:
            if upper in ('PRELIMINARY STATEMENT', 'SUMMARY OF ARGUMENT', 'STATEMENT OF QUESTIONS PRESENTED',
                         'STATEMENT OF THE CASE', 'STATEMENT OF FACTS', 'FACTS', 'ARGUMENT',
                         'COUNTERSTATEMENT OF FACTS', 'COUNTERSTATEMENT OF QUESTIONS PRESENTED',
                         'PROCEDURAL HISTORY', 'INTRODUCTION'):
                skip_until_substance = False
                cleaned_lines.append(line)
                continue
            continue

        # Detect attorney signature blocks (firm names, addresses, phone numbers)
        if re.match(r'^\s*(Respectfully submitted|Dated:)', line, re.IGNORECASE):
            in_attorney_block = True
            continue
        if in_attorney_block:
            continue

        cleaned_lines.append(line)

    result = '\n'.join(cleaned_lines).strip()
    # If stripping removed too much (>90%), return original — something went wrong
    if len(result) < len(text) * 0.1:
        return text
    return result


def _truncate(text, max_chars):
    """Truncate text to max_chars, noting truncation if applied"""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... DOCUMENT TRUNCATED at {max_chars} characters due to size limits ...]"


def _fit_documents(doc_list, max_total=MAX_TOTAL_CHARS):
    """Proportionally truncate a list of (label, text, priority) tuples to fit max_total.
    Priority: 'critical' docs are included in full first (never truncated),
    'primary' docs get 2x share vs 'secondary' docs.
    Documents smaller than their share redistribute surplus to larger docs."""
    total = sum(len(t) for _, t, _ in doc_list if t)
    if total <= max_total:
        return [(label, text) for label, text, _ in doc_list]

    # Critical docs get their full text first — never truncated
    critical_used = 0
    for i, (label, text, priority) in enumerate(doc_list):
        if text and priority == 'critical':
            critical_used += len(text)

    remaining_budget = max(0, max_total - critical_used)

    # Two-pass allocation for non-critical docs
    docs_with_text = [(i, label, text, priority) for i, (label, text, priority) in enumerate(doc_list) if text and priority != 'critical']
    total_weight = sum(2.0 if p == 'primary' else 1.0 for _, _, _, p in docs_with_text)

    allocations = {}
    # Critical docs get full allocation
    for i, (label, text, priority) in enumerate(doc_list):
        if text and priority == 'critical':
            allocations[i] = len(text)

    for i, label, text, priority in docs_with_text:
        weight = 2.0 if priority == 'primary' else 1.0
        allocations[i] = int(remaining_budget * (weight / total_weight)) if total_weight > 0 else 0

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
