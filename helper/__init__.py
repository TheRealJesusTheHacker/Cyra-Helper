"""Cyra Helper v1 — volunteer compute client for Cyra

Your PC donates idle GPU/CPU to Cyra's batch media-render queue.
Your machine never sees Cyra's memory, conversations, tools, or files —
only render prompts and coarse resource stats.
"""

from helper.protocol import PROTOCOL_V

__all__ = ["PROTOCOL_V"]
