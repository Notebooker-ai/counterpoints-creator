"""counterpoints-creator: an Open Notebook creator that extracts debatable
issues from notebook content and generates a structured two-sided debate for
each (emitted as ``counterpoints.v1``).

The LLM first identifies the issues (question + two named sides), then
generates each issue's debate one at a time: matched point/counterpoint pairs
with evidence, side A's response to each counter, and a neutral synthesis.
The view bundle renders the data interactively; a Markdown file is always
attached and a PDF is rendered via Quarto + Tectonic best-effort.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import ClassVar, List, Literal

from ai_prompter import Prompter
from loguru import logger
from open_notebook_creator_sdk import (
    BaseCreator,
    CreationError,
    CreationRequest,
    CreationResult,
    CreatorManifest,
    CreatorView,
    ModelRoleSpec,
)
from open_notebook_creator_sdk.schemas import CounterpointsV1
from pydantic import BaseModel, Field

from counterpoints_creator.export import build_export_files

__version__ = "0.1.0"

SCHEMA_ID = "counterpoints.v1"


class CounterpointsConfig(BaseModel):
    """Per-generation config; its JSON Schema drives the host's generate form."""

    num_issues: int = Field(
        default=3, ge=1, le=10, description="How many debatable issues to extract"
    )
    points_per_issue: int = Field(
        default=4, ge=2, le=8, description="Argument pairs per issue"
    )
    include_synthesis: bool = Field(
        default=True,
        description="Close each issue with a neutral synthesis of where the sides actually disagree",
    )
    formats: List[Literal["pdf"]] = Field(
        default=["pdf"],
        description="File formats to render in addition to the always-included Markdown",
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def _render_prompt(name: str, ctx: dict) -> str:
    template = resources.files("counterpoints_creator.prompts").joinpath(name).read_text()
    return Prompter(template_text=template).render(ctx)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _opt(value: object) -> str | None:
    s = _clean(value)
    return s or None


class CounterpointsCreator(BaseCreator):
    config_model: ClassVar[type] = CounterpointsConfig

    @property
    def manifest(self) -> CreatorManifest:
        return self.build_manifest(
            key="counterpoints",
            name="Counterpoints",
            version=__version__,
            description="Two-sided debates on issues from your notebook: matched point/counterpoint/response pairs with a neutral synthesis.",
            sdk_compat=">=0.4,<1",
            emits=[SCHEMA_ID],
            model_roles=[
                ModelRoleSpec(
                    key="text",
                    kind="language",
                    requires=["structured_json"],
                    description="LLM that identifies the issues and writes both sides.",
                )
            ],
            icon="scale",
            view=CreatorView(entry="view/index.html"),
            suggestion_hint=(
                "which claims to challenge, which opposing perspectives to develop, "
                "and where the real disagreement lies"
            ),
        )

    async def generate(self, request: CreationRequest) -> CreationResult:
        cfg = CounterpointsConfig.model_validate(request.config)
        role = request.models.get("text")
        if role is None:
            return CreationResult(
                status="FAILURE",
                schema_id=SCHEMA_ID,
                data={},
                errors=[CreationError(phase="setup", message="missing 'text' model role")],
                user_message="No language model was provided for counterpoints generation.",
            )

        # 1. Identify the debatable issues (structured JSON).
        issues_prompt = _render_prompt(
            "issues.jinja",
            {
                "content": request.content.text,
                "num_issues": cfg.num_issues,
                "instructions": request.instructions,
            },
        )
        llm = role.create_language(structured={"type": "json"}, max_tokens=2000)
        resp = await llm.ainvoke(issues_prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        try:
            outline = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as e:
            logger.error(f"counterpoints: issues outline was non-JSON: {e}")
            return CreationResult(
                status="FAILURE",
                schema_id=SCHEMA_ID,
                data={},
                errors=[CreationError(phase="parse", message=f"invalid JSON: {e}", retryable=True)],
                user_message="The model returned an unparseable response. Please retry.",
            )

        title = _clean(outline.get("title")) if isinstance(outline, dict) else ""
        description = _opt(outline.get("description")) if isinstance(outline, dict) else None
        issues_in = outline.get("issues", []) if isinstance(outline, dict) else []
        issues_meta = [
            {
                "question": _clean(i.get("question")),
                "context": _opt(i.get("context")),
                "side_a": _clean(i.get("side_a")) or "For",
                "side_b": _clean(i.get("side_b")) or "Against",
            }
            for i in issues_in
            if isinstance(i, dict) and _clean(i.get("question"))
        ][: cfg.num_issues]
        if not title or not issues_meta:
            return CreationResult(
                status="FAILURE",
                schema_id=SCHEMA_ID,
                data={},
                errors=[CreationError(phase="generate", message="no debatable issues identified")],
                user_message="No debatable issues could be identified in this content.",
            )

        # 2. One debate per issue (structured JSON per call, best-effort).
        debate_llm = role.create_language(structured={"type": "json"}, max_tokens=4000)
        issues: list[dict] = []
        errors: list[CreationError] = []
        warnings: list[str] = []
        for meta in issues_meta:
            prompt = _render_prompt(
                "debate.jinja",
                {
                    "content": request.content.text,
                    "instructions": request.instructions,
                    "question": meta["question"],
                    "context": meta["context"],
                    "side_a": meta["side_a"],
                    "side_b": meta["side_b"],
                    "points_per_issue": cfg.points_per_issue,
                    "include_synthesis": cfg.include_synthesis,
                },
            )
            try:
                dresp = await debate_llm.ainvoke(prompt)
                draw = dresp.content if hasattr(dresp, "content") else str(dresp)
                debate = json.loads(_strip_fences(draw))
            except Exception as e:  # noqa: BLE001 - one issue failing is non-fatal
                logger.warning(f"counterpoints: debate failed for {meta['question']!r}: {e}")
                errors.append(CreationError(phase="generate", message=f"{meta['question']}: {e}", retryable=True))
                warnings.append(f"Could not generate the debate for: {meta['question']}")
                continue

            pairs_in = debate.get("pairs", []) if isinstance(debate, dict) else []
            pairs = [
                {
                    "point": _clean(p.get("point")),
                    "point_evidence": _opt(p.get("point_evidence")),
                    "counterpoint": _clean(p.get("counterpoint")),
                    "counterpoint_evidence": _opt(p.get("counterpoint_evidence")),
                    "response": _opt(p.get("response")),
                }
                for p in pairs_in
                if isinstance(p, dict) and _clean(p.get("point")) and _clean(p.get("counterpoint"))
            ][: cfg.points_per_issue]
            if not pairs:
                warnings.append(f"No argument pairs produced for: {meta['question']}")
                continue

            issues.append(
                {
                    "question": meta["question"],
                    "context": meta["context"],
                    "side_a": meta["side_a"],
                    "side_b": meta["side_b"],
                    "pairs": pairs,
                    "synthesis": _opt(debate.get("synthesis")) if cfg.include_synthesis else None,
                }
            )

        if not issues:
            return CreationResult(
                status="FAILURE",
                schema_id=SCHEMA_ID,
                data={},
                warnings=warnings,
                errors=errors or [CreationError(phase="generate", message="no debates produced")],
                user_message="No debates could be generated from this content.",
            )

        data = CounterpointsV1(
            title=title,
            description=description,
            issues=issues,
        ).model_dump()

        # 3. File exports (never turn a SUCCESS into a FAILURE).
        files: list = []
        try:
            files, export_warnings = await build_export_files(data, request.output_dir, cfg.formats)
            warnings.extend(export_warnings)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"counterpoints: export failed: {e}")
            warnings.append(f"Export files could not be generated: {e}")

        return CreationResult(
            status="PARTIAL" if errors else "SUCCESS",
            schema_id=SCHEMA_ID,
            data=data,
            files=files,
            warnings=warnings,
            errors=errors,
        )
