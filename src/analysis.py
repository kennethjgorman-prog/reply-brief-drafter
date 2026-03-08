"""
BriefDrafter analysis: dispatch functions for argument analysis by brief type.
"""

import json

from src.config import MAX_TOTAL_CHARS
from src.text_processing import _strip_opposing_brief_chrome, _fit_documents
from src.claude_client import call_claude
from src.document_gathering import _gather_legal_research, _gather_record_volumes, _gather_respondent_briefs


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


def _analyze_for_appellant(docs, case_law_issues=None):
    """Analyze lower court decision for appealable errors"""
    decision_text = docs.get('lower_court_decision', {}).get('text', '')
    transcript_text = docs.get('trial_transcript', {}).get('text', '')
    appendix_text = docs.get('appellant_appendix', {}).get('text', '')
    research_text = _gather_legal_research(docs, case_law_issues)
    record_combined = _gather_record_volumes(docs)

    # Fit documents within token budget
    fitted = _fit_documents([
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('TRIAL TRANSCRIPT', transcript_text, 'secondary'),
        ('APPELLANT\'S APPENDIX', appendix_text, 'secondary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ], max_total=MAX_TOTAL_CHARS)

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


def _analyze_for_respondent(docs, case_law_issues=None):
    """Analyze appellant's brief for weaknesses to defend the lower court decision"""
    appellant_text = _strip_opposing_brief_chrome(docs.get('appellant_brief', {}).get('text', ''))
    decision_text = docs.get('lower_court_decision', {}).get('text', '')
    appendix_text = docs.get('respondent_appendix', {}).get('text', '')
    research_text = _gather_legal_research(docs, case_law_issues)
    record_combined = _gather_record_volumes(docs)

    # Fit documents within token budget
    fitted = _fit_documents([
        ('APPELLANT\'S OPENING BRIEF', appellant_text, 'primary'),
        ('LOWER COURT DECISION', decision_text, 'primary'),
        ('RESPONDENT\'S APPENDIX', appendix_text, 'secondary'),
        ('RECORD ON APPEAL', record_combined, 'primary'),
        ('LEGAL RESEARCH', research_text, 'secondary'),
    ], max_total=MAX_TOTAL_CHARS)

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
    ], max_total=MAX_TOTAL_CHARS)
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
