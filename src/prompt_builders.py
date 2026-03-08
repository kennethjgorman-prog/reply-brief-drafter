"""
BriefDrafter prompt builders: protocol, style, exemplars, structure prompts.
"""

import re
from src.config import _load_protocol


def _build_drafting_protocol():
    """Shared anti-hallucination, citation, and formatting protocol for all brief types.
    Adapted from Universal Litigation Transcript Protocol v9.2 Enhanced Guardrails."""
    return """
================================================================================
DRAFTING PROTOCOL WITH ENHANCED ANTI-HALLUCINATION GUARDRAILS
================================================================================

YOU ARE BEING USED IN A LEGAL CONTEXT WHERE ACCURACY IS CRITICAL.
  - Hallucinated facts could mislead a court
  - Incorrect citations waste attorney time
  - Fabricated testimony or case law violates ethical rules
  - Your output WILL be reviewed by the attorney

================================================================================
RULE 1: SOURCE-FIRST WORKFLOW (MANDATORY)
================================================================================

You CANNOT write a sentence without FIRST finding the source in the uploaded documents.

  OLD (WRONG) WORKFLOW: Think of a fact \u2192 write sentence \u2192 try to find citation
  NEW (REQUIRED) WORKFLOW: Find fact in document \u2192 note page number \u2192 THEN write sentence with citation

For EVERY factual sentence, you MUST:
  1. FIND the specific fact in the RECORD ON APPEAL or other evidentiary document
  2. NOTE the page number where you found it (printed at top center of each page)
  3. WRITE the sentence based on what you actually found
  4. CITE the page at the end: (page). or (page-page).

If you cannot find a fact in the documents, DO NOT WRITE IT. Write [CITE NEEDED] instead.

================================================================================
RULE 2: RECORD CITATIONS \u2014 EVERY FACTUAL SENTENCE (NON-NEGOTIABLE)
================================================================================

EVERY SENTENCE that states a fact from the record MUST end with a citation.

  FORMAT: ([page]). \u2014 Period goes AFTER the closing parenthesis
  NO prefixes: No "R." or "A." or "p." \u2014 just the bare page number
  EXAMPLE: "The plaintiff fell on the stairs" (125).
  EXAMPLE: "The court dismissed the case" (91).

  CRITICAL \u2014 USE RECORD PAGE NUMBERS, NOT TRANSCRIPT PAGE NUMBERS:
  - The record on appeal has its own continuous pagination (the number after "--- PAGE X ---")
  - Deposition transcripts embedded in the record have INTERNAL page numbers ("Page 47" etc.)
  - You MUST cite the RECORD page number, NOT the internal transcript page number
  - WRONG: Testimony at deposition page 47, cited as (47)
  - RIGHT: Same testimony found at record page 135, cited as (135)
  - Match the page numbering used in the OPENING BRIEF \u2014 those are the correct record pages

  REQUIREMENTS:
  - EVERY factual sentence needs its own cite
  - The LAST sentence of every paragraph MUST have a citation
  - NO facts may be stated without a citation to Record, Appendix, or RA
  - If you cannot find support, write "[CITE NEEDED]"

  IF YOUR OUTPUT HAS FACTUAL SENTENCES WITHOUT CITATIONS, IT HAS FAILED.

================================================================================
RULE 3: CASE LAW CITATIONS \u2014 ZERO TOLERANCE FOR FABRICATION
================================================================================

*** YOU ARE FORBIDDEN FROM INVENTING CASE NAMES ***

YOUR ONLY SOURCES FOR CASE LAW ARE:
  a) Cases cited in any uploaded brief (opening brief, respondent's brief)
  b) Cases in the uploaded Legal Research document
  c) Cases cited in the lower court decision

THAT'S IT. NO OTHER SOURCES. PERIOD.

BEFORE YOU WRITE ANY CASE CITATION, ASK YOURSELF:
  "Did I see this exact case name in one of the uploaded documents?"
  If NO \u2192 DO NOT CITE IT. Write "[CASE CITE NEEDED]" instead.

YOU MUST NOT:
  - Cite ANY case from your training data or general knowledge
  - Cite ANY case you "remember" but cannot find in the uploaded documents
  - Invent a case name that "sounds right"
  - Fabricate holdings for real cases
  - Cite cases from your training data \u2014 your training data is OFF LIMITS

WHEN CITING A CASE:
  - Find the FULL citation string in the uploaded document and COPY IT
  - WRONG: _Smith v. Jones_ held that... (missing the full citation)
  - RIGHT: _Smith v. Jones_, 123 AD3d 456 [2d Dept 2020] held that...
  - If you cannot find the full citation, write [FULL CITE NEEDED]

CITATION FORMAT:
  - NEW YORK OFFICIAL FORMAT: 123 AD3d 456 [2d Dept 2020]
  - Case names use UNDERSCORES: _Case Name v. Other Party_
  - Court and year in SQUARE BRACKETS [ ], NEVER parentheses ( )
  - NO PERIODS in reporters: AD3d NOT A.D.3d, NY2d NOT N.Y.2d, NYS2d NOT N.Y.S.2d

================================================================================
RULE 4: ZERO INFERENCE POLICY
================================================================================

You may ONLY state facts that are EXPLICITLY in the uploaded documents.

PROHIBITED:
  - Emotional states not explicitly stated
  - Motivations or intentions not testified to
  - Causal relationships not explicitly stated
  - Credibility assessments
  - Logical conclusions, even if "obvious"
  - Negative inferences (absence of something not stated)

EXAMPLES OF PROHIBITED INFERENCES:
  \u2717 "He was not hospitalized" \u2192 \u2713 "He was treated in the ED and discharged"
  \u2717 "She was able to walk" \u2192 \u2713 "She exited the building"
  \u2717 "The defendant knew about the condition" \u2192 \u2713 State what the record actually says

PROHIBITED: ADDING CHARACTERIZATIONS TO CASE DESCRIPTIONS
  When describing what happened in a case, use ONLY the court's words.
  Do NOT add adjectives, labels, or causal explanations the court did not use.

  \u2717 WRONG: In _Monroe_, "the bands snapped" due to a malfunction
    (The court said "the metal bands broke" \u2014 it NEVER said "malfunction."
     You FABRICATED "malfunction." That is a lie to the court.)

  \u2713 RIGHT: In _Monroe_, "one or more of the metal bands broke, causing
    the logs to come loose and plaintiff to be propelled off the trailer"
    (Uses the court's ACTUAL language.)

  \u2717 WRONG: The court found that defendant was negligent
    (Unless the court used the word "negligent" \u2014 do not characterize.)

  \u2713 RIGHT: The court held that defendant "failed to exercise reasonable care"
    (Uses the court's ACTUAL language.)

  THIS RULE APPLIES TO EVERY CASE YOU DISCUSS:
  - Do NOT summarize a case holding in your own words and put it in quotes
  - Do NOT add words like "malfunction," "defect," "negligence," "reckless,"
    "intentional," "unsafe," "dangerous" unless the court used those exact words
  - When in doubt, QUOTE the court's actual language rather than paraphrasing
  - If you are describing a case from the respondent's brief, you are reading
    the respondent's CHARACTERIZATION of the case \u2014 NOT the court's language.
    Do NOT put the respondent's characterization in quotes and cite the case.

WHEN IN DOUBT:
  - Flag with [VERIFY] and let the attorney decide
  - Quote the source directly \u2014 use the EXACT words from the document
  - It is BETTER to flag uncertainty than to fabricate

================================================================================
RULE 5: DOCUMENT SOURCE HIERARCHY
================================================================================

CATEGORY A \u2014 EVIDENTIARY SOURCES (cite as record facts):
  - Lower court decision / order
  - Trial transcript
  - Record volumes / appendix
  - Exhibits, affidavits, sworn statements from the record

CATEGORY B \u2014 ADVOCACY DOCUMENTS (NOT facts \u2014 these are spin):
  - Appellant's brief / opening brief
  - Respondent's brief / answering brief
  - Any party's memorandum of law

RULES FOR CATEGORY B:
  a) NEVER cite a record page based on what an opposing brief says is on that page.
     Go to the ACTUAL record page and verify.
  b) NEVER put quotes from an opposing brief and cite a record page as if you found it.
  c) ALWAYS attribute: "Appellant argues that..." or "Respondent contends that..."
  d) NEVER adopt the opposing party's characterizations as your own.
  e) NEVER quote the opposing brief's DESCRIPTION of a case and cite the case
     as if those were the court's words. The opposing brief is SPIN \u2014 it
     describes cases the way it wants the court to see them.

  EXAMPLE OF RULE (e) VIOLATION:
  Respondent's brief says: "In Monroe, the bands snapped causing injury."
  \u2717 WRONG: In _Monroe_, "the bands snapped" (_Monroe_ at 653).
    (You quoted RESPONDENT'S description and cited it as the court's language!)
  \u2713 RIGHT: Respondent characterizes _Monroe_ as involving bands that snapped.
    However, the _Monroe_ court actually stated that "one or more of the metal
    bands broke" (_Monroe_ at 653).
    (You attributed respondent's language to respondent, then used the court's
    actual language separately.)

================================================================================
RULE 6: FORMATTING \u2014 PLAIN TEXT ONLY (NO MARKDOWN)
================================================================================

  - NEVER use markdown: NO ## headings, NO **bold**, NO *italics*, NO # anything
  - Output PLAIN TEXT ONLY
  - Section headings: plain ALL CAPS on their own line
  - Point headings: "POINT I" on its own line, heading in ALL CAPS on next line(s)
  - Sub-headings: tab + "A." + tab + heading text
  - Body paragraphs: Start with a tab character
  - Block quotes: Indent with two tabs
  - Blank line between paragraphs and around headings
  - Case names: _underscores_ for underlining (NOT asterisks)

================================================================================
SELF-AUDIT \u2014 RUN BEFORE OUTPUTTING
================================================================================

Before submitting your draft, check EVERY paragraph:

  1. Does EVERY factual sentence end with a record citation?
     If NO \u2192 ADD the citation or mark [CITE NEEDED]

  2. Did I find EVERY fact in the actual document before writing it?
     If NO \u2192 DELETE the sentence or mark [VERIFY]

  3. Did I make ANY inferences not explicitly in the documents?
     If YES \u2192 REWRITE to state only what the document says

  4. Does EVERY case citation include the FULL cite (volume, reporter, page, [court year])?
     If NO \u2192 Find and add the full cite or mark [FULL CITE NEEDED]

  5. Did I cite ANY case from my training data instead of the uploaded documents?
     If YES \u2192 DELETE it and write [CASE CITE NEEDED]

  6. Did I use the correct section headings for this brief type?
     If appellant brief \u2192 "STATEMENT OF THE CASE" (NOT "Counterstatement")
     If respondent brief \u2192 "COUNTERSTATEMENT OF FACTS" (NOT "Statement of the Case")

  7. Is my formatting plain text with no markdown?
     If NO \u2192 Remove all markdown

IF ANY CHECK FAILS, FIX IT BEFORE OUTPUTTING.
================================================================================

"""


def _build_anti_hallucination_block():
    """Mandatory anti-hallucination rules as a standalone, prominent prompt section.
    Kept separate from drafting protocol and writing style so Claude treats it
    as a top-level mandatory constraint, not mere stylistic guidance."""
    return """
################################################################################
## MANDATORY ANTI-HALLUCINATION RULES \u2014 VIOLATION = MALPRACTICE              ##
## These rules OVERRIDE all other instructions. Never relax them.            ##
################################################################################

""" + _load_protocol('anti_hallucination.txt') + """

################################################################################
## END MANDATORY ANTI-HALLUCINATION RULES                                    ##
################################################################################
"""


def _strip_attorney_names(text):
    """Remove attorney names and their surrounding phrases from procedural history output.

    Strips patterns like:
      - "of Brian J. Isaac, Esq."  / "of Robert M. Lefland, Esq."
      - "Brian J. Isaac, Esq. argued" -> "plaintiff argued" (handled by prompt)
      - ", Esq." honorific after any name
      - "the affirmation of [Name], Esq." -> "the affirmation"
    """
    # Remove "of [Name], Esq." — catches "affirmation of Brian J. Isaac, Esq."
    text = re.sub(r'\s+of\s+[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+,\s*Esq\.', '', text)
    # Remove standalone "[Name], Esq." that may remain (e.g., at start of sentence)
    text = re.sub(r'[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+,\s*Esq\.', '', text)
    # Clean up artifacts: double spaces, "affirmation  dated" -> "affirmation dated"
    text = re.sub(r'  +', ' ', text)
    # Clean "affirmation dated" -> "affirmation" (date on non-moving papers shouldn't be there either)
    text = re.sub(r'(affirmation|affidavit)\s+dated\s+\w+\s+\d{1,2},\s*\d{4}', r'\1', text)
    return text


def _build_party_label_constraint(project):
    """Build a mandatory party-labeling instruction from project party fields."""
    representing = project.get('representing', '')
    appellant = project.get('appellant', '').strip().rstrip(',')
    respondent = project.get('respondent', '').strip().rstrip(',')
    if not representing or not appellant or not respondent:
        return ''

    if representing == 'respondent':
        our_party = respondent
        our_label = 'plaintiff'
    else:
        our_party = appellant
        our_label = 'plaintiff'

    return f"""MANDATORY \u2014 PARTY REFERENCES: Refer to {our_party} as "{our_label}" throughout. NEVER use the party's surname. Every instance of the surname in your output is a violation. Defendants and other parties may be referred to by entity name."""


def _build_writing_style():
    """Writing style guidance for fact sections and argument sections"""
    return """
WRITING STYLE - TWO MODES (USE THE CORRECT ONE FOR EACH SECTION):

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
MODE 1: FACT SECTIONS (Preliminary Statement, Statement of Facts/Case, Counterstatement of Facts)
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

Write in flowing, professional narrative prose \u2014 NOT a choppy list of facts.

12 SENTENCE PATTERNS - USE STRATEGICALLY (never same pattern 2x in a row):
1. DIRECT ATTRIBUTION: "[Subject] [verb] [fact]" (45).
2. SUBORDINATE CLAUSE: "When [context], [main clause]" (45).
3. EMBEDDED ATTRIBUTION: "[Fact], [subject] testified, [continuation]" (45).
4. TEMPORAL TRANSITION: "[Time marker] + [fact]" (45).
5. COMPOUND WITH CONTRAST: "[Fact 1], but/yet [Fact 2]" (45).
6. PARTICIPIAL PHRASE: "[Verb-ing phrase], [main clause]" (45).
7. PASSIVE VOICE (sparingly): "[Object] was [past participle]" (45).
8. DIRECT QUOTE: [Attribution], "[direct quote]" (45).
9. INVERTED ORDER: "[Important fact first], [attribution second]" (45).
10. SEQUENTIAL: "[Subject] [verb] [item 1], [item 2], and [item 3]" (45).
11. CONCESSIVE: "Although/Though [fact 1], [fact 2]" (45).
12. APPOSITIONAL: "[Subject], [descriptive phrase], [verb phrase]" (45).

ANTI-MONOTONY RULES:
- NEVER use the same pattern more than 2x in a row
- VARY sentence length: Short (8-12 words) \u2192 Medium (13-20) \u2192 Long (21-30) \u2014 create rhythm
- ROTATE attribution verbs (pool of 20, never repeat within 5 sentences):
  testified, stated, explained, confirmed, noted, indicated, acknowledged,
  described, clarified, maintained, recounted, recalled, asserted, reported,
  revealed, admitted, conceded, observed, mentioned, established
- Combine related facts into fewer, richer sentences
- Use pronouns naturally \u2014 do NOT repeat party names in every sentence

FACT STYLE EXAMPLE \u2014 BAD (monotonous):
"Appellant testified he relied on his insurance. Appellant stated he expected
Nationwide to answer. Appellant explained he was a permissive user. Appellant
said he contacted Progressive later."

FACT STYLE EXAMPLE \u2014 GOOD (flowing):
"Following service of the complaint, Ekstein relied on Nationwide Insurance
Company to interpose an answer on his behalf, believing his status as a
permissive user of the vehicle entitled him to coverage (34-35). It was not
until plaintiff moved for default judgment that he contacted his own carrier,
Progressive Insurance, which agreed to assign counsel (34-35). By that point,
however, his time to answer had long expired (5)."

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
MODE 2: ARGUMENT SECTIONS (Point I, Point II, Point III, etc.)
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

Write persuasive legal argument in sophisticated, flowing prose.

ARGUMENT STYLE RULES:
- Lead with legal conclusions, then support with authority
- Integrate case citations into flowing prose \u2014 NOT as choppy standalone sentences
- Use rhetorical contrast: "Appellant claims X, but the record shows Y"
- Build logical chains: legal standard \u2192 application of facts \u2192 conclusion
- Combine related legal points into cohesive paragraphs (3-7 sentences each)
- Vary sentence length for rhythm and emphasis
- Use same 12 sentence patterns and anti-monotony rules as fact sections

ARGUMENT STYLE EXAMPLE \u2014 BAD (choppy, list-like):
"The court had discretion. The court exercised discretion properly.
Appellant failed to cross-move. CPLR 2215 requires a formal motion.
Appellant did not comply with CPLR 2215."

ARGUMENT STYLE EXAMPLE \u2014 GOOD (persuasive, flowing):
"The Supreme Court properly exercised its discretion in declining to treat
Ekstein's informal opposition papers as a cross-motion for relief. Under
CPLR 2215, a party seeking affirmative relief must make a formal cross-motion
\u2014 a requirement Ekstein indisputably failed to meet (5). While courts retain
discretion to entertain informal requests, _Fried v. Jacob Holding, Inc._,
110 AD3d 56 [2d Dept 2013], that discretion is not unlimited, and the factors
identified in _Fried_ weigh decisively against Ekstein here."

SENTENCE FLOW OPTIMIZATION (both modes):
1. PRONOUN CHAINS: Link sentences with pronouns referring to previous subjects
2. TOPIC CONTINUITY: Maintain subject continuity within topic groups
3. LOGICAL GROUPING: 3-7 sentences per paragraph, introduction \u2192 support \u2192 transition
4. SUBORDINATION: Use subordinate clauses for cause/effect, time, condition

PARTY REFERENCES \u2014 CRITICAL:
- Do NOT repetitively use "Appellant" or "Respondent" \u2014 it becomes monotonous
- Use the party's NAME (e.g., "Ekstein," "Zweibel") as the primary reference
- Use their ROLE in the case below (e.g., "defendant," "plaintiff") as secondary reference
- Use "Appellant"/"Respondent" only occasionally for variety
- Use pronouns ("he," "she," "they") naturally after establishing who you mean
- Mix these references: name \u2192 pronoun \u2192 role \u2192 name \u2192 pronoun

QUOTATION MARKS \u2014 SACRED:
- Quotation marks indicate EXACT WORDS from testimony, court decisions, or statutes
- NEVER remove quotation marks from quoted language
- NEVER paraphrase text that appears in quotation marks \u2014 preserve the exact words
- NEVER convert a direct quote into a paraphrase by dropping the quotes
- If the source material has language in quotes, keep it in quotes in the brief
- Adding quotation marks to language that was not quoted is equally wrong

ABSOLUTE RULE \u2014 NO FABRICATED QUOTES:
- NEVER place language inside quotation marks unless it appears VERBATIM in the source documents
- If you cannot find the EXACT words in the provided documents, DO NOT quote \u2014 paraphrase WITHOUT quotation marks instead
- NEVER reconstruct, approximate, or synthesize a quote from memory or context
- NEVER combine words from different parts of a source into a single "quote"
- If a fact appears in the brief structure or instructions with quoted language, VERIFY it appears verbatim in the source documents before including it in quotes. If you cannot verify, paraphrase without quotes.
- Fabricating a quote in a legal brief is sanctionable conduct. When in doubt, paraphrase.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
ATTORNEY VOICE \u2014 MANDATORY STYLE PATTERNS
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

You MUST write in the attorney's distinctive voice. These patterns are derived from
4,020 documents and 430,145 paragraphs spanning 25 years of legal writing.

SIGNATURE PHRASES \u2014 USE THESE NATURALLY THROUGHOUT:
- "We respectfully submit" \u2014 primary advocacy phrase, use to frame key conclusions and transitions
- "Moreover," / "In addition," \u2014 additive layering transitions to start sentences
- "Indeed," \u2014 emphasis transition at sentence start to drive home points
- "It is well settled that" / "It is black letter law that" \u2014 introduce established legal principles
- "Contrary to [party]'s assertions" \u2014 rebuttal opener
- "is unavailing" / "is without merit" / "is baseless" \u2014 dismiss opposing arguments
- "Under these circumstances" / "In the case at bar" \u2014 pivot to application
- "Regardless," \u2014 introduce alternative/independent argument
- "fails to address" / "fails to take into consideration" \u2014 identify gaps in opposing papers
- "Based upon the foregoing" \u2014 conclusion opener
- "should be reversed" / "warrants reversal" \u2014 appellate conclusion
- "abuse of discretion" \u2014 standard of review

STRUCTURAL ARGUMENT PATTERNS \u2014 USE THESE TEMPLATES:

1. OPPONENT REBUTTAL: "And, contrary to [party]'s assertions, [rule with quoted authority] ([lead case]; [string cite]). Indeed, [elaboration or second quoted rule] ([additional cites]). In addition, [statute or procedural rule]."

2. CREDIBILITY ATTACK: "[Party]'s contention that [X] is unavailing. Indeed, it is well settled that [legal rule] ([pattern jury instruction or treatise], citing, [string cite])."

3. CASE DISTINCTION: "We respectfully submit that [case] is inapplicable. It is uncontested that [factual distinction 1]. Moreover, [factual distinction 2]. It is black letter law that [rule] ([lead case], quoting [source]; [string cite]). Conversely, at bar [application]. Thus, there can be no dispute that [case] is inapplicable."

4. FACTUAL DEMOLITION: "[Party] submitted [evidence]. However, [witness] stated that [contradicting fact] ([record cite]). It was uncontested that [devastating fact] ([string cite establishing legal consequence])."

5. STANDARD OF REVIEW: "[Quoted standard with lead case]. [Second quoted formulation] ([lead case]; see also [5-10 cases]; see generally [10+ cases])."

6. CONCLUSION: "We respectfully submit that the order should be reversed. [State error]. However, this is not a correct statement of the law. [Correct rule with string cite]. We respectfully submit that [party] failed to meet [its/their] burden. Regardless, [alternative argument]. In addition, [third layer]."

STRING CITATION STYLE:
- Stack 5-15+ cases separated by semicolons in a single parenthetical
- Signal hierarchy: see \u2192 see also \u2192 see generally \u2192 cf. \u2192 accord \u2192 citing
- Include parenthetical descriptions for key cases, bare cites for supporting weight
- NY state format: [2d Dept. 2011]; Federal format: (2d Cir. 2013)

ANTI-PATTERNS \u2014 NEVER DO THESE:
- NEVER use em dashes (\u2014). Use commas instead. Hyphens in compound words are fine.
- NEVER write "It is important to note" or "Significantly" \u2014 these are AI filler phrases
- NEVER write "First and foremost" \u2014 not in the attorney's vocabulary
- NEVER write "It bears noting" \u2014 not in the attorney's vocabulary
- NEVER use bullet points in the body of briefs \u2014 always continuous prose
- NEVER use passive hedging ("it could be argued") \u2014 take definitive positions
- NEVER write "In conclusion" \u2014 use "Based on/upon the foregoing" instead
- NEVER start multiple consecutive paragraphs with "The" \u2014 use transitions (Moreover, Indeed, In addition)
- NEVER write single-sentence paragraphs in argument sections \u2014 build substantial paragraphs (3-7 sentences)
- NEVER separate the period from the citation: YES: "issue (Tr. at 127:6-7)." NO: "issue. (Tr. at 127:6-7)"
"""


def _build_exemplars(brief_type='appellant'):
    """Return curated exemplar passages matched to brief type for few-shot injection."""

    # All 13 exemplars keyed by type
    exemplars = {
        'opponent_rebuttal': """### OPPONENT REBUTTAL PATTERN:
> And, contrary to defendant's assertions, the fact that the police officers did not sign their deposition transcripts is of no moment. Indeed, it is well settled that where a deposition is not signed, but certified by a reporter and not challenged as inaccurate, it may be considered in opposition to a party's motion for summary judgment (see, Femia v. Graphic Arts Mut. Ins. Co., 100 AD3d 954, 955 [2d Dept. 2012]; Martin v. City of New York, 82 AD3d 653, 654 [1st Dept. 2011]; Bennett v. Berger, 283 AD2d 374 [1st Dept. 2001]; Zabari v. City of New York, 242 AD2d 15 [1st Dept. 1998]; Zalot v. Zieba, 81 AD3d 935 [2d Dept. 2011]; Felberbaum v. Weinberger, 40 AD3d 808 [2d Dept. 2007]). In addition, CPLR 3116(a) states: "If the witness fails to sign and return the deposition within sixty days, it may be used as fully as though signed." As there is no dispute that the plaintiff had sent the police officer's transcripts to their attorneys requesting that they be executed in accordance with CPLR 3116(a) (217-218), their deposition transcripts were properly considered by the trial court.""",

        'credibility_attack': """### CREDIBILITY ATTACK PATTERN:
> 452's contention that it did not have notice of the defective condition because it was not responsible for inspecting the ramp is unavailing and without any legal support. Indeed, it is well settled that "[t]he duty to provide a safe place to work includes the detection of dangers discoverable by reasonable diligence" (N.Y. Pattern Jury Instr.--Civil 2:216, citing, Lunde v Nichols Yacht Sales, Inc., 143 AD2d 816 [2d Dept 1988]; Kennedy v McKay, 86 AD2d 597 [2d Dept 1982]; Lagzdins v United Welfare Fund-Security Division Marriott Corp., 77 AD2d 585 [2d Dept 1980]; Monroe v New York, 67 AD2d 89 [2d Dept 1979]; Bass v Standard Brands, Inc., 65 AD2d 689 [1st Dept 1978]).""",

        'credibility_attack_expert': """### CREDIBILITY ATTACK (EXPERT) PATTERN:
> It is well settled that opinion evidence must be based on facts in the record or personally known to the witness (Hambsch v. NYCTA, 63 NY2d 723 [1984]; Matott v. Ward, 48 NY2d 455 [1979]; Cassano v. Hagstrom, 5 NY2d 643 [1959]). Expert testimony is not admissible unless there is a proper foundation for it (Parker v. Mobil Oil Corp., 7 NY3d 434 [2006]; Amatulli v. Delhi Constr. Corp., 77 NY2d 525 [1991]). "Where the expert's ultimate assertions are speculative or unsupported by any evidentiary foundation ... the opinion should be given no probative force and is insufficient to withstand summary judgment" (Diaz v. N.Y. Downtown Hosp., 99 NY2d 542 [2002]). Where an opinion is "conclusory in every respect", it should be "disregarded entirely." (Bender v. Gross, 33 AD3d 417 [1st Dept. 2006]).""",

        'case_distinction': """### CASE DISTINCTION PATTERN:
> We respectfully submit that Bing is inapplicable to this case. It is uncontested that plaintiff in this case alleged a violation of section 7-210, while the plaintiff in Bing did not. Moreover, in Bing, the accident occurred on a ramp, not the sidewalk. It is black letter law that "pedestrian ramps are not part of the sidewalk for the purpose of imposing liability on abutting landowners pursuant to [section 7-210]" (Rodriguez v. Themelion Realty Corp., 94 AD3d 733 [2d Dept. 2012], quoting, Vidakovic v. City of New York, 84 AD3d 1357, 1357-1358 [2d Dept. 2011]; see, Gary v. 101 Owners Corp., 89 AD3d 627, 627-628 [1st Dept. 2011]; Ortiz v. City of New York, 67 AD3d 21, 23, 27-28 [1st Dept. 2009], revd. on other grounds, 14 NY3d 779 [2010]). Conversely, at bar the plaintiff "slipped on snow and ice on the sidewalk adjacent to [defendants'] property" (emphasis added). Thus, there can be no dispute that Bing is inapplicable to section 7-210.""",

        'factual_narrative': """### FACTUAL NARRATIVE PATTERN:
> Defendants also submitted the affidavit of Dr. Howard M. Sandler, who conducted a defense exam of the plaintiff on August 16, 2016, and claimed, contrary of the findings of the Workers' Compensation Board, that plaintiff was a malingerer and did not suffer from asthma, chemical gastritis and ocular problems (1548-1576). However, Dr. Sandler stated that his conclusions regarding plaintiff's pulmonary claims, were "...based in part on the independent medical examination performed by pulmonologist, Dr. Mitchell Horowitz..." (1568). It was uncontested that Dr. Horowitz's report was unsworn and unaffirmed and thus inadmissible (see, Kreimerman v. Stunis, 74 AD3d 753, 755 [2d Dept. 2010]; Magid v. Lincoln Servs. Corp., 60 AD3d 1008 [2d Dept. 2009]; Casas v. Montero, 48 AD3d 72 [2d Dept. 2008]; Malave v. Basikov, 45 AD3d 539 [2d Dept. 2007]; Nkhereanye v. Hillaire, 35 AD3d 419 [2d Dept. 2006]).""",

        'standard_of_review': """### STANDARD OF REVIEW PATTERN:
> Stated otherwise, where a "resolution of a factual issue is clearly at variance with the proper testimony, the failure to set aside the verdict and direct a new trial constitutes an abuse of discretion" (DeAngelis v. Kirschner, 171 AD2d 593-5 [1st Dept. 1991]). Where there is "abundant evidence establishing negligence on the part of a defendant", an "essential finding of non-liability against him is against the credible weight of the evidence" (Yalkut v. NYC, 162 AD2d 185 [1st Dept. 1990]; see also Arrigo v. Turner Constr. Co., 182 AD2d 482 [1st Dept. 1992]; Browne v. Pikula, 256 AD2d 1139 [4th Dept. 1998], quoting Darrow v. Lavancha, 169 AD2d 965-6 [3d Dept. 1991]; see generally Zhuravenko v. Gjelaj, 261 AD2d 399 [2d Dept. 1999]; Mathewson v. Bender, 259 AD2d 673 [2d Dept. 1999]; Panariello v. Ballinger, 248 AD2d 452 [2d Dept. 1998]; Dellavecchia v. Zorros, 231 AD2d 549 [2d Dept. 1996]; Mohamed v. Frische, 223 AD2d 628 [2d Dept. 1996]; Finkel v. Benoit, 211 AD2d 749 [2d Dept. 1995]; Carter v. Smalls, 162 AD2d 431 [2d Dept. 1990]; Pire v. Otero, 123 AD2d 611 [2d Dept. 1986].""",

        'summary_judgment_standard': """### SUMMARY JUDGMENT STANDARD PATTERN:
> It is well settled that "the proponent of a summary judgment motion must make a prima facie showing of entitlement to judgment as a matter of law, tendering sufficient evidence to demonstrate the absence of any material issues of fact" (O'Brien v. Port Auth. of N.Y. & N.J., 29 NY3d 27, 37 [2017], quoting, Alvarez v. Prospect Hosp., 68 NY2d 320, 324 [1986]). "This burden is a heavy one and on a motion for summary judgment, facts must be viewed in the light most favorable to the nonmoving party" (William J. Jenack Estate Appraisers & Auctioneers, Inc., v. Rabizadeh, 22 NY3d 470, 475 [2013]). "A defendant moving for summary judgment dismissing a complaint cannot satisfy its initial burden by merely pointing to gaps in the plaintiff's case" (Feldberg v. Skorupa, 151 AD3d 1016, 1017 [2d Dept. 2017]); "rather, it must affirmatively demonstrate the merit of its defense" (Pandarakalam v. Liberty Mut. Ins. Co., 137 AD2d 1234 [2d Dept. 2016]). "If the moving party fails to meet this initial burden, summary judgment must be denied 'regardless of the sufficiency of the opposing papers'" (Voss v. Netherlands Ins. Co., 22 NY3d 728, 734 [2014]).""",

        'summary_judgment_opposition': """### SUMMARY JUDGMENT OPPOSITION PATTERN:
> We respectfully submit that the defendant's motion should be denied. First, it failed to meet its burden for summary judgment. In this vein, it is well settled in that when moving for summary judgment in a case like this a defendant must submit proof that it had maintenance procedures in place and that those procedures were followed prior to plaintiff's accident (see Yioves v. T.J. Max, 29 AD3d 572 [2d Dept. 2006]; Britto v. A&P, 21 AD3d 436-7 [2d Dept. 2005]; Jacques v. Richal Enterprises, 300 AD2d 45 [1st Dept. 2002]). The defendant's argument that Mr. Yiguang inspected the building every day and that the condition could have developed minutes after he left the building is based on speculation and does not establish a prima facie case for summary judgment. Indeed, While Mr. Yiguang testified that he generally inspected the building every day, he never stated that he inspected the building on the day in question. Evidence concerning general maintenance procedures is not sufficient: what is required is proof that such procedures were actually followed on the date of the accident (See Yioves v. T.J. Max, 29 AD3d 572 [2d Dept. 2006]; Britto v. A&P, 21 AD3d 436-7 [2d Dept. 2005]; see generally Jacques v. Richal Enterprises, 300 AD2d 45 [1st Dept. 2002]; Lorenzo v. Plitt Theatres, 267 AD2d 54 [1st Dept. 1999]; Edwards v. Wal-Mart, 243 AD2d 803 [3d Dept. 1997]; Van Steenburg v. A&P, 235 AD2d 1001 [3d Dept. 1997]; Mancini v. Quality Markets, 256 AD2d 1177 [2d Dept. 1998]).""",

        'rhetorical_demolition': """### RHETORICAL DEMOLITION PATTERN:
> Defendant's reliance on Dr. Berman's testimony fairs no better. Although Dr. Berman's report stated that plaintiff was taking Oxycodone and Percocet, it was uncontested that plaintiff never took these painkillers (1450-1451). Dr. Berman's report erroneously claimed that the airbags deployed because of the impact (1385) when it was uncontested that they were not deployed (1449) as defendant crashed into the side of plaintiff's police car (883). In addition, Dr. Berman did not review the operative reports for plaintiff's wrist and shoulder surgeries, which were in evidence and available to him prior to trial (1456).""",

        'string_cite': """### STRING CITE PATTERN:
> Nico's assertion that it was not liable because no spikes were detected by its inspector or Con Ed's inspector is without merit. A defendant is required to "see what he should have seen" (Weigand v. United Traction Co., 221 NY 39 [1917]; see generally, Lolik v. Big V Supermarkets, 210 AD2d 703 [3d Dept. 1994]; Milka v. Hernandez, 187 AD2d 1031 [4th Dept. 1992]; Weiser v. Dalvo, 184 AD2d 935 [3d Dept. 1992]; Levitt v. County of Suffolk, 166 AD2d 421 [2d Dept. 1990]; Safran v. Amato, 155 AD2d 653 [2d Dept. 1989]; Sappleton v. Metropolitan Suburban Bus Authority, 140 AD2d 684 [2d Dept. 1988]; Lester v. Jolicofer, 120 AD2d 574 [2d Dept. 1986]; Kiernan v. Edwards, 97 AD2d 750 [2d Dept. 1983], app. dism. 62 NY2d 617 [1984]) and may be liable for the "failure to use reasonable care to discover and correct a condition which [he] ought to have found" (Rogers v. Dorchester Assoc., 32 NY2d 553, 559 [1973]).""",

        'conclusion_brief': """### CONCLUSION (BRIEF) PATTERN:
> We respectfully submit that the order should be reversed. Regarding plaintiff's Labor Law 200 and common law negligence claims, defendants argued, and the trial court apparently agreed, that these claims should be dismissed because defendants did not supervise or control plaintiff's work. However, this is not a correct statement of the law. The correct analysis depends on whether defendants possessed the authority to exercise supervision or control over the plaintiff's work, not whether they actually exercised supervision or control over the work (see, Reyes v. Arco Wentworth Management Corp., 83 AD3d 47, 51 [2d Dept. 2011]; Ortega v. Puccia, 57 AD3d 54, 62 [2d Dept. 2008]; Chowdhury v. Rodriguez, 57 AD3d 121, 122-123 [2d Dept. 2008]). As defendants never produced any evidence that they did not have the authority to supervise or control the plaintiff's work, we respectfully submit that they failed to meet their burden for summary judgment on this issue.""",

        'conclusion_motion': """### CONCLUSION (MOTION) PATTERN:
> Based upon the foregoing, it is reasonable to infer that Pescatore created the dangerous trap like condition (see, Palumbo v. Innovation Communication Concepts, 251 AD2d 246 [1st Dept. 1998]). In light of the foregoing, there are material issues of fact and the motion should be denied (see, Gamer v. Ross, 49 AD3d 598, 600 [2d Dept. 2008], citing Cupo v. Karfunkel, 1 AD3d 48 [2d Dept. 2003]).""",

        'incomplete_discovery': """### INCOMPLETE DISCOVERY DEFENSE PATTERN:
> Where a party moving for summary judgment has failed to respond to an opponent's legitimate discovery demands, the motion should be denied (see, Levy v. Bd. of Ed. of City of Yonkers, 232 AD2d 377-8 [2d Dept. 1996]; citing Wohlgemuth v. Logan, 144 AD2d 160 [3d Dept. 1988]). Indeed, motions for summary judgment have been routinely denied where discovery was incomplete or key information was either in the possession of the moving party or not available to the party opposing the motion (see, Espindola v. Jorawar, 228 AD2d 243 [1st Dept. 1996]; Darling v. Solomon, 227 AD2d 851 [3d Dept. 1996]; Cox v. JD Realty Assoc., 217 AD2d 179 [1st Dept. 1995]; Kelly v. Fleet Bank, 229 AD2d 659 [3d Dept. 1996]; Yu v. Forero, 184 AD2d 506-7 [2d Dept. 1992] (Where "evidence needed is within the exclusive knowledge of the moving party", motion should be denied)).""",
    }

    # Select subset based on brief_type
    type_map = {
        'appellant': ['factual_narrative', 'standard_of_review', 'case_distinction', 'string_cite', 'opponent_rebuttal', 'conclusion_brief'],
        'respondent': ['opponent_rebuttal', 'credibility_attack', 'case_distinction', 'factual_narrative', 'string_cite', 'conclusion_brief'],
        'reply': ['opponent_rebuttal', 'credibility_attack', 'case_distinction', 'rhetorical_demolition'],
    }

    selected_keys = type_map.get(brief_type, type_map['appellant'])
    selected = [exemplars[k] for k in selected_keys if k in exemplars]

    header = """
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
STYLE EXEMPLARS \u2014 MATCH THIS VOICE
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
These are REAL passages from the attorney's prior work.
Match the STRUCTURE, TRANSITIONS, CITATION DENSITY, and VOICE.
Do NOT copy the content \u2014 use the PATTERN.
"""
    return header + '\n\n'.join(selected)


def build_intro_task(brief_type):
    """Build the task instruction for Preliminary Statement / Introduction sections."""
    cite_rules = """CITATION FORMAT — READ THIS CAREFULLY:
Each source document is divided by "--- PAGE XXXX ---" markers. The number after PAGE is the Record on Appeal page number. Cite the SPECIFIC page where each fact appears.
- CORRECT: On January 15, 2021, plaintiff presented to Dr. Smith with complaints of fatigue and shortness of breath (1247).
- CORRECT: A CBC performed that day revealed a hemoglobin of 8.2 g/dL (1249).
- WRONG: Defendants failed to properly diagnose plaintiff's condition. ← This is argument, not fact
- WRONG: The lower court erred in granting summary judgment. ← This is argument, not fact
- WRONG: [CITE NEEDED] ← Every sentence states a fact from a specific page; find it
Use bare parenthetical page numbers only: (4252) or (1681). No "R." prefix. No "at p."
"""
    if brief_type == 'appellant':
        return f"""TASK: Draft the PRELIMINARY STATEMENT section.

THIS IS A FACTUAL SECTION — NO ARGUMENT ALLOWED:
The Preliminary Statement introduces the case by presenting the KEY FACTS and the procedural posture. It is NOT an argument section. Every sentence must state a FACT from the record with a record citation. Do NOT write legal conclusions, advocacy, or argument.

PROHIBITED (these are argument, not facts):
- "Defendants deviated from accepted medical practice" ← legal conclusion
- "The order should be reversed" ← advocacy
- "The court improvidently granted summary judgment" ← argument
- "Defendants negligently failed to..." ← legal conclusion
- Any sentence that cannot be tied to a specific record page

REQUIRED (these are facts):
- "Plaintiff presented to defendant Dr. Smith on January 15, 2021 with complaints of fatigue (1247)." ← fact with cite
- "A CT scan performed on March 3, 2021 revealed a 4 cm mass in the ascending colon (1683)." ← fact with cite
- "Defendants moved for summary judgment on July 10, 2023 (75)." ← procedural fact with cite
- "The court granted the motion by order dated December 5, 2023 (77)." ← procedural fact with cite

Structure:
1. One sentence identifying the appeal (from what order, what court)
2. Brief factual background — the key facts of what happened to the plaintiff, each with a record cite
3. Brief procedural posture — what motions were filed and how the court ruled, each with a record cite
4. Keep it concise — 3 to 5 paragraphs maximum

{cite_rules}
- Begin with the heading "PRELIMINARY STATEMENT" in ALL CAPS
- Write in polished appellate prose
- Present facts favorably to our client but EVERY sentence must be a verifiable fact from the record
- This is a standalone section — draft ONLY the Preliminary Statement"""
    elif brief_type == 'respondent':
        return f"""TASK: Draft the PRELIMINARY STATEMENT section.

THIS IS A FACTUAL SECTION — NO ARGUMENT ALLOWED:
The Preliminary Statement introduces the case by presenting the KEY FACTS and the procedural posture. It is NOT an argument section. Every sentence must state a FACT from the record with a record citation. Do NOT write legal conclusions, advocacy, or argument.

PROHIBITED (these are argument, not facts):
- "The lower court correctly determined..." ← legal conclusion
- "Appellant's claims are without merit" ← advocacy
- "The decision should be affirmed" ← argument
- Any sentence that cannot be tied to a specific record page

REQUIRED (these are facts):
- Factual statements about what happened, each citing a record page
- Procedural facts about what the court did, each citing a record page

Structure:
1. One sentence identifying the appeal
2. Brief factual background with record cites
3. Brief procedural posture with record cites
4. Keep it concise — 3 to 5 paragraphs maximum

{cite_rules}
- Begin with the heading "PRELIMINARY STATEMENT" in ALL CAPS
- Write in polished appellate prose
- Present facts favorably to our client but EVERY sentence must be a verifiable fact from the record
- This is a standalone section — draft ONLY the Preliminary Statement"""
    else:  # reply
        return f"""TASK: Draft the INTRODUCTION section.

THIS IS A FACTUAL SECTION — NO ARGUMENT ALLOWED:
The Introduction frames the reply by presenting the KEY FACTS that respondent misstated or ignored. Every sentence must state a FACT from the record with a record citation. Do NOT write legal conclusions, advocacy, or argument.

PROHIBITED:
- "Respondent mischaracterizes the record" ← argument
- "The court should reverse" ← advocacy
- Any sentence without a record citation

REQUIRED:
- Factual statements from the record, each with a page cite
- Where respondent's brief misstates a fact, state the ACTUAL fact from the record with the cite

Structure:
1. One sentence identifying what the reply addresses
2. The key factual corrections, each citing the record
3. Keep it concise — 2 to 4 paragraphs maximum

{cite_rules}
- Begin with the heading "INTRODUCTION" in ALL CAPS
- Write in polished appellate prose
- This is a standalone section — draft ONLY the Introduction"""


def build_argument_task(argument_number):
    """Build the task instruction for an argument (Point) section."""
    return f"TASK: Draft ONLY POINT {argument_number}. Draft this ONE argument section ONLY. Do NOT draft a Statement of Facts, Procedural History, Preliminary Statement, Conclusion, or any other Point. Output ONLY the argument for POINT {argument_number} with its point heading. Use proper appellate brief formatting."


def build_conclusion_task(brief_type):
    """Build the task instruction for the conclusion section."""
    if brief_type == 'appellant':
        return "TASK: Draft the CONCLUSION requesting reversal and specifying the relief sought."
    elif brief_type == 'respondent':
        return "TASK: Draft the CONCLUSION requesting affirmance of the lower court's decision."
    else:
        return "TASK: Draft the CONCLUSION section requesting specific relief."


def build_facts_task(brief_type, custom_instructions=''):
    """Build the task instruction for Statement of Facts / Counterstatement.
    Includes the RELEVANCE guardrail."""
    if brief_type == 'respondent':
        facts_heading = "COUNTERSTATEMENT OF FACTS"
    else:
        facts_heading = "STATEMENT OF THE CASE"
    if custom_instructions and len(custom_instructions) > 300:
        return f"TASK:\n\n{custom_instructions}"

    task = f"""TASK: Draft a COMPREHENSIVE {facts_heading} section.

COMPLETENESS IS MANDATORY — EXTRACT EVERY FACT:
You MUST extract EVERY medical fact, date, test result, finding, symptom, diagnosis, treatment, and outcome from the source documents. Do NOT summarize. Do NOT condense. Do NOT skip facts you consider minor. The attorney needs ALL facts from these documents in narrative form. If a source document mentions a date, a test, a finding, a symptom, a medication, a procedure, or a clinical observation, it MUST appear in your draft.

Requirements:
- Begin with the heading "{facts_heading}" in ALL CAPS
- Present the facts as a narrative, organized chronologically
- Every factual assertion MUST include a record citation
- Write in polished appellate prose, not bullet points
- Do NOT include legal argument, case law citations, or standards of review
- Do NOT include procedural history (motions, orders, filings) — only substantive facts
- Present facts favorably to our client's position while remaining accurate to the record
- Use direct quotes from the record where the language is powerful or damning
- This is a standalone section — draft ONLY the {facts_heading}

CITATION FORMAT — READ THIS CAREFULLY:
Each source document is divided by "--- PAGE XXXX ---" markers. The number after PAGE is the Record on Appeal page number. Cite the SPECIFIC page where each fact appears, not the page range of the entire document.
- CORRECT: On August 8, 2022, a CT scan of the abdomen and pelvis was performed (1683).
- CORRECT: The pathology report documented adenocarcinoma of the colon (1670).
- CORRECT: Lab results from that date showed a WBC of 4.44 K/mcL (1681).
- WRONG: The CT scan revealed a mass (1683-1693). ← Do not cite the entire document range
- WRONG: [CITE NEEDED] ← Every fact comes from a specific page; find it
- WRONG: (R. at 1683) ← No "R. at" prefix. Just (1683).
Use bare parenthetical page numbers only: (4252) or (1681). No "R." prefix. No "at p."
If a fact spans two consecutive pages, cite both: (4252-4253). Never cite ranges wider than 2-3 pages.

FACTS ONLY — NO OPINIONS, NO ARGUMENT, NO CONCLUSIONS:
This section presents WHAT HAPPENED — the medical timeline, symptoms, test results, diagnoses, treatments, and outcomes. Your source documents are primarily expert affidavits, which mix facts with expert opinions. You MUST separate the two. Extract ONLY the underlying facts. Leave ALL opinions for the Expert Opinions section.

HOW TO TRANSLATE EXPERT OPINION LANGUAGE INTO FACTS:
Expert affidavits use opinion language like "failed to," "deviated from," "should have," "inadequate," "negligent." These are OPINIONS, not facts. You must translate them into neutral factual statements.

PROHIBITED — these are opinions/argument copied from expert affidavits:
- "Defendants failed to order iron studies" ← opinion about what should have been done
- "Defendants' approach remained inadequate" ← opinion/judgment
- "The anemia went inadequately investigated" ← opinion
- "Defendants negligently failed to diagnose" ← legal conclusion
- "Should have ordered a colonoscopy" ← opinion about standard of care
- "Departed from accepted medical practice" ← expert opinion
- "Failed to recognize" / "failed to properly work up" ← opinion
- "Despite having confirmed X, defendants did not Y" ← argumentative framing

REQUIRED — translate each of the above into the neutral underlying fact:
- "No iron studies were ordered at the October 20, 2022 visit (4251)." ← what actually happened
- "Plaintiff was instructed to take iron supplements and return in three months (1673)." ← what was done
- "No colonoscopy or endoscopy was ordered between October 2022 and June 2023 (4252)." ← what did not happen, stated neutrally
- "Plaintiff's hemoglobin declined from 14.6 g/dL on July 6, 2021 to 11.7 g/dL on October 20, 2022 (4251)." ← the objective data
- "No fecal occult blood testing was performed (4252)." ← neutral statement of absence

THE RULE: If a sentence uses "failed to," "should have," "inadequate," "negligent," "deviated," "departed," or any similar judgment language, REWRITE IT as a neutral statement of what DID or DID NOT happen, with a record cite. The reader can draw their own conclusions from the facts.

ADDITIONAL RULES:
- Do NOT identify experts by name, credential, or specialty in this section
- Do NOT write "Dr. X opined that..." or "plaintiff's expert concluded..." — those belong in the Expert Opinions section
- INSTEAD, present the facts the experts cite: "On August 8, 2022, a CT scan revealed..." (1683)
- Medical records (lab results, imaging, progress notes) should be presented as the facts they are
- Build a thorough chronological medical narrative: every symptom, consultation, test, finding, diagnosis, treatment, medication, and current condition

RELEVANCE — ONLY FACTS RELATED TO THE CLAIMS:
- Do NOT include biographical details (education, profession, hobbies, marital status) unless directly relevant to the claims or damages
- Every fact you include must relate to: (a) the events giving rise to liability, (b) the parties' conduct at issue, (c) causation, or (d) damages and current condition
- For damages/current condition, a concise summary is sufficient — do not exhaustively catalog every treatment, medication, or test result unless instructed to do so
- If a fact does not connect to any element of the claims, OMIT IT"""
    if custom_instructions:
        task += f"\n\nADDITIONAL ATTORNEY INSTRUCTIONS:\n{custom_instructions}"
    return task


def build_procedural_history_task(custom_instructions=''):
    """Build the task instruction for the procedural history section."""
    if custom_instructions and len(custom_instructions) > 300:
        task = f"TASK:\n\n{custom_instructions}"
    else:
        task = """TASK: Draft the PROCEDURAL HISTORY section.

Requirements:
- Begin with the heading "PROCEDURAL HISTORY" in ALL CAPS
- Include: commencement of the action, key motions filed, court orders and decisions, trial dates, verdict/judgment
- Present in chronological order with specific dates where available
- Cite to the record for each procedural event, e.g., (R. at 12)
- Write in plain, factual prose — no advocacy or argument
- Do NOT include substantive facts, testimony, or legal analysis
- This is a standalone section — draft ONLY the PROCEDURAL HISTORY"""
        if custom_instructions:
            task += f"\n\nADDITIONAL ATTORNEY INSTRUCTIONS:\n{custom_instructions}"
    return task


def build_expert_opinions_task(custom_instructions=''):
    """Build the task instruction for the expert opinions section."""
    task = """TASK: Draft the EXPERT OPINIONS section.

Requirements:
- Begin with the heading "EXPERT OPINIONS" in ALL CAPS
- For EACH expert, present in this order:
  1. Full name, specialty, and credentials (board certifications, years of experience, academic appointments) — ONE paragraph
  2. Their opinions organized by topic: standard of care, departures from the standard, causation, injuries/damages
  3. Quote the expert's strongest conclusions verbatim
- Separate each expert with a subheading (expert's name and specialty)
- Present opinions as advocacy — frame them favorably for our client
- Write in polished appellate prose
- Do NOT include the underlying facts (dates, test results, medical timeline) — those belong in the Statement of the Case

CITATION FORMAT — READ THIS CAREFULLY:
Each source document is divided by "--- PAGE XXXX ---" markers. The number after PAGE is the Record on Appeal page number. When you cite a fact, cite the SPECIFIC page where that fact appears.
- CORRECT: Dr. Smith opined that defendants departed from the standard of care by failing to order a CT scan (4252).
- WRONG: (4249-4259) ← Do not cite the entire document range
- WRONG: [CITE NEEDED] ← Every fact comes from a specific page; find it
Use bare parenthetical page numbers only: (4252) or (4252-4253). No "R." prefix. No "at p."
If a fact spans two consecutive pages, cite both: (4252-4253). Never cite ranges wider than 2-3 pages.
- FOCUS on what each expert CONCLUDED and WHY — their professional opinions on standard of care, departures, and causation
- This is a standalone section — draft ONLY the Expert Opinions"""
    if custom_instructions:
        task += f"\n\nADDITIONAL ATTORNEY INSTRUCTIONS:\n{custom_instructions}"
    return task


def build_custom_section_task(custom_instructions):
    """Build the task instruction for a custom section."""
    return f"""TASK: Draft the following section of the brief based on the attorney's instructions:

{custom_instructions}

Use the uploaded documents as your source material. Include proper record citations. Write in polished appellate prose. This is a standalone section — do NOT draft the entire brief, only the section described above."""


def build_revision_prompt(revision_instructions, party_context, constraints=''):
    """Build the full revision prompt including all guardrails.
    Includes rules 7 & 8 (relevance/biographical and post-diagnosis treatment)."""
    return f"""You are an expert appellate attorney revising a brief.

PARTY CONTEXT — CRITICAL:
{party_context}

{constraints}

=== REVISION INSTRUCTIONS ===
{revision_instructions}

The EXISTING BRIEF and ORIGINAL SOURCE DOCUMENTS are provided as structured document blocks for citation tracking.

REVISION RULES - CRITICAL:

1. Apply ONLY the changes described in the revision instructions
2. Preserve ALL existing content that is not affected by the revisions
3. Keep all existing case citations intact unless specifically told to change them
4. Keep all existing record/appendix citations intact unless specifically told to change them
5. Do NOT add new cases from your training data — only use cases from the source documents above
6. Return the COMPLETE revised brief (not just the changed parts)
7. Do NOT inject irrelevant biographical details (education, profession, hobbies) unless directly relevant to the claims
8. Do NOT expand post-diagnosis treatment details unless specifically instructed — a brief statement of current condition is sufficient

*** NO OMISSIONS — MANDATORY ***
You may condense or tighten prose, but you must NEVER omit arguments, points, or content.
- Every argument point in the original MUST appear in the revision
- Every case citation and its discussion MUST be preserved
- Every factual assertion with a record cite MUST be preserved
- Condensing a paragraph into tighter prose is fine
- DROPPING a paragraph, argument, or case discussion is NOT fine
- If the original has Points I through IV, the revision must have Points I through IV
- Omitting content is as bad as hallucinating content — both are unacceptable

*** QUOTATION MARKS — NEVER REMOVE ***
- If text is in quotation marks, it is a DIRECT QUOTE from testimony, a court decision, or a statute
- NEVER remove quotation marks from quoted language
- NEVER paraphrase text that is in quotation marks — the quotes indicate EXACT WORDS
- NEVER convert a direct quote into a paraphrase by dropping the quotation marks
- You may move a quoted passage or tighten surrounding prose, but the quoted text itself must remain verbatim and in quotation marks
- Adding quotation marks to language that was not quoted is equally wrong — do not fabricate quotes

*** CASE CITATION GUARDRAILS — ZERO TOLERANCE FOR FABRICATION ***

YOU ARE FORBIDDEN FROM INVENTING CASE NAMES.

YOUR ONLY SOURCES FOR CASE LAW ARE:
a) Cases already in the existing brief you are revising
b) Cases in the uploaded Legal Research document
c) Cases in any other uploaded source documents above

THAT'S IT. NO OTHER SOURCES.

BEFORE YOU WRITE ANY NEW CASE CITATION, ASK:
"Is this case in the existing brief OR in the uploaded documents?"
If NO → DO NOT CITE IT. Write [CASE CITE NEEDED] instead.

YOU MUST NOT:
- Cite ANY case from your training data
- Invent a case name that "sounds right"
- Guess at case names
- Fabricate holdings for real cases

CASE CITATION FORMAT:
- Case names MUST use UNDERSCORES: _Case Name v. Party_
- DO NOT use **asterisks** for case names
- NY Official format: _Case Name_, 123 AD3d 456 [2d Dept 2020]
- The court and year MUST be in SQUARE BRACKETS [ ], NEVER parentheses ( )
- WRONG: 123 AD3d 456 (2d Dept 2020) — DO NOT USE PARENTHESES for court/year
- CORRECT: 123 AD3d 456 [2d Dept 2020] — ALWAYS USE BRACKETS for court/year
- This applies to ALL reporters: AD2d, AD3d, NY2d, NY3d, Misc 2d, Misc 3d

RECORD CITATIONS - CRITICAL FORMAT:
- NEVER use "R." prefix - that is WRONG
- NEVER use "A." prefix - that is WRONG
- CORRECT format: (page number). with period AFTER parenthesis
- WRONG: (R. 45). WRONG: (A. 123).
- CORRECT: (45). CORRECT: (123).

ANTI-HALLUCINATION — ABSOLUTE:
- NEVER invent facts not in the source documents
- NEVER invent case names or holdings — this is malpractice
- NEVER use your training data for legal citations
- If unsure, write [VERIFY] rather than guess
- A brief with [CASE CITE NEEDED] is useful; a brief with fabricated citations is malpractice

FORMATTING - CRITICAL (PLAIN TEXT, NO MARKDOWN):
- NEVER use ## or # or ** or * or any markdown syntax
- Output PLAIN TEXT ONLY — this is a legal brief, not a markdown document
- Section headings: plain ALL CAPS on their own line (e.g., PRELIMINARY STATEMENT)
- Point headings: "POINT I" on its own line, then heading text in ALL CAPS on next line
- Sub-headings: tab + letter + tab + text
- Body paragraphs: Start each paragraph with a tab character
- Block quotes: Indent with two tabs
- Blank line between paragraphs and before/after headings
- Case names: _underscores_ only, NEVER **asterisks**
- Preserve the existing formatting style of the brief you are revising

{_build_anti_hallucination_block()}

{_build_writing_style()}

OUTPUT ONLY THE COMPLETE REVISED BRIEF TEXT. No commentary. PLAIN TEXT ONLY — NO MARKDOWN:"""


def _build_structure_prompt(structure):
    """Build the attorney-directed structure block for the drafting prompt"""
    parts = []
    parts.append("=== ATTORNEY-DEFINED BRIEF STRUCTURE (MANDATORY \u2014 FOLLOW EXACTLY) ===")
    parts.append("")
    parts.append("The attorney has defined the exact structure for this brief.")
    parts.append("Draft ONLY the Points defined below. Use ONLY the facts and cases listed.")
    parts.append("Do NOT invent additional arguments, facts, or Points beyond what is specified.")
    parts.append("Do NOT add cases from your training data \u2014 only use cases the attorney listed")
    parts.append("and cases found in the uploaded documents.")
    parts.append("")

    if structure.get('preliminary_statement'):
        parts.append("PRELIMINARY STATEMENT NOTES:")
        parts.append(structure['preliminary_statement'])
        parts.append("")

    if structure.get('procedural_history'):
        parts.append("PROCEDURAL HISTORY:")
        parts.append(structure['procedural_history'])
        parts.append("")

    if structure.get('factual_background'):
        parts.append("KEY FACTS:")
        parts.append(structure['factual_background'])
        parts.append("")

    for pt in structure.get('points', []):
        parts.append(f"--- POINT {pt['id']}: {pt.get('heading', '')} ---")
        if pt.get('argument_description'):
            parts.append(f"ARGUMENT: {pt['argument_description']}")
        if pt.get('facts'):
            parts.append(f"KEY FACTS FOR THIS POINT:\n{pt['facts']}")
        if pt.get('cases'):
            parts.append(f"KEY CASES FOR THIS POINT:\n{pt['cases']}")
        parts.append("")

    parts.append("=== END ATTORNEY-DEFINED STRUCTURE ===")
    return "\n".join(parts)
