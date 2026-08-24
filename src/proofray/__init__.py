# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical ProofRay import surface.

The implementation and durable wire identities remain in :mod:`horizon_memory`
during the public-alpha compatibility window.  This module is a zero-behavior-change
facade: both namespaces expose the same objects and reopen the same proofs.
"""
from horizon_memory import *  # noqa: F401,F403
from horizon_memory import __all__, __version__
