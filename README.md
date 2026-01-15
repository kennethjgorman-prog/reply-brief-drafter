# Reply Brief Drafter

An AI-powered tool for drafting appellate reply briefs. Upload your opening brief, respondent's brief, and record on appeal, and the tool will draft a comprehensive reply brief with proper citations.

## Features

- **Multi-pass AI drafting**: Uses 5 separate AI passes to extract research and draft the brief
  - Pass 1: Extract cases from respondent's brief
  - Pass 2: Extract cases from appellant's brief
  - Pass 3: Extract key record evidence
  - Pass 4: Extract key transcript quotes with page numbers
  - Pass 5: Draft the complete reply brief

- **Citation guardrails**: Only cites cases and facts from uploaded documents - no hallucinated citations

- **Proper citation formats**:
  - Record: `(125)`
  - Appellant's Appendix: `(A. 45)`
  - Respondent's Appendix: `(RA. 12)`
  - Case citations with pinpoint pages: `(125 A.D.3d at 499)`

- **Flexible document uploads**: Support for multiple record volumes, appendices, and legal research

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/reply-brief-drafter.git
cd reply-brief-drafter
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file with your Anthropic API key:
```
ANTHROPIC_API_KEY=your-api-key-here
```

4. Run the application:
```bash
python app.py
```

5. Open your browser to `http://127.0.0.1:5003`

## Usage

1. Create a new project with case information
2. Upload required documents:
   - Opening Brief (your appellant's brief)
   - Respondent's Brief
   - Appellant's Appendix
3. Optionally upload:
   - Record volumes (1-5)
   - Respondent's Appendix
   - Legal Research
4. Click "Draft Entire Reply Brief"
5. Download the generated Word document

## Requirements

- Python 3.8+
- Anthropic API key (Claude)
- See `requirements.txt` for Python dependencies

## License

MIT License
