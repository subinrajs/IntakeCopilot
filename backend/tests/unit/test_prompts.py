import pytest

from intake.prompts import UnknownPrompt, load_prompt
from intake.settings import get_settings

PROMPTS = sorted(p.stem for p in get_settings().prompts_dir.glob("*.md"))


@pytest.mark.parametrize("name", PROMPTS)
def test_every_prompt_loads_and_guards_against_injected_instructions(name: str) -> None:
    step, _, version = name.partition(".")
    text = load_prompt(step, version)
    assert "{{" not in text  # all placeholders filled
    assert "instructions" in text.lower() and "ignore" in text.lower()


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_triage_prompts_embed_the_rubric_file(version: str) -> None:
    rubric = (get_settings().data_dir / "rubric.md").read_text().strip()
    assert rubric in load_prompt("triage", version)


def test_live_prompt_versions_exist() -> None:
    for step, version in get_settings().prompt_versions.items():
        load_prompt(step, version)


def test_unknown_prompt_version_is_an_error() -> None:
    with pytest.raises(UnknownPrompt):
        load_prompt("triage", "v99")
