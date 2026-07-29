# counterpoints-creator

An [Open Notebook](https://github.com/Notebooker-ai/open-notebook-nb) creation plugin
that extracts debatable issues from notebook content and generates a structured
two-sided debate for each: matched **point ↔ counterpoint ↔ response** argument
pairs, plus a neutral synthesis. Emits `counterpoints.v1`.

## Output

- **Interactive view bundle** (`view/index.html`): two-column debate rendered in the
  host's sandboxed iframe, one section per issue, light/dark theme aware.
- **Files**: a Markdown export (always) and a PDF rendered via Quarto + Tectonic
  (best-effort — a render failure becomes a warning, never a generation failure).

## Config

| field | default | range | notes |
| --- | --- | --- | --- |
| `num_issues` | 3 | 1–10 | debatable issues to extract |
| `points_per_issue` | 4 | 2–8 | argument pairs per issue |
| `include_synthesis` | true | — | neutral "where they actually disagree" closer |
| `formats` | `["pdf"]` | — | file formats beyond the always-emitted Markdown |

## Structure per issue

Each issue is a neutrally-phrased question with two named sides (labels are
content-derived, e.g. "Growth first" vs "Stability first", not always "pro/con").
Each pair is: side A's **point** (+ evidence), side B's strongest direct
**counterpoint** (+ evidence), and side A's steel-manned **response**. The
synthesis never picks a winner — it names the crux and what evidence would
resolve it.

## Development

```bash
uv sync --extra dev
uv run pytest -v
```

Registered in open-notebook's `CREATOR_PACKAGES` as `counterpoints`.
