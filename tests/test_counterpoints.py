"""Unit tests: schema validation, config bounds, and export rendering.

No LLM calls — generation is exercised end-to-end by the host's integration
tests; here we lock down the data contract and the derived exports.
"""

from __future__ import annotations

import pytest
from open_notebook_creator_sdk.schemas import CounterpointsV1, validate_artifact_data
from pydantic import ValidationError

from counterpoints_creator import CounterpointsConfig, CounterpointsCreator
from counterpoints_creator.export import (
    build_export_files,
    render_markdown,
    render_qmd,
    slugify,
)

SAMPLE = {
    "title": "Remote Work Debates",
    "description": "Two contested questions from the research.",
    "issues": [
        {
            "question": "Should the company go fully remote?",
            "context": "The sources disagree on productivity effects.",
            "side_a": "Remote first",
            "side_b": "Office first",
            "pairs": [
                {
                    "point": "Remote widens the hiring pool.",
                    "point_evidence": "Study A found 3x applicant volume.",
                    "counterpoint": "Onboarding quality drops without in-person ramp-up.",
                    "counterpoint_evidence": "Study B shows slower time-to-productivity.",
                    "response": "Structured async onboarding closes most of the gap.",
                },
                {
                    "point": "Real-estate costs fall sharply.",
                    "counterpoint": "Savings are offset by travel and off-site budgets.",
                },
            ],
            "synthesis": "The crux is whether mentoring can be replicated remotely.",
        }
    ],
}


def test_schema_roundtrip():
    model = validate_artifact_data("counterpoints.v1", SAMPLE)
    assert isinstance(model, CounterpointsV1)
    dumped = model.model_dump()
    assert dumped["issues"][0]["side_a"] == "Remote first"
    assert dumped["issues"][0]["pairs"][1]["response"] is None


def test_schema_rejects_unknown_fields():
    bad = {"title": "x", "issues": [], "winner": "side_a"}
    with pytest.raises(ValidationError):
        CounterpointsV1.model_validate(bad)


def test_config_defaults_and_bounds():
    cfg = CounterpointsConfig()
    assert (cfg.num_issues, cfg.points_per_issue, cfg.include_synthesis) == (3, 4, True)
    assert cfg.formats == ["pdf"]
    with pytest.raises(ValidationError):
        CounterpointsConfig(num_issues=0)
    with pytest.raises(ValidationError):
        CounterpointsConfig(points_per_issue=9)


def test_manifest():
    m = CounterpointsCreator().manifest
    assert m.key == "counterpoints"
    assert m.emits == ["counterpoints.v1"]
    assert m.view is not None and m.view.entry == "view/index.html"


def test_render_markdown():
    md = render_markdown(SAMPLE)
    assert md.startswith("# Remote Work Debates")
    assert "## 1. Should the company go fully remote?" in md
    assert "**Remote first:** Remote widens the hiring pool." in md
    assert "**Office first:** Onboarding quality drops" in md
    assert "**Remote first responds:**" in md
    assert "### Synthesis" in md


def test_render_qmd_front_matter():
    qmd = render_qmd(SAMPLE)
    assert qmd.startswith("---\n")
    assert 'title: "Remote Work Debates"' in qmd
    assert "pdf-engine: tectonic" in qmd


def test_slugify():
    assert slugify("Remote Work: Débates!") == "remote-work-debates"
    assert slugify("") == "counterpoints"


async def test_build_export_files_markdown_only(tmp_path):
    files, warnings = await build_export_files(SAMPLE, str(tmp_path), formats=[])
    assert [f.filename for f in files] == ["remote-work-debates.md"]
    assert warnings == []
    assert (tmp_path / "remote-work-debates.md").exists()


async def test_build_export_files_pdf_missing_quarto_is_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    files, warnings = await build_export_files(SAMPLE, str(tmp_path), formats=["pdf"])
    assert any(f.filename.endswith(".md") for f in files)
    assert not any(f.filename.endswith(".pdf") for f in files)
    assert any("Quarto" in w or "PDF" in w for w in warnings)
