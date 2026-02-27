"""
Quality Control Reporter for BriefDrafter
Adapted from Transcript Summarizer enhanced_qc.py
Provides citation coverage, repetition analysis, and style compliance metrics.
"""

import re
from typing import Dict, List


class AntiRepetitionEngine:
    """Detects and scores repetitive sentence patterns."""

    def __init__(self, max_consecutive_patterns: int = 3):
        self.max_consecutive = max_consecutive_patterns

    def check_repetition(self, text: str) -> Dict:
        sentences = [s.strip() for s in re.split(r'\.\s+', text) if s.strip() and len(s.strip()) > 15]
        patterns = []
        repetitions = []

        for sentence in sentences:
            pattern = self._extract_pattern(sentence)
            patterns.append(pattern)

            if len(patterns) >= self.max_consecutive:
                recent = patterns[-self.max_consecutive:]
                if len(set(recent)) == 1:
                    repetitions.append({
                        'position': len(patterns),
                        'pattern': recent[0],
                        'example': sentence[:60] + '...' if len(sentence) > 60 else sentence
                    })

        unique = len(set(patterns))
        variety = unique / len(patterns) if patterns else 0

        return {
            'repetitions': repetitions,
            'variety_score': variety,
            'unique_patterns': unique,
            'total_sentences': len(sentences),
            'has_violations': len(repetitions) > 0
        }

    def _extract_pattern(self, sentence: str) -> str:
        clean = re.sub(r'\([^)]+\)', '', sentence).strip()
        clean = re.sub(r'_[^_]+_', '', clean).strip()
        words = clean.split()
        if not words:
            return 'empty'

        first = words[0].lower()
        second = words[1].lower() if len(words) > 1 else ''

        if first in ('he', 'she', 'it', 'they', 'plaintiff', 'defendant'):
            if second in ('testified', 'stated', 'confirmed', 'explained', 'argued', 'contended'):
                return 'subject_testimony'
            return 'subject_action'
        elif first in ('the', 'a', 'an', 'this', 'that'):
            if second in ('court', 'record', 'evidence', 'testimony'):
                return 'article_legal'
            return 'article_subject'
        elif first in ('moreover', 'furthermore', 'indeed', 'however', 'additionally'):
            return 'transition_start'
        elif first in ('here', 'in', 'on', 'at', 'under'):
            return 'prepositional_start'
        elif first[0].isupper() and len(first) > 2:
            return 'proper_noun'
        else:
            return 'other'


class BriefQC:
    """Quality control for appellate brief drafts."""

    def __init__(self):
        self.repetition_engine = AntiRepetitionEngine()
        self.ai_isms = [
            'it is important to note',
            'first and foremost',
            'it bears noting',
            'it should be noted',
            'it is worth noting',
            'it is crucial to',
            'it is essential to',
            'in conclusion',
            'to summarize',
        ]
        self.prohibited_patterns = [
            r'\u2014',  # em dash
            r'##\s',    # markdown heading
            r'\*\*',    # markdown bold
        ]

    def run_qc(self, draft: str) -> Dict:
        """Run all QC checks on a brief draft."""
        paragraphs = [p.strip() for p in draft.split('\n\n') if p.strip()]

        citation_metrics = self._check_citations(paragraphs)
        repetition_metrics = self.repetition_engine.check_repetition(draft)
        style_metrics = self._check_style(draft)

        violations = []
        warnings = []

        # Citation violations
        if citation_metrics['coverage_pct'] < 80:
            violations.append(
                f"CITATION_COVERAGE: {citation_metrics['coverage_pct']:.0f}% "
                f"({citation_metrics['paras_without_cites']} paragraphs missing citations)"
            )
        elif citation_metrics['coverage_pct'] < 95:
            warnings.append(
                f"CITATION_COVERAGE: {citation_metrics['coverage_pct']:.0f}% "
                f"(consider adding citations to remaining paragraphs)"
            )

        # Repetition violations
        if repetition_metrics['has_violations']:
            violations.append(
                f"REPETITION: {len(repetition_metrics['repetitions'])} consecutive-pattern violations"
            )
        if repetition_metrics['variety_score'] < 0.4:
            warnings.append(
                f"LOW_VARIETY: Sentence variety score {repetition_metrics['variety_score']:.0%}"
            )

        # Style violations
        for issue in style_metrics['violations']:
            violations.append(f"STYLE: {issue}")
        for issue in style_metrics['warnings']:
            warnings.append(f"STYLE: {issue}")

        overall_pass = len(violations) == 0

        return {
            'overall_pass': overall_pass,
            'citation_coverage': citation_metrics,
            'repetition_analysis': repetition_metrics,
            'style_compliance': style_metrics,
            'violations': violations,
            'warnings': warnings,
        }

    def _check_citations(self, paragraphs: List[str]) -> Dict:
        """Check citation coverage across factual paragraphs."""
        # Citation patterns for appellate briefs
        cite_patterns = [
            r'\(\d[\d\-\u2013, ]*\)',         # bare record: (125), (125-130)
            r'\(Tr\.\s*(?:at\s+)?\d+',        # transcript: (Tr. at 125:14)
            r'_[A-Z][^_]+v\.\s+[^_]+_',       # case name: _Smith v. Jones_
            r'\d+\s+AD[23]d\s+\d+',           # reporter: 123 AD3d 456
            r'\d+\s+NY[23]d\s+\d+',           # reporter: 123 NY3d 456
            r'\[CITE NEEDED\]',                # flagged
            r'\[FULL CITE NEEDED\]',           # flagged
            r'\[CASE CITE NEEDED\]',           # flagged
        ]

        # Skip non-factual paragraphs
        skip_patterns = [
            r'^POINT\s+[IVX]+',
            r'^PRELIMINARY\s+STATEMENT',
            r'^STATEMENT\s+OF',
            r'^COUNTERSTATEMENT',
            r'^CONCLUSION',
            r'^STANDARD\s+OF\s+REVIEW',
            r'^Respectfully\s+submitted',
            r'^Dated:',
            r'^\t[A-Z]\.\t',  # sub-headings
        ]

        total_factual = 0
        with_cites = 0
        without_cites = []

        for para in paragraphs:
            if len(para) < 50:
                continue
            if any(re.match(p, para, re.IGNORECASE) for p in skip_patterns):
                continue

            total_factual += 1
            has_cite = any(re.search(p, para) for p in cite_patterns)
            if has_cite:
                with_cites += 1
            else:
                without_cites.append(para[:80] + '...' if len(para) > 80 else para)

        coverage = (with_cites / total_factual * 100) if total_factual > 0 else 100

        return {
            'total_factual_paragraphs': total_factual,
            'paragraphs_with_cites': with_cites,
            'paras_without_cites': len(without_cites),
            'coverage_pct': coverage,
            'missing': without_cites[:5],  # show first 5
        }

    def _check_style(self, draft: str) -> Dict:
        """Check for style violations (em dashes, AI-isms, markdown)."""
        violations = []
        warnings = []

        # Em dashes
        em_dash_count = draft.count('\u2014')
        if em_dash_count > 0:
            violations.append(f"Em dashes found ({em_dash_count}x) -- replace with commas")

        # Markdown
        md_headings = len(re.findall(r'^#{1,3}\s', draft, re.MULTILINE))
        if md_headings > 0:
            violations.append(f"Markdown headings found ({md_headings}x)")

        md_bold = len(re.findall(r'\*\*[^*]+\*\*', draft))
        if md_bold > 0:
            violations.append(f"Markdown bold found ({md_bold}x)")

        # AI-isms
        ai_count = 0
        for phrase in self.ai_isms:
            count = len(re.findall(re.escape(phrase), draft, re.IGNORECASE))
            if count > 0:
                ai_count += count
                warnings.append(f'AI-ism "{phrase}" found ({count}x)')

        # Bullet points
        bullets = len(re.findall(r'^\s*[-*]\s', draft, re.MULTILINE))
        if bullets > 2:
            warnings.append(f"Bullet points detected ({bullets}x) -- briefs use prose paragraphs")

        return {
            'violations': violations,
            'warnings': warnings,
            'em_dash_count': em_dash_count,
            'ai_ism_count': ai_count,
            'markdown_count': md_headings + md_bold,
        }


def generate_qc_report(qc_results: Dict) -> str:
    """Generate a human-readable QC report string."""
    lines = []
    lines.append("=" * 60)
    lines.append("BRIEF QUALITY CONTROL REPORT")
    lines.append("=" * 60)

    status = "PASS" if qc_results['overall_pass'] else "NEEDS REVIEW"
    lines.append(f"Overall: {status}")
    lines.append("")

    # Citation Coverage
    cc = qc_results['citation_coverage']
    lines.append(f"Citation Coverage: {cc['coverage_pct']:.0f}%")
    lines.append(f"  Factual paragraphs: {cc['total_factual_paragraphs']}")
    lines.append(f"  With citations: {cc['paragraphs_with_cites']}")
    if cc['paras_without_cites'] > 0:
        lines.append(f"  Missing citations: {cc['paras_without_cites']}")

    # Repetition
    ra = qc_results['repetition_analysis']
    lines.append(f"Sentence Variety: {ra['variety_score']:.0%}")
    lines.append(f"  Unique patterns: {ra['unique_patterns']}/{ra['total_sentences']}")
    if ra['has_violations']:
        lines.append(f"  Consecutive-pattern violations: {len(ra['repetitions'])}")

    # Style
    sc = qc_results['style_compliance']
    if sc['em_dash_count'] > 0:
        lines.append(f"Em Dashes: {sc['em_dash_count']} (should be 0)")
    if sc['ai_ism_count'] > 0:
        lines.append(f"AI-isms: {sc['ai_ism_count']} detected")

    # Violations
    if qc_results['violations']:
        lines.append("")
        lines.append("VIOLATIONS:")
        for v in qc_results['violations']:
            lines.append(f"  * {v}")

    # Warnings
    if qc_results['warnings']:
        lines.append("")
        lines.append("WARNINGS:")
        for w in qc_results['warnings']:
            lines.append(f"  - {w}")

    lines.append("=" * 60)
    return "\n".join(lines)
