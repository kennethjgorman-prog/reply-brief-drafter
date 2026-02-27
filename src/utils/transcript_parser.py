"""
Witness Map Extractor for BriefDrafter
Ported from MotionDrafter's transcript_parser.py.
Extracts witness testimony ranges and party affiliations from trial transcripts
embedded in the appellate record.
"""

import re
import pdfplumber
from pathlib import Path


def extract_witness_map(file_path: str) -> dict:
    """Extract a witness map from a trial transcript PDF.

    Uses three detection strategies in priority order:

    1. Trial transcript page headers (highest confidence):
           Dr. Apazidis - Plaintiff - Direct
    2. Deposition-style headers:
           DIRECT - DR. POPOWITZ - MR. BASS 134
    3. Body text fallback:
           DIRECT EXAMINATION / BY MR. BRENNAN: / Q Good afternoon, Dr. Saberski.

    Returns:
        {
            "entries": [
                {"witness": "Dr. Popowitz", "exam_type": "Direct",
                 "party": "Plaintiff",
                 "start_page": 134, "end_page": 167},
                ...
            ],
            "unattributed_pages": [124, 125, ...]
        }
    """
    entries = []
    page_witness = {}
    all_pages = set()
    header_pages = set()
    header_pdf_indices = set()

    trial_header_re = re.compile(
        r'^(.+?)\s*[-\u2013\u2014]\s*'
        r'(Plaintiff|Defendant|Defense)\s*[-\u2013\u2014]\s*'
        r'(Direct|Cross|Re-?direct|Re-?cross|Redirect|Recross)'
        r'(?:\s+(\d+))?',
        re.IGNORECASE | re.MULTILINE
    )

    proceedings_re = re.compile(
        r'^(?:PROCEEDINGS|Proceedings)\s+(\d+)',
        re.MULTILINE
    )

    depo_header_re = re.compile(
        r'^(DIRECT|CROSS|RE-?DIRECT|RE-?CROSS|REDIRECT|RECROSS)'
        r'\s*[-\u2013\u2014]\s*'
        r'(.+?)'
        r'\s*[-\u2013\u2014]\s*'
        r'.+?\s+(\d+)\s*$',
        re.MULTILINE
    )

    exam_body_re = re.compile(
        r'(DIRECT|CROSS|RE-?DIRECT|RE-?CROSS|REDIRECT|RECROSS)\s+EXAMINATION',
        re.IGNORECASE
    )
    by_attorney_re = re.compile(
        r'BY\s+(?:MR|MS|MRS)\.\s+[A-Z][A-Za-z]+\s*:',
        re.IGNORECASE
    )
    greeting_re = re.compile(
        r'Q\s+.*?(?:Dr\.|Mr\.|Ms\.|Mrs\.)\s+([A-Z][a-z]+)',
        re.IGNORECASE
    )

    called_re = re.compile(
        r'([A-Z][A-Z\s]+[A-Z]),?\s+called as a witness'
        r'.+?(?:by|on behalf of)\s+(?:the\s+)?(Plaintiff|Defendant|Defense)',
        re.IGNORECASE | re.DOTALL
    )

    called_witnesses = {}

    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if not text.strip():
                continue

            lines = text.split('\n')
            tr_page = i
            header_text = '\n'.join(lines[:5])

            for line_idx in range(min(3, len(lines))):
                line = lines[line_idx].strip()
                if re.match(r'^\d+$', line) and 1 <= int(line) <= 9999:
                    tr_page = int(line)
                    break
            else:
                m_th = trial_header_re.search(header_text)
                if m_th and m_th.group(4):
                    tr_page = int(m_th.group(4))
                else:
                    m_pr = proceedings_re.search(header_text)
                    if m_pr:
                        tr_page = int(m_pr.group(1))
            all_pages.add(tr_page)

            m = trial_header_re.search(header_text)
            if m:
                witness = _normalize_witness_name(m.group(1).strip())
                party = _normalize_party(m.group(2).strip())
                exam_type = _normalize_exam_type(m.group(3).strip())
                page_witness[tr_page] = (witness, exam_type, party, 'trial_header')
                header_pages.add(tr_page)
                header_pdf_indices.add(i)
                continue

            m = depo_header_re.search(text)
            if m:
                witness = _normalize_witness_name(m.group(2).strip())
                exam_type = _normalize_exam_type(m.group(1).strip())
                page_witness[tr_page] = (witness, exam_type, '', 'depo_header')
                header_pages.add(tr_page)
                header_pdf_indices.add(i)
                continue

            cm = called_re.search(text)
            if cm:
                collapsed = _collapse_spaced_name(cm.group(1).strip())
                if collapsed:
                    party = _normalize_party(cm.group(2).strip())
                    called_witnesses[collapsed.lower()] = party

        # Strategy 3: Body text fallback
        for i, page in enumerate(pdf.pages, 1):
            if i in header_pdf_indices:
                continue

            text = page.extract_text() or ""
            if not text.strip():
                continue

            lines = text.split('\n')
            tr_page = i
            header_text_fb = '\n'.join(lines[:5])
            for line_idx in range(min(3, len(lines))):
                line = lines[line_idx].strip()
                if re.match(r'^\d+$', line) and 1 <= int(line) <= 9999:
                    tr_page = int(line)
                    break
            else:
                m_pr = proceedings_re.search(header_text_fb)
                if m_pr:
                    tr_page = int(m_pr.group(1))

            if tr_page in header_pages:
                continue

            exam_match = exam_body_re.search(text)
            if exam_match:
                exam_type = _normalize_exam_type(exam_match.group(1))
                after_exam = text[exam_match.end():exam_match.end() + 500]
                by_match = by_attorney_re.search(after_exam)
                if by_match:
                    after_by = after_exam[by_match.end():by_match.end() + 200]
                    greeting = greeting_re.search(after_by)
                    if greeting:
                        witness_last = greeting.group(1)
                        full_greeting = greeting.group(0)
                        title = ''
                        for t in ['Dr.', 'Mr.', 'Ms.', 'Mrs.']:
                            if t in full_greeting:
                                title = t + ' '
                                break
                        witness = title + witness_last
                        page_witness[tr_page] = (witness, exam_type, '', 'body')

    # Collapse into ranges
    if not page_witness:
        return {'entries': [], 'unattributed_pages': sorted(all_pages)}

    sorted_pages = sorted(page_witness.keys())
    current_witness, current_exam, current_party, current_source = page_witness[sorted_pages[0]]
    current_start = sorted_pages[0]
    current_end = sorted_pages[0]
    current_auto = current_source == 'body'

    for pg in sorted_pages[1:]:
        w, e, p, s = page_witness[pg]
        if w == current_witness and e == current_exam:
            current_end = pg
            if s == 'body':
                current_auto = True
            if p and not current_party:
                current_party = p
        else:
            entry = {
                'witness': current_witness,
                'exam_type': current_exam,
                'start_page': current_start,
                'end_page': current_end,
            }
            if current_party:
                entry['party'] = current_party
            if current_auto:
                entry['auto_detected'] = True
            entries.append(entry)
            current_witness, current_exam, current_party, current_source = w, e, p, s
            current_start = pg
            current_end = pg
            current_auto = s == 'body'

    entry = {
        'witness': current_witness,
        'exam_type': current_exam,
        'start_page': current_start,
        'end_page': current_end,
    }
    if current_party:
        entry['party'] = current_party
    if current_auto:
        entry['auto_detected'] = True
    entries.append(entry)

    # Enrich from called_witnesses
    for entry in entries:
        if entry.get('party'):
            continue
        wname = entry['witness'].lower()
        for called_name, party in called_witnesses.items():
            if wname in called_name or called_name in wname:
                entry['party'] = party
                break

    attributed_pages = set()
    for e in entries:
        attributed_pages.update(range(e['start_page'], e['end_page'] + 1))
    unattributed = sorted(all_pages - attributed_pages)

    return {'entries': entries, 'unattributed_pages': unattributed}


def extract_witness_roster_from_digests(documents: dict) -> dict:
    """Extract party and role hints for witnesses from document text.

    Scans all document texts for patterns like:
        "defense expert Dr. Saberski"
        "plaintiff's treating physician Dr. Popowitz"

    Returns dict mapping witness names to {'party': str, 'role': str}.
    """
    roster = {}

    all_text = ''
    for key, doc in documents.items():
        if isinstance(doc, dict) and doc.get('text'):
            all_text += '\n' + doc['text']

    if not all_text:
        return roster

    called_re = re.compile(
        r'(plaintiff|defense|defendant)[\'s]*\s+(?:counsel\s+)?(?:called|retained|hired|engaged)\s+'
        r'(?:Dr\.|Mr\.|Ms\.|Mrs\.)?\s*([A-Z][a-z]{2,})',
        re.IGNORECASE
    )
    role_before_name_re = re.compile(
        r'(plaintiff|defense|defendant)[\'s]*\s+'
        r'((?:treating|expert|independent|medical|economic|retained|consulting)(?:\s+\w+){0,3})'
        r'[,\s]+(?:Dr\.|Mr\.|Ms\.|Mrs\.)?\s*([A-Z][a-z]{2,})',
        re.IGNORECASE
    )
    name_then_role_re = re.compile(
        r'(?:Dr\.|Mr\.|Ms\.|Mrs\.)\s+([A-Z][a-z]{2,})'
        r'[,\s]+(?:the\s+)?'
        r'(plaintiff|defense|defendant)[\'s]*\s+'
        r'((?:treating|expert|independent|medical|economic|retained|consulting)\s*\w*)',
        re.IGNORECASE
    )

    def _norm_party(raw):
        raw = raw.lower().strip("'s")
        if raw == 'plaintiff':
            return 'plaintiff'
        if raw in ('defense', 'defendant'):
            return 'defense'
        return ''

    def _get_key(name):
        for existing in roster:
            if name.lower() in existing.lower() or existing.lower().endswith(name.lower()):
                return existing
        return name

    for m in called_re.finditer(all_text):
        party = _norm_party(m.group(1))
        name = m.group(2)
        if not all_text[m.start(2)].isupper():
            continue
        key = _get_key(name)
        if key not in roster:
            roster[key] = {'party': '', 'role': ''}
        if party:
            roster[key]['party'] = party

    for m in role_before_name_re.finditer(all_text):
        party = _norm_party(m.group(1))
        role_raw = m.group(2).strip().lower()
        name = m.group(3)
        if not all_text[m.start(3)].isupper():
            continue
        key = _get_key(name)
        if key not in roster:
            roster[key] = {'party': '', 'role': ''}
        if party:
            roster[key]['party'] = party
        if role_raw and not roster[key]['role']:
            roster[key]['role'] = role_raw

    for m in name_then_role_re.finditer(all_text):
        name = m.group(1)
        party = _norm_party(m.group(2))
        role_raw = m.group(3).strip().lower()
        key = _get_key(name)
        if key not in roster:
            roster[key] = {'party': '', 'role': ''}
        if party:
            roster[key]['party'] = party
        if role_raw and not roster[key]['role']:
            roster[key]['role'] = role_raw

    return roster


def build_witness_constraint(witness_map: dict) -> str:
    """Build a witness constraint block for injection into drafting prompts.

    Args:
        witness_map: Dict with 'entries' list from extract_witness_map()

    Returns:
        Constraint text block or empty string if no witness data
    """
    entries = witness_map.get('entries', [])
    if not entries:
        return ''

    has_party = any(e.get('party') for e in entries)

    if has_party:
        plaintiff_witnesses = [e for e in entries if e.get('party', '').lower() in ('plaintiff',)]
        defense_witnesses = [e for e in entries if e.get('party', '').lower() in ('defendant', 'defense')]
        other_witnesses = [e for e in entries if not e.get('party')]

        lines = ['=== WITNESS ROSTER (MANDATORY \u2014 DO NOT VIOLATE) ===']

        if plaintiff_witnesses:
            lines.append('\nPLAINTIFF\'S WITNESSES:')
            for e in plaintiff_witnesses:
                role = f" ({e['role']})" if e.get('role') else ''
                lines.append(f"- {e['witness']}{role}: {e['exam_type']} pp.{e['start_page']}-{e['end_page']}")

        if defense_witnesses:
            lines.append('\nDEFENSE WITNESSES:')
            for e in defense_witnesses:
                role = f" ({e['role']})" if e.get('role') else ''
                lines.append(f"- {e['witness']}{role}: {e['exam_type']} pp.{e['start_page']}-{e['end_page']}")

        if other_witnesses:
            lines.append('\nOTHER WITNESSES:')
            for e in other_witnesses:
                role = f" ({e['role']})" if e.get('role') else ''
                lines.append(f"- {e['witness']}{role}: {e['exam_type']} pp.{e['start_page']}-{e['end_page']}")

        lines.append('\nRULES:')
        lines.append('1. When citing a page, attribute to the witness listed for that page range')
        lines.append('2. Defense expert testimony about plaintiff\'s doctors is NOT the same as those doctors admitting something')
        lines.append('3. "Dr. [DefenseExpert] testified that Dr. [PlaintiffDoc] failed to..." is CORRECT framing')
        lines.append('4. "Dr. [PlaintiffDoc] acknowledged/admitted/conceded..." with a page in the defense expert\'s range is WRONG')
        lines.append('5. NEVER frame defense expert opinions as plaintiff\'s doctor\'s admissions')
        lines.append('=== END WITNESS ROSTER ===')
    else:
        lines = ['=== WITNESS MAP (MANDATORY \u2014 DO NOT VIOLATE) ===']
        lines.append('When citing transcript testimony, attribute to the correct witness by page:')
        for e in entries:
            lines.append(f"- Pages {e['start_page']}-{e['end_page']}: {e['witness']} ({e['exam_type']})")
        lines.append('NEVER attribute testimony from one witness\'s page range to a different witness.')
        lines.append('=== END WITNESS MAP ===')

    return '\n'.join(lines)


def verify_attribution_framing(draft_text: str, witness_map: dict) -> str:
    """Detect defense expert testimony misattributed as plaintiff admission.

    Scans for patterns like "Dr. Jones acknowledged..." citing a page in a
    defense expert's range, and inserts [FRAMING ERROR] flags.

    Args:
        draft_text: The draft brief text
        witness_map: Dict with 'entries' list

    Returns:
        Draft text with [FRAMING ERROR] flags inserted where needed
    """
    entries = witness_map.get('entries', [])
    if not entries:
        return draft_text

    # Build page -> witness/party lookup
    page_lookup = {}
    for e in entries:
        for pg in range(e['start_page'], e['end_page'] + 1):
            page_lookup[pg] = {
                'witness': e['witness'],
                'party': e.get('party', ''),
                'role': e.get('role', ''),
                'exam_type': e['exam_type'],
            }

    # Build name -> party map
    name_party = {}
    for e in entries:
        last_name = e['witness'].split()[-1].lower()
        party = e.get('party', '').lower()
        if party in ('plaintiff',):
            name_party[last_name] = 'plaintiff'
        elif party in ('defendant', 'defense'):
            name_party[last_name] = 'defense'

    # Pattern: "Dr. Name acknowledged/admitted/conceded... (Tr. at PAGE)" or "(PAGE)"
    admission_pattern = re.compile(
        r'(?:Dr\.|Mr\.|Ms\.|Mrs\.)\s+([A-Z][a-z]+)'
        r'\s+(?:acknowledged|admitted|conceded|agreed|confirmed|accepted)'
        r'[^(]*'
        r'\((?:Tr\.?\s*(?:at\s+)?)?(\d+)',
        re.IGNORECASE
    )

    result = draft_text
    offset = 0

    for m in admission_pattern.finditer(draft_text):
        name = m.group(1).lower()
        page = int(m.group(2))

        named_party = name_party.get(name, '')
        page_info = page_lookup.get(page)

        if not page_info or not named_party:
            continue

        page_party = page_info['party'].lower()
        page_witness = page_info['witness']

        # Red flag: named witness is plaintiff-side but page is defense testimony
        if named_party == 'plaintiff' and page_party in ('defendant', 'defense'):
            flag = (
                f" [FRAMING ERROR: Page {page} is {page_witness} "
                f"({page_info.get('party', 'defense')}) testimony, not "
                f"{m.group(1)} admitting. Rewrite as: '{page_witness} testified "
                f"that {m.group(1)} failed to...']"
            )
            insert_pos = m.end() + offset
            close = result.find(')', insert_pos - len(m.group(0)) + m.group(0).rfind('('))
            if close != -1:
                insert_pos = close + 1 + offset
            result = result[:insert_pos] + flag + result[insert_pos:]
            offset += len(flag)

    return result


# --- Internal helpers ---

def _normalize_party(raw: str) -> str:
    raw = raw.strip().lower()
    if raw == 'plaintiff':
        return 'Plaintiff'
    if raw in ('defendant', 'defense'):
        return 'Defendant'
    return ''


def _normalize_exam_type(raw: str) -> str:
    raw = raw.replace('-', '').upper()
    exam_map = {
        'DIRECT': 'Direct', 'CROSS': 'Cross',
        'REDIRECT': 'Redirect', 'RECROSS': 'Recross',
    }
    return exam_map.get(raw, raw.title())


def _collapse_spaced_name(spaced: str) -> str:
    parts = spaced.split()
    if not parts:
        return ''
    single_chars = sum(1 for p in parts if len(p) == 1)
    if single_chars < len(parts) * 0.7:
        return ''
    words = re.split(r'\s{2,}', spaced)
    result = []
    for word in words:
        letters = [c for c in word if c.isalpha()]
        if letters:
            result.append(''.join(letters).capitalize())
    return ' '.join(result)


def _normalize_witness_name(raw: str) -> str:
    raw = raw.strip()
    title_map = {'DR.': 'Dr.', 'MR.': 'Mr.', 'MS.': 'Ms.', 'MRS.': 'Mrs.'}
    parts = raw.split()
    result = []
    for p in parts:
        upper = p.upper().rstrip(',')
        if upper in title_map:
            result.append(title_map[upper])
        else:
            result.append(p.capitalize())
    return ' '.join(result)
