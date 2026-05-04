# Olympus dev image — agentlib + sysadmin agent.
#
# This is a *dev* image (W1-2): one container with kubectl + the lean
# test set installed (langchain.tools + pydantic, same as CI). It's
# enough to exercise the AgentSpec contract, tool-gating, the in-memory
# bus, and the sysadmin smoke tests.
#
# It is NOT enough to run the LLM-backed ``StructuralAgent.invoke()``
# path — that needs langchain>=1.0 + olympus_telemetry, neither of
# which is on PyPI yet. To enable it locally:
#
#   docker compose build --build-arg INSTALL_LLM_STACK=1
#
# (You'll also need olympus_telemetry available — bind-mount it or
# publish it first.)
#
# Production per-agent images are W5-6 work and will live alongside
# each agent.

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

# Source — copy after deps so layer cache survives most edits.
COPY libs/agentlib libs/agentlib
COPY agents agents
COPY docs docs
COPY PROJECT_PLAN.md PROJECT_PLAN.md

# Editable installs of every Olympus package the dashboard depends on
# (the dashboard's build_default_server constructs all four agents).
RUN pip install --no-cache-dir --no-deps -e ./libs/agentlib \
 && pip install --no-cache-dir --no-deps -e ./agents/sysadmin \
 && pip install --no-cache-dir --no-deps -e ./agents/programmer \
 && pip install --no-cache-dir --no-deps -e ./agents/terraform \
 && pip install --no-cache-dir --no-deps -e ./agents/ansible \
 && pip install --no-cache-dir --no-deps -e ./agents/olympus_cli \
 && pip install --no-cache-dir --no-deps -e ./agents/dashboard

ENV PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["olympus-dashboard", "--host=0.0.0.0", "--port=8765", "--router=manual"]
