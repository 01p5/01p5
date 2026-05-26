"""HPC agent — operates a GPU + Slurm cluster via two MCP servers.

The agent itself ships no native tools; its toolbox is grafted at
runtime from the ``gpu-mcp`` and ``slurm-mcp`` servers (registered via
the dashboard's MCP page or the build_default_server ``mcp_servers=``
kwarg). The orchestrator's router keeps this agent out of the
candidate list until both prerequisites are connected — so a user
asking about jobs / GPU health before wiring those servers gets routed
to the next-best agent rather than this one.
"""
from .agent import HPCAgent, HPCResponse

__all__ = ["HPCAgent", "HPCResponse"]
