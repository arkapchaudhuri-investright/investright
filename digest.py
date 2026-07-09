"""Nightly AI digest — the one external call that isn't market data (§2, §4).

Turns the rule-based screener's top picks into a short plain-English note via a
free-tier LLM: Gemini if GEMINI_API_KEY is set, else Groq via GROQ_API_KEY —
never Claude (§1, locked). It's an external dependency like Yahoo or EDGAR, so
the same discipline applies (§8.0): generate() raises on any failure and the
caller keeps the last saved digest rather than breaking the page. The AI only
summarizes our own arithmetic — the ranking itself has no AI in it.

Keys live in .env next to this file (kept out of code; gitignore it if this
ever becomes a repo):
    GEMINI_API_KEY=...   # free at https://aistudio.google.com/apikey
    GROQ_API_KEY=...     # free at https://console.groq.com/keys
"""
import os
from pathlib import Path

import requests

ENV_PATH = Path(__file__).parent / ".env"
TIMEOUT = 45


def _load_env():
    """Tiny KEY=value reader — real environment always wins, no new dependency."""
    try:
        lines = ENV_PATH.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_env()

# Free-tier models; override via env if either provider renames them.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


def provider():
    """('gemini'|'groq', key) for whichever free API has a key, else None."""
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        return "groq", os.environ["GROQ_API_KEY"]
    return None


def build_prompt(picks, today_label):
    """picks: [{ticker, name, score, upside_pct, reasons: [str], news: [str]}]."""
    lines = []
    for p in picks:
        why = "; ".join(p.get("reasons") or []) or "no single standout signal"
        line = f"- {p['ticker']} ({p['name']}) — score {p['score']}/100."
        if p.get("upside_pct") is not None:
            line += f" DCF gap {p['upside_pct']:+.0f}% (historical-trend estimate)."
        line += f" Signals: {why}."
        if p.get("news"):
            line += " Recent headlines: " + " | ".join(p["news"][:2])
        lines.append(line)
    picks_block = "\n".join(lines)
    return f"""You write a short nightly note for InvestRight, a personal stock-research \
dashboard. Below is tonight's output from its transparent rule-based screener \
(fundamental pass/fail checks, a DCF built from historical trends, SEC insider \
filings, dividend records), best-ranked first.

{picks_block}

Write the note for {today_label}:
- One overview paragraph, 2–3 sentences, on what stands out tonight and why.
- Then one line per ticker, in the order given, formatted exactly as
  "TICKER — one plain-English sentence on why it ranks where it does."
- Under 180 words total. Calm and factual, sentence case, no hype, no emojis,
  no markdown headings.
- Use only the data above. The DCF gap is arithmetic on past growth, not an
  analyst target — hedge it accordingly, especially the very large gaps.
- No advice: never say buy, sell, hold, or should."""


def generate(picks, today_label):
    """(body, model) from whichever free API has a key. Raises on any failure —
    missing key, quota, network, empty answer — so the caller can keep last-good."""
    prov = provider()
    if not prov:
        raise RuntimeError("no GEMINI_API_KEY or GROQ_API_KEY in .env or environment")
    name, key = prov
    prompt = build_prompt(picks, today_label)
    return _gemini(prompt, key) if name == "gemini" else _groq(prompt, key)


def _ask_prompt(context, question):
    """Ground Otto's answer in the stock's saved metrics (the `context` block).
    Same honesty rules as the digest (§1): explain the numbers, never advise."""
    return f"""You are Otto, a friendly, plain-spoken owl assistant on InvestRight, a \
personal stock-research dashboard. Answer the user's question about the stock \
using ONLY the data below — do not invent figures or use outside knowledge. If \
the data doesn't cover it, say so plainly.

Rules:
- Be concise: 2–4 short sentences, sentence case, calm, no hype, no emojis, no markdown.
- The DCF "fair value" is arithmetic on the company's own past growth, not an \
analyst target — hedge large gaps.
- Never tell the user to buy, sell, or hold, and never say what they "should" do. \
You explain what the numbers say. End nothing with advice.

DATA
{context}

QUESTION: {question}

Otto's answer:"""


def ask(context, question):
    """Answer one user question about a stock, grounded in `context`. Uses the
    same free provider as the digest (Gemini, else Groq); never Claude (§1).
    Returns the answer text. Raises on any failure so the caller can degrade."""
    prov = provider()
    if not prov:
        raise RuntimeError("no GEMINI_API_KEY or GROQ_API_KEY in .env or environment")
    name, key = prov
    prompt = _ask_prompt(context, question)
    body, _model = _gemini(prompt, key) if name == "gemini" else _groq(prompt, key)
    return body


def _gemini(prompt, key):
    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": key},   # header, not ?key= — keeps it out of logged URLs
        json={"contents": [{"parts": [{"text": prompt}]}],
              # room for 2.5's internal thinking tokens; the note itself is short
              "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048}},
        timeout=TIMEOUT)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    body = "\n".join(p["text"] for p in parts if p.get("text")).strip()
    if not body:
        raise RuntimeError("Gemini returned an empty candidate")
    return body, GEMINI_MODEL


def _groq(prompt, key):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": GROQ_MODEL, "temperature": 0.4, "max_tokens": 1024,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=TIMEOUT)
    r.raise_for_status()
    body = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not body:
        raise RuntimeError("Groq returned an empty message")
    return body, GROQ_MODEL
