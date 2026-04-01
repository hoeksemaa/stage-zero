import anthropic
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from ddgs import DDGS
import trafilatura
import requests

client = anthropic.Anthropic()
SYSTEM_PROMPT = Path("prompt.md").read_text()
MODEL = "claude-haiku-4-5-20251001"
MAX_FETCH_CHARS = 5000

TOOLS = [
    {
        "name": "search",
        "description": (
            "Search the web. Returns a list of titles, URLs, and snippets. "
            "Does NOT return full page content — use fetch_url to read a page. "
            "Use specific, targeted queries (e.g. 'ICD-10 06183J4 annual volume HCUP NIS')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch and extract readable text from a URL. "
            "Use this after search to read a specific page. "
            f"Returns up to {MAX_FETCH_CHARS} characters of extracted text. "
            "You MUST provide a reason explaining how you found this URL "
            "(e.g. 'from search results for X', 'linked from page Y', 'known from training data')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "reason": {
                    "type": "string",
                    "description": (
                        "How you found this URL. Examples: "
                        "'Result #3 from search for ICD-10 TIPS volume', "
                        "'Linked from https://hcup-us.ahrq.gov/nisoverview.jsp', "
                        "'Known URL from training data — AHRQ HCUP documentation page'"
                    ),
                },
            },
            "required": ["url", "reason"],
        },
    },
    {
        "name": "calculate",
        "description": "Evaluate a math expression. Use for ALL arithmetic — never compute in your head.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Python math expression, e.g. '5280 * 45600'"},
                "label": {"type": "string", "description": "What this calculation represents"},
            },
            "required": ["expression", "label"],
        },
    },
    {
        "name": "log_assumption",
        "description": "Log an assumption. Use whenever you introduce a number that is not directly sourced.",
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The assumption being made"},
                "basis": {"type": "string", "description": "Why you believe this is reasonable"},
                "confidence": {"type": "string", "enum": ["high", "moderate", "low", "speculative"]},
            },
            "required": ["claim", "basis", "confidence"],
        },
    },
]


# --- Tool implementations ---

def search(query: str) -> str:
    try:
        results = DDGS().text(query, max_results=10)
    except Exception as e:
        return json.dumps({"error": str(e), "results": []})
    out = []
    for r in results:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        })
    return json.dumps({"query": query, "results": out})


def fetch_url(url: str) -> str:
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (research agent)"
        })
        resp.raise_for_status()
    except Exception as e:
        return json.dumps({"error": f"Fetch failed: {e}", "url": url})

    text = trafilatura.extract(resp.text, include_tables=True, include_links=True)
    if not text:
        text = resp.text[:MAX_FETCH_CHARS]
        source = "raw_html"
    else:
        source = "trafilatura"

    truncated = len(text) > MAX_FETCH_CHARS
    text = text[:MAX_FETCH_CHARS]
    return json.dumps({
        "url": url,
        "content": text,
        "chars": len(text),
        "truncated": truncated,
        "extraction": source,
    })


def calculate(expression: str, label: str) -> str:
    allowed = {"math": math, "abs": abs, "round": round, "min": min, "max": max}
    result = eval(expression, {"__builtins__": {}}, allowed)
    return json.dumps({"label": label, "expression": expression, "result": result})


def log_assumption(claim: str, basis: str, confidence: str) -> str:
    return json.dumps({"assumption": claim, "basis": basis, "confidence": confidence})


DISPATCH = {
    "search": lambda inp: search(inp["query"]),
    "fetch_url": lambda inp: fetch_url(inp["url"]),
    "calculate": lambda inp: calculate(inp["expression"], inp["label"]),
    "log_assumption": lambda inp: log_assumption(inp["claim"], inp["basis"], inp.get("confidence", "moderate")),
}


# --- Audit log ---

class AuditLog:
    def __init__(self, query: str):
        self.query = query
        self.started = datetime.now()
        self.searches = []       # [{query, results: [{title, url, snippet}]}]
        self.fetches = []        # [{url, reason, attribution, chars, error}]
        self.assumptions = []    # [{claim, basis, confidence}]
        self.calculations = []   # [{expression, label, result}]

    def log_search(self, query: str, results: list):
        self.searches.append({"query": query, "results": results})

    def log_fetch(self, url: str, reason: str, chars: int = 0, error: str = None):
        attribution = self._attribute(url, reason)
        self.fetches.append({
            "url": url,
            "reason": reason,
            "attribution": attribution,
            "chars": chars,
            "error": error,
        })

    def log_assumption(self, claim: str, basis: str, confidence: str):
        self.assumptions.append({"claim": claim, "basis": basis, "confidence": confidence})

    def log_calculation(self, expression: str, label: str, result):
        self.calculations.append({"expression": expression, "label": label, "result": result})

    def _attribute(self, url: str, reason: str) -> str:
        """Determine provenance: which search or fetch led to this URL."""
        # Check if URL appeared in any search results
        for s in self.searches:
            for r in s["results"]:
                if r["url"] == url or url.startswith(r["url"]) or r["url"].startswith(url):
                    return f"search: \"{s['query']}\""
        # Check if URL appeared in a previously fetched page's content
        for f in self.fetches:
            if f.get("url") != url and not f.get("error"):
                # We can't check content here (it's not stored), so rely on Claude's reason
                pass
        # Fall back to Claude's stated reason
        reason_lower = reason.lower()
        if "training data" in reason_lower or "known" in reason_lower:
            return "training_data"
        if "link" in reason_lower or "found on" in reason_lower or "linked from" in reason_lower:
            return f"discovered_link: {reason}"
        return f"claude_stated: {reason}"

    def write(self):
        ts = self.started.strftime("%Y%m%d_%H%M%S")
        slug = self.query[:50].replace(" ", "_").replace("/", "-")
        path = Path("audits") / f"{ts}_{slug}.md"
        path.parent.mkdir(exist_ok=True)

        lines = []
        lines.append(f"# Audit: {self.query}")
        lines.append(f"**Date:** {self.started.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Model:** {MODEL}")
        lines.append("")

        # --- Search tree ---
        lines.append("## Research Trail")
        lines.append("")
        for i, s in enumerate(self.searches, 1):
            lines.append(f"### Search {i}: `{s['query']}`")
            lines.append("")
            lines.append("| # | Title | URL |")
            lines.append("|---|-------|-----|")
            for j, r in enumerate(s["results"], 1):
                lines.append(f"| {j} | {r['title']} | {r['url']} |")
            lines.append("")

            # Show fetches attributed to this search
            related = [f for f in self.fetches if f["attribution"] == f'search: "{s["query"]}"']
            if related:
                lines.append("**Fetched from this search:**")
                lines.append("")
                for f in related:
                    status = f"({f['chars']} chars)" if not f["error"] else f"ERROR: {f['error']}"
                    lines.append(f"- {f['url']} {status}")
                    lines.append(f"  - *Reason:* {f['reason']}")
                lines.append("")

        # --- Discovered links (found in fetched pages, not from search) ---
        discovered = [f for f in self.fetches if f["attribution"].startswith("discovered_link:")]
        if discovered:
            lines.append("### Discovered Links (found in fetched pages)")
            lines.append("")
            for f in discovered:
                status = f"({f['chars']} chars)" if not f["error"] else f"ERROR: {f['error']}"
                lines.append(f"- {f['url']} {status}")
                lines.append(f"  - *Reason:* {f['reason']}")
                lines.append(f"  - *Attribution:* {f['attribution']}")
            lines.append("")

        # --- Training data URLs ---
        training = [f for f in self.fetches if f["attribution"] == "training_data"]
        if training:
            lines.append("### From Training Data (Claude already knew the URL)")
            lines.append("")
            for f in training:
                status = f"({f['chars']} chars)" if not f["error"] else f"ERROR: {f['error']}"
                lines.append(f"- {f['url']} {status}")
                lines.append(f"  - *Reason:* {f['reason']}")
            lines.append("")

        # --- Other/unattributed ---
        other = [f for f in self.fetches if f["attribution"].startswith("claude_stated:")]
        if other:
            lines.append("### Other Fetches")
            lines.append("")
            for f in other:
                status = f"({f['chars']} chars)" if not f["error"] else f"ERROR: {f['error']}"
                lines.append(f"- {f['url']} {status}")
                lines.append(f"  - *Reason:* {f['reason']}")
            lines.append("")

        # --- Assumptions ---
        if self.assumptions:
            lines.append("## Assumptions Logged")
            lines.append("")
            lines.append("| Claim | Basis | Confidence |")
            lines.append("|-------|-------|------------|")
            for a in self.assumptions:
                lines.append(f"| {a['claim']} | {a['basis']} | {a['confidence']} |")
            lines.append("")

        # --- Calculations ---
        if self.calculations:
            lines.append("## Calculations")
            lines.append("")
            lines.append("| Label | Expression | Result |")
            lines.append("|-------|------------|--------|")
            for c in self.calculations:
                lines.append(f"| {c['label']} | `{c['expression']}` | {c['result']} |")
            lines.append("")

        # --- Summary stats ---
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Searches:** {len(self.searches)}")
        lines.append(f"- **Pages fetched:** {len(self.fetches)}")
        lines.append(f"- **Assumptions logged:** {len(self.assumptions)}")
        lines.append(f"- **Calculations:** {len(self.calculations)}")
        fetch_sources = {}
        for f in self.fetches:
            bucket = f["attribution"].split(":")[0]
            fetch_sources[bucket] = fetch_sources.get(bucket, 0) + 1
        if fetch_sources:
            lines.append(f"- **Fetch provenance:** {json.dumps(fetch_sources)}")
        lines.append("")

        path.write_text("\n".join(lines))
        return str(path)


# --- Agent loop ---

def run(query: str) -> str:
    messages = [{"role": "user", "content": query}]
    audit = AuditLog(query)

    while True:
        # Truncate tool_result content in older turns to cap context growth.
        for msg in messages[:-2]:
            if not isinstance(msg["content"], list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if isinstance(block.get("content"), str) and len(block["content"]) > 300:
                        block["content"] = block["content"][:300] + " [truncated]"

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError:
                if attempt == 2:
                    raise
                wait = 30 * (attempt + 1)
                print(f"  [rate limit, retrying in {wait}s]")
                time.sleep(wait)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            audit_path = audit.write()
            print(f"\n  [audit log written to {audit_path}]")
            return "\n".join(b.text for b in response.content if b.type == "text")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            # Log the tool call
            if block.name == "search":
                print(f'  -> search("{block.input["query"]}")')
            elif block.name == "fetch_url":
                reason = block.input.get("reason", "no reason given")
                print(f'  -> fetch_url({block.input["url"]})')
                print(f'     reason: {reason}')
            else:
                print(f"  -> {block.name}({json.dumps(block.input, separators=(',', ':'))[:80]})")

            result = DISPATCH[block.name](block.input)

            # Log to audit + console
            if block.name == "search":
                parsed = json.loads(result)
                results = parsed.get("results", [])
                print(f"     ({len(results)} results)")
                for i, r in enumerate(results, 1):
                    print(f"     {i}. {r['title']}")
                    print(f"        {r['url']}")
                audit.log_search(parsed.get("query", ""), results)

            elif block.name == "fetch_url":
                parsed = json.loads(result)
                reason = block.input.get("reason", "no reason given")
                if "error" in parsed:
                    print(f"     ERROR: {parsed['error']}")
                    audit.log_fetch(block.input["url"], reason, error=parsed["error"])
                else:
                    print(f"     ({parsed['chars']} chars, {parsed['extraction']}, truncated={parsed['truncated']})")
                    audit.log_fetch(block.input["url"], reason, chars=parsed["chars"])

            elif block.name == "log_assumption":
                parsed = json.loads(result)
                audit.log_assumption(parsed["assumption"], parsed["basis"], parsed["confidence"])

            elif block.name == "calculate":
                parsed = json.loads(result)
                audit.log_calculation(parsed["expression"], parsed["label"], parsed["result"])

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Market to size: ")
    print(run(query))
