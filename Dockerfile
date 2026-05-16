# Olympus dashboard image.
#
# Two stages:
#   1. frontend-build (node:20-alpine) — runs vite to produce the SPA
#      under agents/dashboard/static/dist/.
#   2. python:3.12-slim — installs agentlib + every agent + the
#      dashboard server, copies in the built SPA, and exposes :8765.
#
# Build:
#   docker build -t olympus/dashboard:dev .                          # lean (no LLM stack)
#   docker build -t olympus/dashboard:llm --build-arg INSTALL_LLM_STACK=1 .

# ---------- stage 1: frontend ----------
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY agents/dashboard/frontend/package.json agents/dashboard/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi
COPY agents/dashboard/frontend/ ./
RUN npm run build
# Output lands at /build/../static/dist relative to vite.config.ts,
# i.e. /static/dist after the COPY layout. Re-copy to a known path.
RUN mkdir -p /spa && cp -r /static/dist/. /spa/

# ---------- stage 2: backend ----------
FROM python:3.12-slim

ARG KUBECTL_VERSION=v1.30.0
ARG INSTALL_LLM_STACK=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -sSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/olympus

# Lean install — same set as .github/workflows/ci.yml.
RUN pip install --no-cache-dir \
        ruff pytest pydantic \
        'langchain>=0.3,<2.0' 'langchain-core>=0.3,<2.0'

# Optional: full LLM stack for live agent runs.
RUN if [ "$INSTALL_LLM_STACK" = "1" ]; then \
        pip install --no-cache-dir \
            'langchain==1.0.2' 'langchain-core==1.0.1' \
            'langgraph==1.0.2' 'langchain-anthropic==1.0.0' \
            'langchain-openai==1.0.1' 'litellm==1.79.0' \
            'python-dotenv==1.2.1' ; \
    fi

# Tooling the agents shell out to: git for source fetches, ansible-core
# for the ansible agent, terraform (pinned binary fetch — repo route was
# flaky on intranet DNS) for the terraform agent.
ARG TERRAFORM_VERSION=1.9.8
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client ansible-core unzip ca-certificates curl \
 && curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/tf.zip \
 && unzip -o /tmp/tf.zip -d /usr/local/bin/ \
 && rm /tmp/tf.zip \
 && apt-get purge -y curl unzip \
 && rm -rf /var/lib/apt/lists/*

# Source — copy after deps so layer cache survives most edits.
COPY libs/agentlib libs/agentlib
COPY agents agents
COPY docs docs
COPY PROJECT_PLAN.md PROJECT_PLAN.md
# infra/terraform and infra/ansible let the dashboard's /stacks/* endpoints
# enumerate stacks, and let the agents actually operate on them (cwd
# defaults to /opt/olympus when not specified).
COPY infra/terraform infra/terraform
COPY infra/ansible infra/ansible

# Editable installs of every Olympus package the dashboard depends on
# (the dashboard's build_default_server constructs all four agents).
RUN pip install --no-cache-dir --no-deps -e ./libs/agentlib \
 && pip install --no-cache-dir --no-deps -e ./agents/sysadmin \
 && pip install --no-cache-dir --no-deps -e ./agents/programmer \
 && pip install --no-cache-dir --no-deps -e ./agents/terraform \
 && pip install --no-cache-dir --no-deps -e ./agents/ansible \
 && pip install --no-cache-dir --no-deps -e ./agents/olympus_cli \
 && pip install --no-cache-dir --no-deps -e ./agents/dashboard

# Drop the Vite-built SPA bundle on top of the python source tree.
# DashboardServer auto-picks static/dist when present.
COPY --from=frontend-build /spa /opt/olympus/agents/dashboard/static/dist

ENV PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["olympus-dashboard", "--host=0.0.0.0", "--port=8765", "--router=manual"]
