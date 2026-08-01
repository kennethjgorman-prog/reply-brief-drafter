# CLAUDE.md — BriefDrafter Project Guide

## CRITICAL: READ THIS ENTIRE FILE BEFORE MAKING ANY CHANGES

BriefDrafter is a **production application** built by an appellate attorney with 25+ years of experience. It drafts appellate briefs (Appellant's, Respondent's, Reply) using Claude AI with multi-pass drafting, citation guardrails, NYSCEF hyperlinking, and DOCX generation.

**THIS IS A COMMERCIAL-GRADE APPLICATION. DO NOT:**
- Refactor, rename, or reorganize existing code
- Make changes unless explicitly asked with a clear, specific instruction
- Touch files you were not asked to touch
- Run `git checkout`, `git reset`, or any destructive git commands
- "Improve" or "clean up" working code
- Add dependencies without explicit permission

**WHEN IN DOUBT, ASK. DO NOT ACT.**

If the developer asks a question or discusses an approach, that is NOT permission to edit code. Only edit when given a clear, unambiguous directive like "change line X to Y" or "add function Z to file W."

---

## Architecture Overview

```
BriefDrafter/
  app.py                    — Main Flask app (63K), all primary routes, runs on port 5003
  config.json               — App configuration
  .env                      — Environment variables (API keys)
  requirements.txt          — flask, anthropic, python-dotenv, pdfplumber, python-docx
  src/
    __init__.py
    config.py               — Constants: PROJECTS_DIR, BRIEF_TYPE_CONFIG, file limits
    project_io.py           — Project CRUD, text extraction, file validation
    claude_client.py        — Claude API wrapper (call_claude, call_claude_with_docs)
    text_processing.py      — Text cleaning, truncation, fitting, search
    analysis.py             — Argument analysis (appellant, respondent, reply)
    prompt_builders.py      — All AI prompt construction (71K — largest file)
    drafting_engine.py      — Multi-pass brief drafting orchestrator (51K)
    guardrails.py           — Citation validation, anti-hallucination checks (74K)
    record_indexing.py      — Record evidence extraction, transcript quotes
    document_gathering.py   — Document collection and preprocessing
    docx_generator.py       — DOCX output with NYSCEF hyperlinks, formatting (27K)
    routes/
      __init__.py
      hyperlinker.py        — STANDALONE record hyperlinker tool (809 lines) *** SEE BELOW ***
      dropbox_routes.py     — Dropbox integration
      summarization.py      — Transcript summarization routes
      witness.py            — Witness analysis routes
    processors/
      two_pass_processor.py — Two-pass transcript processing
    utils/
      citation_validator.py — Citation accuracy checking
      qc_reporter.py        — Quality control reporting
      transcript_parser.py  — Transcript parsing utilities
      file_parser.py        — File format parsing
  templates/
    index.html              — Project list / create
    workspace.html          — Project workspace UI
    hyperlinker.html        — Standalone hyperlinker UI
  projects/                 — User project data (JSON + uploaded files)
  protocols/                — AI prompt protocols
```

---

## Key Modules (DO NOT MODIFY WITHOUT EXPLICIT INSTRUCTION)

### app.py — Main Flask Application (63K)
Primary routes for the entire application:

| Route | Purpose |
|-------|---------|
| `GET /` | Project list / create new |
| `POST /create` | Create new brief project |
| `GET /project/<id>` | Project workspace |
| `POST /project/<id>/upload` | Upload documents |
| `POST /project/<id>/analyze` | AI argument analysis |
| `POST /project/<id>/draft-section` | Draft individual brief section |
| `POST /project/<id>/draft-entire` | Draft complete brief |
| `POST /project/<id>/revise` | Revise drafted brief |
| `POST /project/<id>/supplement` | Supplement with additional research |
| `POST /project/<id>/generate` | Generate final DOCX |
| `GET /project/<id>/download` | Download generated brief |

### src/routes/hyperlinker.py — Standalone Hyperlinker (809 lines)

**THIS FILE WAS NEARLY DESTROYED IN A PREVIOUS SESSION. TREAT IT AS SACRED.**

A standalone tool (registered as Flask blueprint) that adds clickable hyperlinks to record/appendix citations in .docx and .pdf files. Supports NYSCEF (NY state) and PACER/ECF (federal).

**Key components:**

1. **Citation Patterns** (lines 18-42): Six regex patterns — bare, r_dot, a_dot, ja_dot, sa_dot. Uses `_D` (dash/en-dash), `_PAGE_PART`, `_PAGE_LIST` building blocks.

2. **Citation Detection** (lines 45-64): `_find_all_citations()` — finds all citations using selected format patterns, returns sorted non-overlapping matches.

3. **Page Label Parsing** (lines 84-166): `_parse_pdf_page_labels()` — reads PDF `/PageLabels` metadata to map page labels to physical pages. Handles `/Nums`, `/Kids`, roman numerals, prefixes.

4. **URL Resolution** (lines 169-193): `_resolve_url_via_labels()` — given a page number, finds it across volumes and returns URL. Supports three modes:
   - `hosted` — self-hosted PDFs at `/hyperlinker/hosted/{id}#page=N`
   - `pacer` — external court URL with `#page=N`
   - `nyscef` — NYSCEF ViewDocument URL with `#page=N`

5. **DOCX Hyperlinking** (lines 196-338): `add_hyperlinks_to_docx()` — character-level format preservation. Rebuilds paragraph XML, wrapping citations in `<w:hyperlink>` elements while preserving all formatting (bold, italic, fonts). **This is extremely delicate code.**

6. **Merged PDF Mode** (lines 341-447): `add_hyperlinks_merged_pdf()` — merges brief + volumes into single PDF with internal GOTO links (no external references, no security warnings).

7. **PDF Hyperlinking** (lines 450-497): `add_hyperlinks_to_pdf()` — adds URI link annotations to PDF citations.

8. **Self-Hosting** (lines 500-524): Routes and functions for serving hosted PDFs.

9. **Upload/Process Routes** (lines 527-809): Session management, file upload, volume management, processing orchestration.

### src/guardrails.py — Citation Guardrails (74K)
Anti-hallucination system. Validates every citation in AI-generated briefs against source documents. Functions include `validate_citations()`, `enforce_paragraph_cites()`, `enforce_case_cites()`, `guardrail_brief()`, `verify_factual_fidelity()`, `editorial_review_pass()`.

### src/prompt_builders.py — Prompt Construction (71K)
Builds all AI prompts for drafting. Contains task builders for each brief section (intro, argument, conclusion, facts, procedural history, expert opinions). Also handles revision and supplement prompts.

### src/drafting_engine.py — Multi-Pass Drafting (51K)
Orchestrates the AI drafting process: `_draft_appellant_brief()`, `_draft_respondent_brief()`, `_draft_reply_brief()`. Each uses multiple Claude API calls in sequence.

### src/docx_generator.py — DOCX Output (27K)
Generates formatted Word documents from drafted content. Handles NYSCEF hyperlinks within generated text (`_add_text_with_citations()`), court captions, headings, formatting. Key functions: `generate_brief_docx()`, `generate_section_docx()`, `resolve_nyscef_url()`.

---

## How the Drafting Pipeline Works

1. **Create Project** — User names case, selects brief type (appellant/respondent/reply)
2. **Upload Documents** — Opening brief (for respondent/reply), record volumes, legal research, transcripts
3. **Analyze Arguments** — AI extracts key arguments, issues, case citations from uploaded docs
4. **Define Structure** — User reviews/edits brief outline (sections, headings)
5. **Draft Sections** — AI drafts each section using multi-pass approach with document context
6. **Guardrail Check** — Every draft passes through citation validation, hallucination detection
7. **Revise/Supplement** — User can request revisions or add research, AI re-drafts
8. **Generate DOCX** — Final formatted Word document with NYSCEF hyperlinks

---

## The Hyperlinker is a SEPARATE TOOL

The hyperlinker (`/hyperlinker` route) is a standalone tool within BriefDrafter. It has its own UI (`hyperlinker.html`), its own session management, and its own processing pipeline. It is NOT part of the drafting workflow — it's a utility tool that attorneys use independently to add hyperlinks to already-written briefs.

**Do not confuse the hyperlinker with the brief drafting system. They share a Flask app but are otherwise independent.**

---

## Dependencies

- **Flask** — web framework
- **anthropic** — Claude API client
- **python-docx** — DOCX generation
- **pdfplumber** — PDF text extraction
- **python-dotenv** — environment variable loading
- **PyMuPDF (fitz)** — used in hyperlinker for PDF manipulation (imported within functions)
- **PyPDF2** — used in hyperlinker for page label parsing (imported within functions)

---

## Environment

- Runs locally on port 5003
- Claude API key in `.env`
- Project data stored in `projects/` directory
- Mac-specific features (Word automation via osascript for PDF conversion)

---

## What NOT to Do

1. **NEVER run `git checkout`, `git reset --hard`, or any destructive git command.** Uncommitted work WILL be lost.
2. **NEVER modify hyperlinker.py without explicit instruction.** It was rebuilt from memory after being destroyed. Every line matters.
3. **NEVER modify guardrails.py casually.** It's 74K of carefully tuned anti-hallucination logic.
4. **NEVER modify prompt_builders.py casually.** The prompts are carefully engineered for legal accuracy.
5. **NEVER refactor or reorganize files.** The structure is intentional.
6. **NEVER add or remove dependencies** without asking first.
7. **NEVER make changes when asked a question.** Questions are questions, not instructions.
8. **If asked to handle a file (e.g., fix a PDF), do NOT edit application code.** Work on the file itself.

---

## Working Style

The developer is an appellate attorney who knows his codebase. He will tell you exactly what to change. Your job is to execute precisely what is asked — nothing more, nothing less. Do not suggest improvements, do not refactor adjacent code, do not add comments, do not reorganize imports. Touch only what you are told to touch.

If something is ambiguous, **ask before acting**. Getting it wrong costs hours of recovery time.
