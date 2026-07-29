"""Export renderers for ``counterpoints.v1`` artifacts: Markdown (always) and
PDF via Quarto + Tectonic (best-effort).

Exports must never turn a successful generation into a failure: every format is
attempted independently and failures become warnings.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

from loguru import logger
from open_notebook_creator_sdk import CreationFile

_QMD_NAME = "counterpoints.qmd"
_QMD_STEM = "counterpoints"

# quarto's first render can be slow (tectonic may fetch packages); cap it.
_RENDER_TIMEOUT_S = 600


def slugify(title: object) -> str:
    s = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "counterpoints"


def _issue_lines(issue: dict, number: int) -> list[str]:
    side_a = (issue.get("side_a") or "For").strip()
    side_b = (issue.get("side_b") or "Against").strip()
    lines = [f"## {number}. {issue.get('question') or 'Issue'}", ""]
    if issue.get("context"):
        lines += [str(issue["context"]).strip(), ""]
    lines += [f"**{side_a}** vs **{side_b}**", ""]
    for i, pair in enumerate(issue.get("pairs") or [], start=1):
        lines.append(f"### Exchange {i}")
        lines.append("")
        lines.append(f"**{side_a}:** {pair.get('point') or ''}")
        if pair.get("point_evidence"):
            lines.append(f"  \n*Evidence:* {pair['point_evidence']}")
        lines.append("")
        lines.append(f"**{side_b}:** {pair.get('counterpoint') or ''}")
        if pair.get("counterpoint_evidence"):
            lines.append(f"  \n*Evidence:* {pair['counterpoint_evidence']}")
        lines.append("")
        if pair.get("response"):
            lines.append(f"**{side_a} responds:** {pair['response']}")
            lines.append("")
    if issue.get("synthesis"):
        lines += ["### Synthesis", "", f"> {issue['synthesis']}", ""]
    return lines


def render_markdown(data: dict) -> str:
    title = (data.get("title") or "Counterpoints").strip() or "Counterpoints"
    lines = [f"# {title}", ""]
    if data.get("description"):
        lines += [str(data["description"]).strip(), ""]
    for n, issue in enumerate(data.get("issues") or [], start=1):
        lines += _issue_lines(issue, n)
    return "\n".join(lines).rstrip() + "\n"


def render_qmd(data: dict) -> str:
    title = (data.get("title") or "Counterpoints").strip() or "Counterpoints"
    front = ["---", f"title: {json.dumps(title)}"]
    if data.get("description"):
        front.append(f"abstract: {json.dumps(str(data['description']).strip())}")
    front += [
        "format:",
        "  pdf:",
        "    pdf-engine: tectonic",
        "    geometry:",
        "      - margin=1in",
        "---",
        "",
    ]
    body: list[str] = []
    for n, issue in enumerate(data.get("issues") or [], start=1):
        body += _issue_lines(issue, n)
    return "\n".join(front + body).rstrip() + "\n"


async def _quarto_render(output_dir: Path) -> None:
    """Render ``counterpoints.qmd`` to PDF in-place. Raises on failure."""
    proc = await asyncio.create_subprocess_exec(
        "quarto",
        "render",
        _QMD_NAME,
        "--to",
        "pdf",
        cwd=str(output_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=_RENDER_TIMEOUT_S)
    if proc.returncode != 0:
        detail = (err.decode(errors="replace") or out.decode(errors="replace")).strip()
        raise RuntimeError(detail[-2000:] or f"quarto exited {proc.returncode}")


async def build_export_files(
    data: dict, output_dir: str, formats: List[str]
) -> Tuple[List[CreationFile], List[str]]:
    """Write Markdown (always) and PDF (if requested) into ``output_dir``.

    Never raises: each format is attempted independently and failures become
    warnings, so export problems can't fail an otherwise successful generation.
    """
    files: List[CreationFile] = []
    warnings: List[str] = []
    out = Path(output_dir)
    stem = slugify(data.get("title"))

    try:
        md_name = f"{stem}.md"
        (out / md_name).write_text(render_markdown(data), encoding="utf-8")
        files.append(
            CreationFile(filename=md_name, content_type="text/markdown", path=md_name, label="Markdown")
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"counterpoints: Markdown export failed: {e}")
        warnings.append(f"Markdown export failed: {e}")

    if "pdf" in formats:
        try:
            (out / _QMD_NAME).write_text(render_qmd(data), encoding="utf-8")
            await _quarto_render(out)
            pdf_name = f"{_QMD_STEM}.pdf"
            if not (out / pdf_name).exists():
                raise RuntimeError("quarto reported success but produced no output file")
            files.append(
                CreationFile(filename=pdf_name, content_type="application/pdf", path=pdf_name, label="PDF")
            )
        except FileNotFoundError:
            logger.warning("counterpoints: 'quarto' binary not found on PATH")
            warnings.append("Quarto is not installed on the server; PDF export skipped.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"counterpoints: PDF export failed: {e}")
            warnings.append("PDF export failed.")

    return files, warnings
