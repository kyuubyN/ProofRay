# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prints the current API bearer token without starting the server -- for when you need it again
after the first run (the server also prints it once at startup, see server.py's `__main__`).

Run: python3 api/show_api_token.py
"""
from __future__ import annotations

from machine_auth import credentials_path, ensure_local_credentials

if __name__ == "__main__":
    credentials = ensure_local_credentials()
    print(f"Bearer token (saved at {credentials_path()}): {credentials['token']}")
