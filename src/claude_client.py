"""
BriefDrafter Claude API client: call_claude and call_claude_with_docs.
"""

import os
import time
from anthropic import Anthropic, AuthenticationError, RateLimitError, APITimeoutError, APIStatusError

from src.config import MODELS

# Extended thinking budget — forces high-effort reasoning on every API call
THINKING_BUDGET = 10_000


def call_claude(prompt: str, max_tokens: int = 4000, model: str = 'sonnet', system: str = None) -> str:
    """Call Claude API with streaming + exponential backoff retry.

    Retries on rate limits (30s/60s/90s), overload 529 (30s/60s/90s),
    timeouts (2s/4s/8s), and generic errors (2s/4s/8s).
    Fails immediately on authentication errors.
    """
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY not set in .env file"

    model_id = MODELS.get(model, MODELS['sonnet'])
    client = Anthropic(api_key=api_key)
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[API] Calling {model_id}, max_tokens={max_tokens}, prompt_len={len(prompt)}", flush=True)

            kwargs = {
                'model': model_id,
                'max_tokens': min(max_tokens + THINKING_BUDGET * 4, 32000 if 'opus' in model_id else 64000),
                'messages': [{"role": "user", "content": prompt}],
                'thinking': {'type': 'enabled', 'budget_tokens': THINKING_BUDGET},
            }
            if system:
                kwargs['system'] = system

            with client.messages.stream(**kwargs) as stream:
                result = stream.get_final_text()

            print(f"[API] Response received, length={len(result)}", flush=True)
            return result

        except AuthenticationError:
            print("[API ERROR] Authentication failed, check ANTHROPIC_API_KEY", flush=True)
            return "ERROR: Authentication failed — check ANTHROPIC_API_KEY"

        except RateLimitError:
            backoff = 30 * attempt
            if attempt < max_retries:
                print(f"[API] Retry {attempt}/{max_retries} after RateLimitError, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] RateLimitError after {max_retries} retries", flush=True)
                return "ERROR: Rate limited after multiple retries. Wait a minute and try again."

        except APIStatusError as e:
            if e.status_code == 529:
                backoff = 30 * attempt
                if attempt < max_retries:
                    print(f"[API] Retry {attempt}/{max_retries} after overload (529), waiting {backoff}s...", flush=True)
                    time.sleep(backoff)
                else:
                    print(f"[API ERROR] Overloaded (529) after {max_retries} retries", flush=True)
                    return "ERROR: API overloaded after multiple retries. Wait a minute and try again."
            else:
                print(f"[API ERROR] APIStatusError {e.status_code}: {e}", flush=True)
                return f"ERROR: API error {e.status_code}: {str(e)}"

        except APITimeoutError:
            backoff = 2 ** attempt
            if attempt < max_retries:
                print(f"[API] Retry {attempt}/{max_retries} after timeout, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] Timeout after {max_retries} retries", flush=True)
                return "ERROR: API timed out after multiple retries."

        except Exception as e:
            backoff = 2 ** attempt
            if attempt < max_retries:
                print(f"[API] Retry {attempt}/{max_retries} after {type(e).__name__}: {e}, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] {type(e).__name__} after {max_retries} retries: {e}", flush=True)
                return f"ERROR: {str(e)}"


def call_claude_with_docs(prompt: str, documents: list, max_tokens: int = 8000, model: str = 'sonnet', system: str = None):
    """Call Claude with source documents for automatic citation tracking.

    Uses Anthropic Citations API: documents are passed as structured content blocks
    so Claude can cite exact source passages. Same retry logic as call_claude().

    Args:
        prompt: Instructions/question (text-only, no embedded documents).
        documents: [{"text": "...", "title": "..."}] — source docs for citation.
        max_tokens, model, system: Same as call_claude.

    Returns:
        (response_text, citations)
        citations: [{"cited_text", "document_title", "document_index", "start", "end"}]
    """
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY not set in .env file", []

    model_id = MODELS.get(model, MODELS['sonnet'])
    client = Anthropic(api_key=api_key)
    max_retries = 3

    # Guard: if total content exceeds ~700K chars (~175K tokens), fall back to call_claude
    # to avoid hitting the 200K token context window limit
    total_doc_chars = sum(len(d.get("text", "")) for d in documents)
    total_chars = total_doc_chars + len(prompt)
    if total_chars > 550000:
        print(f"[API+CITE] Total content {total_chars} chars exceeds 550K limit, falling back to call_claude()", flush=True)
        # Reconstruct the full prompt with documents inline
        inline_docs = "\n\n".join(f"=== {d.get('title', 'Document')} ===\n{d['text']}" for d in documents)
        full_prompt = f"{inline_docs}\n\n{prompt}"
        return call_claude(full_prompt, max_tokens=max_tokens, model=model, system=system), []

    # Build structured content: document blocks + text prompt
    content = []
    for doc in documents:
        content.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": doc["text"]},
            "title": doc.get("title", "Source Document"),
            "citations": {"enabled": True},
        })
    content.append({"type": "text", "text": prompt})

    for attempt in range(1, max_retries + 1):
        try:
            doc_titles = [d.get('title', '?') for d in documents]
            print(f"[API+CITE] Calling {model_id}, max_tokens={max_tokens}, docs={doc_titles}, prompt_len={len(prompt)}", flush=True)

            kwargs = {
                'model': model_id,
                'max_tokens': min(max_tokens + THINKING_BUDGET * 4, 32000 if 'opus' in model_id else 64000),
                'messages': [{"role": "user", "content": content}],
                'thinking': {'type': 'enabled', 'budget_tokens': THINKING_BUDGET},
            }
            if system:
                kwargs['system'] = system

            with client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()

            # Extract text and citations from response
            text_parts = []
            all_citations = []
            for block in message.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    for cite in getattr(block, 'citations', None) or []:
                        all_citations.append({
                            "cited_text": getattr(cite, 'cited_text', ''),
                            "document_title": getattr(cite, 'document_title', ''),
                            "document_index": getattr(cite, 'document_index', 0),
                            "start": getattr(cite, 'start_char_index', 0),
                            "end": getattr(cite, 'end_char_index', 0),
                        })

            result = "".join(text_parts)
            print(f"[API+CITE] Response received, length={len(result)}, citations={len(all_citations)}", flush=True)
            return result, all_citations

        except AuthenticationError:
            print("[API ERROR] Authentication failed, check ANTHROPIC_API_KEY", flush=True)
            return "ERROR: Authentication failed — check ANTHROPIC_API_KEY", []

        except RateLimitError:
            backoff = 30 * attempt
            if attempt < max_retries:
                print(f"[API+CITE] Retry {attempt}/{max_retries} after RateLimitError, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] RateLimitError after {max_retries} retries", flush=True)
                return "ERROR: Rate limited after multiple retries. Wait a minute and try again.", []

        except APIStatusError as e:
            if e.status_code == 529:
                backoff = 30 * attempt
                if attempt < max_retries:
                    print(f"[API+CITE] Retry {attempt}/{max_retries} after overload (529), waiting {backoff}s...", flush=True)
                    time.sleep(backoff)
                else:
                    print(f"[API ERROR] Overloaded (529) after {max_retries} retries", flush=True)
                    return "ERROR: API overloaded after multiple retries. Wait a minute and try again.", []
            else:
                print(f"[API ERROR] APIStatusError {e.status_code}: {e}", flush=True)
                return f"ERROR: API error {e.status_code}: {str(e)}", []

        except APITimeoutError:
            backoff = 2 ** attempt
            if attempt < max_retries:
                print(f"[API+CITE] Retry {attempt}/{max_retries} after timeout, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] Timeout after {max_retries} retries", flush=True)
                return "ERROR: API timed out after multiple retries.", []

        except Exception as e:
            backoff = 2 ** attempt
            if attempt < max_retries:
                print(f"[API+CITE] Retry {attempt}/{max_retries} after {type(e).__name__}: {e}, waiting {backoff}s...", flush=True)
                time.sleep(backoff)
            else:
                print(f"[API ERROR] {type(e).__name__} after {max_retries} retries: {e}", flush=True)
                return f"ERROR: {str(e)}", []
