"""Embedded application service for the native ProofRay client.

This package is an application boundary. It may orchestrate providers,
connectors and UI events, but it never grants factual authority to them.
"""

from .protocol import BRIDGE_SCHEMA, MAX_FRAME_BYTES

__all__ = ["BRIDGE_SCHEMA", "MAX_FRAME_BYTES"]
