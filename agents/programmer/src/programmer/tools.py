"""
Programmer agent tools.

Generation tools are pure string templates — no I/O. The single
destructive tool, ``write_file``, performs the file system mutation
and is gated by the runtime's approval hook.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


_DOCKERFILE_TEMPLATES: dict[str, str] = {
    "python": """FROM python:{version}-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD [{cmd}]
""",
    "node": """FROM node:{version}-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
CMD [{cmd}]
""",
    "go": """FROM golang:{version}-alpine AS build
WORKDIR /src
COPY go.* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /out/app ./...

FROM gcr.io/distroless/static
COPY --from=build /out/app /app
CMD [{cmd}]
""",
}


def _quote_cmd(cmd: list[str]) -> str:
    return ", ".join(f'"{c}"' for c in cmd)


@tool
def generate_dockerfile(
    language: str,
    version: str = "3.12",
    cmd: Optional[list[str]] = None,
) -> str:
    """Generate a Dockerfile for a service.

    language: 'python', 'node', or 'go'.
    version: language runtime version (e.g. '3.12', '20', '1.22').
    cmd: container CMD as a list (e.g. ['python', 'app.py']).
    """
    template = _DOCKERFILE_TEMPLATES.get(language.lower())
    if template is None:
        return f"ERROR: unsupported language {language!r}; supported: {sorted(_DOCKERFILE_TEMPLATES)}"
    cmd = cmd or {"python": ["python", "app.py"], "node": ["node", "index.js"], "go": ["/app"]}[language.lower()]
    return template.format(version=version, cmd=_quote_cmd(cmd))


@tool
def generate_compose_service(
    name: str,
    image: str,
    port: int,
    env: Optional[dict[str, str]] = None,
) -> str:
    """Generate a single docker-compose service block in YAML."""
    lines = [
        f"  {name}:",
        f"    image: {image}",
        "    ports:",
        f"      - \"{port}:{port}\"",
    ]
    if env:
        lines.append("    environment:")
        for k, v in env.items():
            lines.append(f"      {k}: {v}")
    return "\n".join(lines) + "\n"


@tool
def generate_helm_values(service_name: str, image: str, port: int, replicas: int = 1) -> str:
    """Generate a minimal Helm chart values.yaml for a service."""
    return (
        f"replicaCount: {replicas}\n"
        f"image:\n"
        f"  repository: {image}\n"
        f"  tag: latest\n"
        f"service:\n"
        f"  name: {service_name}\n"
        f"  type: ClusterIP\n"
        f"  port: {port}\n"
    )


@tool
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """Read up to ``limit`` lines from ``path`` starting at ``offset``.

    Use this before editing — the runtime expects the agent to have seen
    the current file content before it proposes changes. Returns the
    raw bytes decoded as UTF-8, with line numbers prefixed (one-based)
    so subsequent edit_file calls can quote exact strings to replace.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"ERROR: {p} does not exist or is not a file"
    try:
        text = p.read_text()
    except UnicodeDecodeError:
        return f"ERROR: {p} is not valid UTF-8 (binary file?)"
    lines = text.splitlines()
    end = min(offset + limit, len(lines))
    if offset >= len(lines):
        return f"(offset {offset} is past end of file, total lines = {len(lines)})"
    out = [f"{i + 1:>5} | {ln}" for i, ln in enumerate(lines[offset:end], start=offset)]
    suffix = ""
    if end < len(lines):
        suffix = f"\n... ({len(lines) - end} more lines — re-call with offset={end})"
    return "\n".join(out) + suffix


@tool
def write_file(path: str, content: str) -> str:
    """Create or completely OVERWRITE ``path`` with ``content``.

    DESTRUCTIVE — gated by approval. Prefer ``edit_file`` for changing
    existing files; ``write_file`` is for new files or full rewrites
    where the entire content is being replaced.
    """
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} bytes to {p}"


@tool
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace ``old_string`` with ``new_string`` in ``path``.

    DESTRUCTIVE — gated by approval, and the approval card renders a
    unified diff so the reviewer sees exactly which lines change.

    Rules (mirror Claude Code's Edit tool):
      - ``old_string`` must appear in the file at least once.
      - If it appears more than once and ``replace_all`` is False, the
        edit fails — disambiguate by quoting more surrounding context
        or pass ``replace_all=True`` to intentionally replace every
        occurrence.
      - ``old_string`` and ``new_string`` must differ (no-op rejected).
      - The agent should call ``read_file`` first so it quotes
        old_string verbatim from the actual file content.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"ERROR: {p} does not exist — use write_file to create new files"
    if old_string == new_string:
        return "ERROR: old_string and new_string are identical (no-op)"
    current = p.read_text()
    count = current.count(old_string)
    if count == 0:
        return (
            f"ERROR: old_string was not found in {p}. "
            "Did you read_file first to quote the exact text?"
        )
    if count > 1 and not replace_all:
        return (
            f"ERROR: old_string matches {count} places in {p}. "
            "Quote more surrounding context to make it unique, or "
            "pass replace_all=True to replace every occurrence."
        )
    updated = current.replace(old_string, new_string) if replace_all \
        else current.replace(old_string, new_string, 1)
    p.write_text(updated)
    return f"edited {p} ({count} replacement{'s' if count != 1 else ''})"


READ_ONLY_TOOLS = [
    generate_dockerfile, generate_compose_service, generate_helm_values,
    read_file,
]
DESTRUCTIVE_TOOLS = [write_file, edit_file]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS
