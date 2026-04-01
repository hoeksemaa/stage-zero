import anthropic

try:
    r = anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1,
        messages=[{"role": "user", "content": "hi"}],
    )
    print("Key works.")
except Exception as e:
    print(f"Failed: {e}")
