"""
BriefDrafter configuration: constants, brief-type config, loaders.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths \u2014 config.py lives in src/, so parent.parent is the BriefDrafter root
BASE_DIR = Path(__file__).resolve().parent.parent
PROTOCOLS_DIR = BASE_DIR / 'protocols'
CONFIG_PATH = BASE_DIR / 'config.json'
PROJECTS_DIR = BASE_DIR / 'projects'
PROJECTS_DIR.mkdir(exist_ok=True)
SUMMARIZER_JOBS_DIR = Path.home() / 'Library' / 'CloudStorage' / 'Dropbox' / 'Appeals' / \
    'Appellate and Motion Codes' / '1 Appellate Record Prompts' / 'jobs'


def _load_protocol(name):
    """Load a protocol .txt file from the protocols/ directory."""
    path = PROTOCOLS_DIR / name
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return ''


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


config = load_config()

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}

# Dropbox OAuth settings
DROPBOX_APP_KEY = config.get('dropbox_app_key', '')
DROPBOX_APP_SECRET = config.get('dropbox_app_secret', '')

# Max characters to include per document in prompts
# 200K token context window: ~560K chars for docs + prompt, minus ~60K for prompt/instructions
# leaves ~500K for documents. With thinking budget (10K) + response tokens (~16K),
# we need to stay well under. 300K chars ≈ 75K tokens — safe margin.
MAX_PRIMARY_CHARS = 150000
MAX_SECONDARY_CHARS = 75000
MAX_TOTAL_CHARS = 300000

_STOP_WORDS = frozenset([
    'the', 'a', 'an', 'is', 'was', 'were', 'are', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'must',
    'of', 'to', 'in', 'for', 'on', 'at', 'by', 'from', 'with', 'about',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
    'that', 'this', 'these', 'those', 'it', 'its', 'they', 'them', 'their',
    'he', 'she', 'his', 'her', 'him', 'we', 'us', 'our', 'you', 'your',
    'who', 'whom', 'which', 'what', 'where', 'when', 'how', 'why',
    'if', 'then', 'than', 'because', 'while', 'although', 'also',
    'each', 'every', 'all', 'any', 'few', 'more', 'most', 'some', 'such',
    'no', 'only', 'own', 'same', 'too', 'very', 'just',
    'draft', 'section', 'brief', 'argument', 'point', 'court',
])

MODELS = {
    'sonnet': 'claude-sonnet-4-20250514',
    'opus': 'claude-opus-4-20250514',
}

BRIEF_TYPE_CONFIG = {
    'appellant': {
        'label': "Appellant's Brief",
        'doc_title': 'BRIEF FOR APPELLANT',
        'signature_role': 'Attorney for Appellant',
        'output_filename': 'Appellants_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '\u270f\ufe0f'},
            {'key': 'lower_court_decision', 'label': 'Lower Court Decision', 'icon': '\U0001f4c4'},
            {'key': 'trial_transcript', 'label': 'Trial Transcript', 'icon': '\U0001f4c4'},
            {'key': 'record_vol_1', 'label': 'Record on Appeal Vol. 1', 'icon': '\U0001f4c1'},
            {'key': 'record_vol_2', 'label': 'Record on Appeal Vol. 2', 'icon': '\U0001f4c1'},
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix", 'icon': '\U0001f4d1'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '\U0001f4da'},
        ],
        'additional_uploads': [
            {'key': 'record_vol_3', 'label': 'Record Vol. 3'},
            {'key': 'record_vol_4', 'label': 'Record Vol. 4'},
            {'key': 'record_vol_5', 'label': 'Record Vol. 5'},
            {'key': 'memo_of_law', 'label': 'Memorandum of Law'},
            {'key': 'reply_affirmation', 'label': 'Reply Affirmation'},
            {'key': 'legal_research_2', 'label': 'Legal Research 2'},
            {'key': 'legal_research_3', 'label': 'Legal Research 3'},
            {'key': 'legal_research_4', 'label': 'Legal Research 4'},
            {'key': 'legal_research_5', 'label': 'Legal Research 5'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': 'Analyze for Appealable Errors',
        'draft_button': "Draft Appellant's Brief",
        'analyze_loading': 'Analyzing lower court decision for errors...',
        'draft_loading': "Drafting appellant's brief...",
    },
    'respondent': {
        'label': "Respondent's Brief",
        'doc_title': 'BRIEF FOR RESPONDENT',
        'signature_role': 'Attorney for Respondent',
        'output_filename': 'Respondents_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '\u270f\ufe0f'},
            {'key': 'appellant_brief', 'label': "Appellant's Opening Brief", 'icon': '\U0001f4c4'},
            {'key': 'lower_court_decision', 'label': 'Lower Court Decision', 'icon': '\U0001f4c4'},
            {'key': 'record_vol_1', 'label': 'Record on Appeal Vol. 1', 'icon': '\U0001f4c1'},
            {'key': 'record_vol_2', 'label': 'Record on Appeal Vol. 2', 'icon': '\U0001f4c1'},
            {'key': 'respondent_appendix', 'label': "Respondent's Appendix", 'icon': '\U0001f4d1'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '\U0001f4da'},
        ],
        'additional_uploads': [
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix"},
            {'key': 'legal_research_2', 'label': 'Legal Research 2'},
            {'key': 'legal_research_3', 'label': 'Legal Research 3'},
            {'key': 'legal_research_4', 'label': 'Legal Research 4'},
            {'key': 'legal_research_5', 'label': 'Legal Research 5'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': "Analyze Appellant's Brief for Weaknesses",
        'draft_button': "Draft Respondent's Brief",
        'analyze_loading': "Analyzing appellant's brief for weaknesses...",
        'draft_loading': "Drafting respondent's brief...",
    },
    'reply': {
        'label': 'Reply Brief',
        'doc_title': 'REPLY BRIEF FOR APPELLANT',
        'signature_role': 'Attorney for Appellant',
        'output_filename': 'Reply_Brief.docx',
        'primary_uploads': [
            {'key': 'existing_draft', 'label': 'Existing Draft (Your Work-in-Progress)', 'icon': '\u270f\ufe0f'},
            {'key': 'opening_brief', 'label': 'Opening Brief (Your Brief)', 'icon': '\U0001f4c4'},
            {'key': 'respondent_brief', 'label': "Respondent's Brief", 'icon': '\U0001f4c4'},
            {'key': 'record_vol_1', 'label': 'Record on Appeal Vol. 1', 'icon': '\U0001f4c1'},
            {'key': 'record_vol_2', 'label': 'Record on Appeal Vol. 2', 'icon': '\U0001f4c1'},
            {'key': 'appellant_appendix', 'label': "Appellant's Appendix", 'icon': '\U0001f4d1'},
            {'key': 'legal_research', 'label': 'Legal Research', 'icon': '\U0001f4da'},
        ],
        'additional_uploads': [
            {'key': 'respondent_appendix', 'label': "Respondent's Appendix"},
            {'key': 'trial_transcript', 'label': 'Trial Transcript'},
            {'key': 'other', 'label': 'Other Document'},
        ],
        'analyze_button': 'Analyze Both Briefs',
        'draft_button': 'Draft Entire Reply Brief',
        'analyze_loading': 'Analyzing arguments...',
        'draft_loading': 'Drafting entire reply brief...',
    },
}
