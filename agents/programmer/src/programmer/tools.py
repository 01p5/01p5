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
        f"    ports:",
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
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates parent dirs). DESTRUCTIVE — gated by approval."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} bytes to {p}"


READ_ONLY_TOOLS = [generate_dockerfile, generate_compose_service, generate_helm_values]
DESTRUCTIVE_TOOLS = [write_file]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS
