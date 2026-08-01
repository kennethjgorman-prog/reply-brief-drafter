"""
Proactive Citation Validator for BriefDrafter
Validates that page citations in drafts fall within known record page ranges.
Adapted from Transcript Summarizer validate_page_citations().
"""

import re
from typing import Dict, List, Tuple, Optional


def extract_all_citations(draft: str) -> List[Dict]:
    """
    Extract all record-page citations from a brief draft.

    Returns list of dicts: {'text': '(125)', 'page': 125, 'position': char_offset}
    """
    citations = []

    # Bare record page citations: (125), (125-130), (125, 127, 130)
    for m in re.finditer(r'\((\d{1,5}(?:\s*[-\u2013]\s*\d{1,5})?(?:\s*,\s*\d{1,5})*)\)', draft):
        full_text = m.group(0)
        inner = m.group(1)
        # Skip if it looks like a case cite: (2d Dept 2020), (123 AD3d 456)
        if re.search(r'[A-Za-z]', inner):
            continue

        # Skip if the parenthetical is a single 4-digit number that looks
        # like a year (1800-2099). Common false positive: "Building Code of
        # the City of New York (1968)", "Penal Law (1990)", etc.
        single_num_match = re.fullmatch(r'\s*(\d+)\s*', inner)
        if single_num_match:
            num = int(single_num_match.group(1))
            if 1800 <= num <= 2099:
                # Check if context suggests a year reference (statute, code, act, etc.)
                ctx_start = max(0, m.start() - 60)
                preceding = draft[ctx_start:m.start()]
                year_context_re = re.compile(
                    r'\b(?:Code|Act|Law|Statute|Rules?|Regulations?|Constitution|'
                    r'Edition|Ed\.?|Amendment|Chapter|Title|Article|Section|'
                    r'§|enacted|adopted|amended|effective|version|year|circa|c\.|'
                    r'published|promulgated|of(?:\s+the)?(?:\s+\w+){0,4})\b',
                    re.IGNORECASE
                )
                if year_context_re.search(preceding):
                    continue
                # Even without explicit context, a bare 4-digit year is more
                # likely a year than a record page in that range.
                continue

        # Skip year-like numbers in brackets context
        pages = re.findall(r'\d+', inner)
        for p in pages:
            page_num = int(p)
            if page_num < 10000:  # skip years or very large numbers
                citations.append({
                    'text': full_text,
                    'page': page_num,
                    'position': m.start(),
                })

    # Transcript citations: (Tr. at 125:14-16), (Tr. 125)
    for m in re.finditer(r'\(Tr\.\s*(?:at\s+)?(\d+)', draft):
        citations.append({
            'text': m.group(0),
            'page': int(m.group(1)),
            'position': m.start(),
        })

    return citations


def validate_page_ranges(citations: List[Dict], known_ranges: List[Tuple[int, int]]) -> Dict:
    """
    Validate extracted citations against known document page ranges.

    Args:
        citations: Output from extract_all_citations()
        known_ranges: List of (start_page, end_page) tuples from uploaded record volumes

    Returns:
        Dict with valid/invalid citations and summary stats
    """
    if not known_ranges:
        return {
            'validated': False,
            'reason': 'No record page ranges available for validation',
            'total': len(citations),
            'valid': [],
            'invalid': [],
        }

    # Build set of all valid pages
    valid_pages = set()
    for start, end in known_ranges:
        valid_pages.update(range(start, end + 1))

    valid_cites = []
    invalid_cites = []

    for cite in citations:
        if cite['page'] in valid_pages:
            valid_cites.append(cite)
        else:
            invalid_cites.append(cite)

    return {
        'validated': True,
        'total': len(citations),
        'valid_count': len(valid_cites),
        'invalid_count': len(invalid_cites),
        'valid': valid_cites,
        'invalid': invalid_cites,
        'known_ranges': known_ranges,
    }


def flag_violations(draft: str, validation_result: Dict) -> str:
    """
    Insert [CITATION WARNING] flags into draft text for invalid citations.

    Args:
        draft: Original draft text
        validation_result: Output from validate_page_ranges()

    Returns:
        Draft text with warnings inserted after invalid citations
    """
    if not validation_result.get('validated') or not validation_result['invalid']:
        return draft

    # Sort invalid citations by position (reverse order to preserve offsets)
    invalid = sorted(validation_result['invalid'], key=lambda c: c['position'], reverse=True)

    flagged = draft
    for cite in invalid:
        pos = cite['position']
        cite_text = cite['text']
        # Find the end of the citation text
        end_pos = pos + len(cite_text)
        # Handle closing paren
        if end_pos < len(flagged) and flagged[end_pos - 1] != ')':
            close = flagged.find(')', pos)
            if close != -1:
                end_pos = close + 1

        warning = f" [CITATION WARNING: page {cite['page']} not found in uploaded record]"
        flagged = flagged[:end_pos] + warning + flagged[end_pos:]

    return flagged


def get_record_page_ranges(project_docs: Dict) -> List[Tuple[int, int]]:
    """
    Extract page ranges from uploaded record volumes in a BD project.

    Args:
        project_docs: The project's documents dict

    Returns:
        List of (start_page, end_page) tuples
    """
    ranges = []

    for key, doc in project_docs.items():
        if not isinstance(doc, dict):
            continue
        # Record volumes typically have page range metadata
        if 'page_range' in doc:
            pr = doc['page_range']
            if isinstance(pr, dict) and 'start' in pr and 'end' in pr:
                ranges.append((pr['start'], pr['end']))
        elif key.startswith('record_vol'):
            text = doc.get('text', '')
            if text:
                # Extract page numbers from page markers: --- PAGE 125 ---
                pages = re.findall(r'---\s*PAGE\s+(\d+)\s*---', text, re.IGNORECASE)
                if pages:
                    page_nums = [int(p) for p in pages]
                    ranges.append((min(page_nums), max(page_nums)))

    return ranges


def generate_validation_report(validation_result: Dict) -> str:
    """Generate a human-readable citation validation report."""
    if not validation_result.get('validated'):
        return f"Citation validation skipped: {validation_result.get('reason', 'unknown')}"

    lines = []
    lines.append(f"Citation Validation: {validation_result['valid_count']}/{validation_result['total']} valid")

    if validation_result['invalid']:
        lines.append(f"  Invalid citations ({validation_result['invalid_count']}):")
        for cite in validation_result['invalid'][:10]:
            lines.append(f"    Page {cite['page']} {cite['text']}")
        if validation_result['invalid_count'] > 10:
            lines.append(f"    ... and {validation_result['invalid_count'] - 10} more")

    return "\n".join(lines)
