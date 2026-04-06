"""
BriefDrafter project I/O: file extraction, project load/save.
"""

import json
import fcntl
import re
from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument

from src.config import PROJECTS_DIR, ALLOWED_EXTENSIONS


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _parse_record_start_page(file_path: Path):
    """Extract the starting record page number from a record volume filename.

    Matches patterns like 'p._1-632' or 'p._633-1170' in the filename
    and returns the starting page number (e.g., 1 or 633).
    Returns None if the file is not a record volume.
    """
    name = file_path.stem  # filename without extension
    if not name.startswith('record_vol_'):
        return None
    m = re.search(r'p[._]+(\d+)\s*-\s*\d+', name)
    if m:
        return int(m.group(1))
    return None


def extract_text(file_path: Path) -> str:
    """Extract text from PDF, DOCX, or TXT file.

    For record volume PDFs, page numbers are detected from the printed
    page number at the top of each page. If the first line is not numeric,
    falls back to a filename-derived calculation for record volumes
    (using the page range in the filename, e.g. p._1-632), or sequential
    numbering for other PDFs.
    """
    ext = file_path.suffix.lower()

    if ext == '.pdf':
        text_parts = []
        record_start = _parse_record_start_page(file_path)
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        # Try to detect printed page number from first line
                        first_line = page_text.strip().split('\n')[0].strip()
                        if first_line.isdigit():
                            page_label = first_line
                        elif record_start is not None:
                            # Record volume fallback: calculate from filename
                            page_label = str(record_start + (i - 1))
                        else:
                            page_label = str(i)
                        text_parts.append(f"--- PAGE {page_label} ---\n{page_text}")
        except Exception as e:
            return f"Error reading PDF: {e}"
        return "\n\n".join(text_parts)

    elif ext == '.docx':
        try:
            doc = DocxDocument(str(file_path))
            return '\n\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
        except Exception as e:
            return f"Error reading DOCX: {e}"

    else:  # .txt
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"


def get_project(project_id: str) -> dict:
    """Load project data with backward-compat migration"""
    project_file = PROJECTS_DIR / project_id / 'project.json'
    if project_file.exists():
        with open(project_file, 'r') as f:
            data = json.load(f)
        # Migrate legacy projects that lack brief_type
        if 'brief_type' not in data:
            data['brief_type'] = 'reply'
            data['representing'] = 'appellant'
            save_project(project_id, data)
        return data
    return None


def save_project(project_id: str, data: dict):
    """Save project data with file locking to prevent race conditions"""
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(exist_ok=True)
    lock_file = project_dir / '.project.lock'
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(project_dir / 'project.json', 'w') as f:
                json.dump(data, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
