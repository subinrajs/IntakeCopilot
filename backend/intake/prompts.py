"""Versioned prompt files: prompts/<step>.<version>.md. The version is stored on every output."""

from functools import lru_cache

from intake.settings import get_settings


class UnknownPrompt(LookupError):
    pass


@lru_cache
def load_prompt(step: str, version: str) -> str:
    settings = get_settings()
    path = settings.prompts_dir / f"{step}.{version}.md"
    if not path.is_file():
        raise UnknownPrompt(f"no prompt file {path.name}")
    text = path.read_text()
    if "{{rubric}}" in text:
        text = text.replace("{{rubric}}", (settings.data_dir / "rubric.md").read_text().strip())
    return text
