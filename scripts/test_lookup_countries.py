"""Quick smoke test: can the model use the lookup_countries tool?"""

import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import anthropic

client = anthropic.Anthropic()

COUNTRIES_DATA = json.loads(Path("countries.json").read_text())

TOOLS = [
    {
        "name": "lookup_countries",
        "description": (
            "Look up countries and regions. "
            "Query with a region key (e.g. 'asia_pacific'), "
            "a country name or alpha-3 code (e.g. 'Japan', 'JPN'), "
            "or 'all_regions' for the full region list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Region key, country name/code, or 'all_regions'"},
            },
            "required": ["query"],
        },
    },
]


def lookup_countries(query: str) -> str:
    q = query.strip().lower()
    if q == "all_regions":
        return json.dumps({"regions": COUNTRIES_DATA["regions"]})
    if q in COUNTRIES_DATA["regions"]:
        countries = [c for c in COUNTRIES_DATA["countries"] if c["region"] == q]
        return json.dumps({"region": q, "info": COUNTRIES_DATA["regions"][q], "countries": countries})
    for c in COUNTRIES_DATA["countries"]:
        if q == c["alpha3"].lower() or q == c["name"].lower():
            return json.dumps({"country": c, "region_info": COUNTRIES_DATA["regions"].get(c["region"])})
    matches = [c for c in COUNTRIES_DATA["countries"] if q in c["name"].lower()]
    if matches:
        return json.dumps({"matches": matches})
    return json.dumps({"error": f"No match for '{query}'"})


TESTS = [
    "What regions are available?",
    "Which countries are in the Middle East & North Africa region?",
    "What region is Brazil in?",
]

for test in TESTS:
    print(f"\n{'='*60}")
    print(f"USER: {test}")
    messages = [{"role": "user", "content": test}]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        tools=TOOLS,
        messages=messages,
    )

    # Handle tool use loop
    while response.stop_reason != "end_turn":
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"  TOOL: lookup_countries({block.input['query']})")
                result = lookup_countries(block.input["query"])
                parsed = json.loads(result)
                # Print a summary, not the full blob
                if "regions" in parsed:
                    print(f"  -> {len(parsed['regions'])} regions returned")
                elif "countries" in parsed:
                    print(f"  -> {len(parsed['countries'])} countries in {parsed['region']}")
                elif "country" in parsed:
                    print(f"  -> {parsed['country']['name']} is in {parsed['country']['region']}")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        if not tool_results:
            break
        messages.append({"role": "user", "content": tool_results})
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

    text = "\n".join(b.text for b in response.content if b.type == "text")
    print(f"  MODEL: {text[:200]}")

print(f"\n{'='*60}")
print("Done.")
