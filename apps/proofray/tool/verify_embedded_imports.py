"""Run inside a packaged runtime during the feasibility spike."""
from __future__ import annotations

import importlib
import json
import platform
from zoneinfo import ZoneInfo


MODULES = (
    "numpy", "proofray", "horizon_memory", "pymongo", "dns", "pg8000",
    "scramp", "asn1crypto", "dateutil", "six", "pymysql", "redis",
    "boto3", "botocore", "jmespath", "s3transfer", "urllib3",
    "tzdata",
)


def main() -> None:
    result = {
        "python": platform.python_version(),
        "modules": {name: bool(importlib.import_module(name)) for name in MODULES},
        "iana_timezone": ZoneInfo("America/Sao_Paulo").key,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
