"""
Two-Pass Transcript Processor

Pass 1 (Extraction): Chunk-by-chunk high-recall fact extraction with is_admission flags.
                      Outputs structured JSON per chunk.
Merge:               Combines all chunk JSONs into a single master fact list, sorted by page.
Pass 2 (Drafting):   Converts the master fact list into flowing narrative prose suitable
                     for a Statement of Facts in an appellate brief or motion.

Citation format is controlled by citation_config, hardcoded per app type.
"""

import json
import os
import re
from typing import List, Dict, Optional, Callable
from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Citation templates — one per app context
# ---------------------------------------------------------------------------

CITATION_CONFIGS = {
    # Appellate — record on appeal: bare page number (325)
    'appellate_record': {
        'template': '({page})',
        'instruction': (
            'Use this EXACT citation format for every factual statement: (page_number)\n'
            'Example: "The ceiling collapsed on July 23, 2019 (403)."\n'
            'Use bare page numbers in parentheses. NO "R." prefix. NO "p." prefix.\n'
            'For page ranges: (403-404).'
        ),
    },
    # Appellate — appendix method: (A. 325)
    'appellate_appendix': {
        'template': '(A. {page})',
        'instruction': (
            'Use this EXACT citation format: (A. page_number)\n'
            'Example: "The ceiling collapsed on July 23, 2019 (A. 403)."\n'
            'The "A." prefix indicates appendix method. For ranges: (A. 403-404).'
        ),
    },
    # Motion — EBT deposition: (Smith's EBT at 45) or (Smith's EBT at 45:1-3)
    'motion_ebt': {
        'template': "({deponent_name}'s EBT at {page})",
        'template_with_lines': "({deponent_name}'s EBT at {page}:{lines})",
        'id_template': '(id., at {page})',
        'instruction': (
            'Use this EXACT citation format:\n'
            '- First reference: ({deponent_name}\'s EBT at page) e.g. (Smith\'s EBT at 45)\n'
            '- With line numbers: ({deponent_name}\'s EBT at page:lines) e.g. (Smith\'s EBT at 45:1-3)\n'
            '- Subsequent references to same deponent in same paragraph: (id., at page)\n'
            '- When switching deponents, use full citation again.\n'
        ),
    },
    # Motion — trial transcript, non-consecutive pagination
    'motion_trial_nonconsecutive': {
        'template': '(Tr. {date} at p. {page}:{lines})',
        'id_template': '(id., at p. {page}:{lines})',
        'instruction': (
            'Use this EXACT citation format for trial transcripts:\n'
            '- Full: (Tr. date at p. page:lines) e.g. (Tr. 9/3/26 at p. 35:3-23)\n'
            '- Subsequent same source: (id., at p. page:lines)\n'
        ),
    },
    # Motion — trial transcript, consecutive pagination
    'motion_trial_consecutive': {
        'template': '({page})',
        'alt_template': '(Tr. at {page})',
        'instruction': (
            'Use this EXACT citation format for trial transcripts:\n'
            '- (page_number) e.g. (345) OR (Tr. at page) e.g. (Tr. at 345)\n'
            '- For ranges: (345-347) or (Tr. at 345-347)\n'
        ),
    },
}


class TwoPassProcessor:
    """Processes transcripts using a two-pass extraction + drafting pipeline."""

    def __init__(self, api_key: Optional[str] = None, model: str = 'sonnet'):
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        self.client = Anthropic(api_key=self.api_key)
        self.models = {
            'sonnet': 'claude-sonnet-4-20250514',
            'opus': 'claude-opus-4-20250514',
        }
        self.model = self.models.get(model, self.models['sonnet'])

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_transcript(
        self,
        pages: List[tuple],
        focus_areas: str,
        citation_config_name: str = 'appellate_record',
        deponent_name: str = '',
        chunk_size: int = 10,
        progress_callback: Optional[Callable] = None,
    ) -> Dict:
        """
        Full pipeline: chunk -> extract -> merge -> draft.

        Args:
            pages: List of (page_number, text) tuples from parse_pdf_pages().
            focus_areas: Legal issues to focus extraction on.
            citation_config_name: Key into CITATION_CONFIGS.
            deponent_name: Name for EBT citation templates.
            chunk_size: Number of transcript pages per chunk.
            progress_callback: Optional fn(stage, current, total, message) for UI updates.

        Returns:
            Dict with keys: 'narrative', 'facts', 'fact_count', 'word_count'
        """
        citation_config = CITATION_CONFIGS.get(citation_config_name, CITATION_CONFIGS['appellate_record'])

        # Inject deponent name into instruction if needed
        if deponent_name and '{deponent_name}' in citation_config.get('instruction', ''):
            citation_config = dict(citation_config)
            citation_config['instruction'] = citation_config['instruction'].replace(
                '{deponent_name}', deponent_name
            )

        # --- Chunk ---
        chunks = self._make_chunks(pages, chunk_size)
        total_chunks = len(chunks)

        if progress_callback:
            progress_callback('extraction', 0, total_chunks, 'Starting extraction...')

        # --- Pass 1: Extract ---
        all_facts = []
        for i, chunk in enumerate(chunks):
            if progress_callback:
                progress_callback('extraction', i + 1, total_chunks,
                                  f'Extracting facts from pages {chunk["range"]}...')

            facts = self._extract_chunk(chunk, focus_areas)
            all_facts.extend(facts)

        # --- Merge & deduplicate ---
        all_facts = self._merge_facts(all_facts)

        if progress_callback:
            progress_callback('drafting', 0, 1,
                              f'Drafting narrative from {len(all_facts)} extracted facts...')

        # --- Pass 2: Draft ---
        narrative = self._draft_narrative(all_facts, citation_config, deponent_name)

        if progress_callback:
            progress_callback('complete', 1, 1, 'Done.')

        return {
            'narrative': narrative,
            'facts': all_facts,
            'fact_count': len(all_facts),
            'word_count': len(narrative.split()),
        }

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _make_chunks(self, pages: List[tuple], chunk_size: int) -> List[Dict]:
        """Group pages into chunks of chunk_size."""
        chunks = []
        for i in range(0, len(pages), chunk_size):
            group = pages[i:i + chunk_size]
            text = "\n\n".join(
                f"[Transcript Page {pg}]\n{txt}" for pg, txt in group
            )
            page_range = f"{group[0][0]}-{group[-1][0]}"
            chunks.append({'text': text, 'range': page_range})
        return chunks

    # ------------------------------------------------------------------
    # Pass 1: Extraction
    # ------------------------------------------------------------------

    EXTRACTION_SYSTEM = """You are a Legal Fact Extraction Engine. Your ONLY job is to extract specific facts and direct quotes from the provided transcript chunk that relate to the user's Focus Areas.

RULES:
1. OUTPUT FORMAT: Output a single valid JSON array of objects. Each object:
   {"page": <number>, "topic": "<focus_area>", "fact": "<brief description>", "quote": "<exact Q&A text>", "is_admission": <true|false>}
2. NO SUMMARIZATION: Do not summarize. Copy the exact fact and quote from the transcript.
3. HIGH RECALL: If a fact is even remotely related to a Focus Area, extract it. Better to have too much than too little.
4. CONTEXT AWARENESS: If a witness says "Yes" or "No" to a question, extract the question AND the answer so the context is preserved.
5. STRICT CITATION: Extract the exact transcript page number from the [Transcript Page XXX] markers.
6. ADMISSION DETECTION: Set "is_admission" to true when the testimony:
   - Admits a fact harmful to the witness's or their party's position
   - Acknowledges control, knowledge, or responsibility for the premises or condition
   - Confirms facts that support the OPPOSING party's legal theory (e.g., confirming that only the defendants had access/control supports plaintiff's argument that defendants were not truly out-of-possession)
   - Contains a direct quote that would be powerful evidence against the witness's side
   - Shows the witness refused to investigate, inspect, review documents, or act
   - Is a statement against interest
   - Shows knowledge of a dangerous condition
   - Acknowledges that no repairs, records, or inspections were done
   - Even hedged answers like "not that I know of" or "I can't testify to that" count as admissions when they confirm the opposing party's factual narrative
7. Output ONLY the JSON array. No preamble, no markdown fences, no explanation. Start with [ and end with ]."""

    def _extract_chunk(self, chunk: Dict, focus_areas: str) -> List[Dict]:
        """Run Pass 1 extraction on a single chunk."""
        user_prompt = f"""FOCUS AREAS:
{focus_areas}

TRANSCRIPT CHUNK (Pages {chunk['range']}):
{chunk['text']}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=self.EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )

        result_text = response.content[0].text.strip()

        # Strip markdown fences if present
        if result_text.startswith('```'):
            result_text = re.sub(r'^```\w*\n?', '', result_text)
            result_text = re.sub(r'\n?```$', '', result_text)

        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            # Try to find a JSON array in the output
            match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return []

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _merge_facts(self, facts: List[Dict]) -> List[Dict]:
        """Sort by page and remove near-duplicate facts."""
        # Sort by page number
        facts.sort(key=lambda f: int(f.get('page', 0)) if str(f.get('page', '')).isdigit() else 0)

        # Deduplicate: same page + very similar fact text
        seen = set()
        unique = []
        for fact in facts:
            key = (fact.get('page', 0), fact.get('fact', '')[:60].lower())
            if key not in seen:
                seen.add(key)
                unique.append(fact)

        return unique

    # ------------------------------------------------------------------
    # Pass 2: Narrative Drafting
    # ------------------------------------------------------------------

    def _draft_narrative(
        self,
        facts: List[Dict],
        citation_config: Dict,
        deponent_name: str = '',
    ) -> str:
        """Run Pass 2: convert extracted facts into narrative prose."""

        citation_instruction = citation_config.get('instruction', '')

        system_prompt = f"""You are an expert Appellate Brief Drafter. Your task is to write a "Statement of Facts" based *exclusively* on the provided database of extracted facts.

INPUT DATA:
A JSON list of verified facts and quotes from the deposition of {deponent_name or 'the witness'}.

OUTPUT GOAL:
A chronological, flowing narrative (Statement of Facts) suitable for an appellate brief or motion.

STRICT RULES:
1. NARRATIVE FLOW: Do NOT write a list or a report. Write a story. Group related facts by topic (e.g., "The Lease Agreement," "The Leaking Ceiling," "The Incident").

2. PROSE STYLE:
   - Do NOT repeat sentence structures. Vary openings and length.
   - NEVER start more than two consecutive sentences with the same word.
   - NEVER use "He testified that..." or "She stated that..." more than once in the entire output. Instead, present facts directly: "The roof leaked whenever it rained (377)."
   - Mix short declarative sentences with longer compound ones.
   - Use the active voice.
   - Write in past tense for events that occurred.

3. CITATION FORMAT:
   {citation_instruction}
   Every sentence containing a factual claim MUST have a citation.

4. ADMISSIONS — MANDATORY VERBATIM QUOTES:
   IF a fact in the JSON has "is_admission": true, you MUST:
   - Include the direct quote from the "quote" field VERBATIM in quotation marks
   - Present the full Q&A exchange or the key answer in quotes
   - NEVER paraphrase an admission. The exact words are legally significant.
   Example: When asked whether anyone else had control of the roof, Manfredi testified: "Not that I know of. I can't testify to that" (403).

5. ZERO HALLUCINATION: Use ONLY the facts in the JSON. Do not infer or add.

6. LENGTH: Write at least 400 words. Be thorough — do NOT drop extracted facts to save space. Every fact in the JSON should appear in the narrative. It is far worse to omit a relevant fact than to write a longer narrative.

7. IRRELEVANT MATERIAL: Do NOT include biographical details (education, criminal history, personal address) unless directly relevant to liability or credibility in the case.

8. COMPLETENESS CHECK: Before finishing, verify that EVERY fact from the JSON input appears in your narrative. If a fact about control, knowledge, repairs, inspections, or the incident is missing, you MUST add it.

FORMATTING:
- Use <h3> headers for major topic sections.
- Start directly with the narrative. No intro ("Here is the summary...") or outro.
- Do not use bullet points, numbered lists, or tables."""

        facts_json = json.dumps(facts, indent=2)

        user_prompt = f"""Write the Statement of Facts for {deponent_name + "'s" if deponent_name else 'the witness'} deposition testimony based on these extracted facts:

{facts_json}"""

        # Use streaming for potentially long responses
        with self.client.messages.stream(
            model=self.model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            narrative = stream.get_final_text()

        return narrative
