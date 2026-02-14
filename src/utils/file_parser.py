"""
File Parser Utility
Extracts text from PDF, DOCX, and TXT files with page markers
"""

import pdfplumber
from pathlib import Path


def parse_file(file_path: str) -> str:
    path = Path(file_path)
    extension = path.suffix.lower()

    if extension == '.pdf':
        return parse_pdf(file_path)
    elif extension == '.docx':
        return parse_docx(file_path)
    elif extension == '.txt':
        return parse_txt(file_path)
    else:
        raise ValueError(f"Unsupported file type: {extension}")


def parse_pdf(file_path: str) -> str:
    """Extract text from PDF with page markers for chunking"""
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_parts.append(f"\n{'─' * 40} Page {i} {'─' * 40}\n")
                text_parts.append(page_text)
    return "\n".join(text_parts)


def parse_pdf_pages(file_path: str):
    """Extract text from PDF as a list of (page_number, text) tuples.

    The page_number is extracted from the transcript text itself (the number
    at the top of each page in court transcripts). Falls back to PDF page number
    if no transcript page number is found.
    """
    import re
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                # Try to extract transcript page number from top of page
                match = re.match(r'^\s*(\d+)\s*\n', text)
                tr_page = int(match.group(1)) if match else i
                pages.append((tr_page, text))
    return pages


def parse_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def parse_txt(file_path: str) -> str:
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()
