from __future__ import annotations


def build_answer(prompt: str) -> str:
    normalized = prompt.strip()
    if not normalized:
        return "No prompt provided."
    return f"Answer: {normalized}"


class SessionReporter:
    def report(self, prompt: str) -> str:
        return build_answer(prompt)
