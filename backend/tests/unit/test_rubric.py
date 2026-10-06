import re

from intake.settings import get_settings


def test_rubric_defines_four_levels_each_with_three_or_more_examples() -> None:
    rubric = (get_settings().data_dir / "rubric.md").read_text()
    for level in ("P1", "P2", "P3", "P4"):
        section = re.search(rf"\*\*{level}\*\*\n((?:- .+\n)+)", rubric)
        assert section, f"no example list for {level}"
        assert len(section.group(1).splitlines()) >= 3


def test_rubric_states_the_more_urgent_tie_break() -> None:
    rubric = (get_settings().data_dir / "rubric.md").read_text()
    assert "choose the more urgent" in rubric
