import anthropic
import json
import math
import sys
import time
from pathlib import Path

client = anthropic.Anthropic()
SYSTEM_PROMPT = Path("prompt.md").read_text()
MODEL = "claude-haiku-4-5-20251001"

TOOLS = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 3,
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

def calculate(expression: str, label: str) -> str:
    allowed = {"math": math, "abs": abs, "round": round, "min": min, "max": max}
    result = eval(expression, {"__builtins__": {}}, allowed)
    return json.dumps({"label": label, "expression": expression, "result": result})


def log_assumption(claim: str, basis: str, confidence: str) -> str:
    return json.dumps({"assumption": claim, "basis": basis, "confidence": confidence})


DISPATCH = {
    "calculate": lambda inp: calculate(inp["expression"], inp["label"]),
    "log_assumption": lambda inp: log_assumption(inp["claim"], inp["basis"], inp.get("confidence", "moderate")),
}


# --- Agent loop ---

def run(query: str) -> str:
    messages = [{"role": "user", "content": query}]

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
            return "\n".join(b.text for b in response.content if b.type == "text")

        tool_results = []
        for block in response.content:
            if block.type == "web_search_tool_result":
                print(f"  -> web_search")
            elif block.type == "tool_use":
                print(f"  -> {block.name}({json.dumps(block.input, separators=(',', ':'))[:80]})")
                result = DISPATCH[block.name](block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Market to size: ")
    print(run(query))
