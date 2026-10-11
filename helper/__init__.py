"""Jade Helper v1 — volunteer compute client for J.A.D.E.

Your PC donates idle GPU/CPU to J.A.D.E.'s batch media-render queue.
Your machine never sees J.A.D.E.'s memory, conversations, tools, or files —
only render prompts and coarse resource stats.
"""

from helper.protocol import PROTOCOL_V

__all__ = ["PROTOCOL_V"]
