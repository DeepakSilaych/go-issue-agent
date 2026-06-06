"""
BM25-based retrieval over symbol index and PR examples.
No external dependencies — pure Python.
"""

import math
import re
from collections import Counter


# ---------------------------------------------------------------------------
# BM25 keyword search
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def bm25_search(
    query: str,
    documents: list[dict],
    text_fields: list[str],
    top_k: int = 10,
    K1: float = 1.5,
    B: float = 0.75,
) -> list[dict]:
    """
    BM25 search over a list of dicts.
    text_fields: list of dict keys to search over (concatenated).
    """
    if not documents:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return documents[:top_k]

    def doc_text(d: dict) -> str:
        return " ".join(str(d.get(f, "")) for f in text_fields)

    doc_tokens = [_tokenize(doc_text(d)) for d in documents]
    doc_lengths = [len(t) for t in doc_tokens]
    avg_len = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1
    N = len(documents)

    # IDF
    idf: dict[str, float] = {}
    for term in set(query_tokens):
        df = sum(1 for tokens in doc_tokens if term in tokens)
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    # Score
    scored = []
    for i, (tokens, length) in enumerate(zip(doc_tokens, doc_lengths)):
        tf_counts = Counter(tokens)
        score = sum(
            idf.get(t, 0) * tf_counts.get(t, 0) * (K1 + 1)
            / (tf_counts.get(t, 0) + K1 * (1 - B + B * length / avg_len))
            for t in query_tokens
        )
        if score > 0:
            scored.append((score, i))

    scored.sort(reverse=True)
    return [documents[i] for _, i in scored[:top_k]]


# ---------------------------------------------------------------------------
# Symbol-level retrieval
# ---------------------------------------------------------------------------

def retrieve_symbols(query: str, symbol_index: list[dict], top_k: int = 20) -> list[dict]:
    """Find relevant symbols from the index for a given issue query."""
    return bm25_search(
        query,
        symbol_index,
        text_fields=["name", "signature", "file", "comment"],
        top_k=top_k,
    )


def format_symbols_for_prompt(symbols: list[dict]) -> str:
    """Format retrieved symbols as a compact prompt section."""
    if not symbols:
        return "(no symbols retrieved)"

    lines = []
    by_file: dict[str, list[dict]] = {}
    for s in symbols:
        by_file.setdefault(s["file"], []).append(s)

    for file, syms in sorted(by_file.items()):
        lines.append(f"\n**{file}**")
        for s in syms:
            sig = s.get("signature", "")[:100]
            comment = s.get("comment", "")
            if comment:
                lines.append(f"  L{s['line']:4d}  {sig}  // {comment[:60]}")
            else:
                lines.append(f"  L{s['line']:4d}  {sig}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PR example retrieval
# ---------------------------------------------------------------------------

def retrieve_similar_prs(issue_title: str, issue_body: str, pr_examples: list[dict], top_k: int = 3) -> list[dict]:
    """Find similar past PRs by keyword matching on title + issue description."""
    query = f"{issue_title} {issue_body}"
    return bm25_search(
        query,
        pr_examples,
        text_fields=["issue_title", "issue_body", "pr_title", "pr_body", "diff_summary"],
        top_k=top_k,
    )


def format_prs_for_prompt(prs: list[dict]) -> str:
    """Format similar PRs as context for the LLM."""
    if not prs:
        return "(no similar PRs found)"

    lines = []
    for i, pr in enumerate(prs, 1):
        lines.append(f"\n### Example {i}: {pr.get('pr_title', 'PR')}")
        if pr.get("issue_title"):
            lines.append(f"Issue: {pr['issue_title']}")
        if pr.get("pr_body"):
            lines.append(pr["pr_body"][:300])
        if pr.get("diff_summary"):
            lines.append(f"\nKey changes:\n{pr['diff_summary'][:500]}")

    return "\n".join(lines)
