from __future__ import annotations

import base64
from collections.abc import Callable
import hashlib
import threading


HostCall = Callable[[str, dict[str, object]], dict[str, object]]
_DIRECT_SUFFIX_BASE64_BYTES = 600 * 1024
_MAX_SINGLE_RECORD_BASE64_BYTES = 850 * 1024


class HostAuthorizedSidecarRecordStore:
    """Sidecar record store backed by the encrypted Flutter-owned database.

    The Python core owns validation and publication.  Flutter owns physical
    durability.  Replacements are transmitted as the smallest changed suffix,
    so the normal append path moves exactly one canonical record.
    """

    def __init__(self, store_key: str, call_host: HostCall, *, page_size: int = 128):
        if not store_key or page_size < 1 or page_size > 512:
            raise ValueError("host record store needs a key and bounded page size")
        self.store_key = store_key
        self._call_host = call_host
        self._page_size = page_size
        self._records: tuple[bytes, ...] | None = None
        self._lock = threading.RLock()

    def load(self) -> tuple[bytes, ...]:
        with self._lock:
            if self._records is not None:
                return self._records
            rows: list[bytes] = []
            cursor = 0
            while True:
                response = self._call_host("sidecar.load", {
                    "store_key": self.store_key,
                    "after_sequence": cursor,
                    "limit": self._page_size,
                })
                encoded = response.get("records")
                complete = response.get("complete")
                if not isinstance(encoded, list) or any(
                        not isinstance(item, str) for item in encoded) or not isinstance(
                            complete, bool):
                    raise OSError("host returned an invalid sidecar page")
                try:
                    page = [base64.b64decode(item, validate=True) for item in encoded]
                except (ValueError, TypeError) as error:
                    raise OSError("host returned invalid sidecar bytes") from error
                if any(not item for item in page):
                    raise OSError("host returned an empty sidecar record")
                rows.extend(page)
                cursor += len(page)
                if complete:
                    break
                if not page:
                    raise OSError("host sidecar pagination made no progress")
            self._records = tuple(rows)
            return self._records

    def replace(self, records: tuple[bytes, ...]) -> None:
        with self._lock:
            current = self.load()
            prefix = 0
            limit = min(len(current), len(records))
            while prefix < limit and current[prefix] == records[prefix]:
                prefix += 1
            suffix = records[prefix:]
            prefix_digest = (
                hashlib.sha256(current[prefix - 1]).hexdigest() if prefix else "")
            encoded = [base64.b64encode(item).decode("ascii") for item in suffix]
            if sum(len(item) for item in encoded) <= _DIRECT_SUFFIX_BASE64_BYTES:
                response = self._call_host("sidecar.replace_suffix", {
                    "store_key": self.store_key,
                    "common_prefix": prefix,
                    "common_prefix_sha256": prefix_digest,
                    "records": encoded,
                })
            else:
                response = self._replace_chunked(
                    prefix, prefix_digest, suffix, encoded)
            if response.get("committed") is not True:
                raise OSError("host did not acknowledge durable sidecar commit")
            self._records = tuple(bytes(item) for item in records)

    def _replace_chunked(
        self,
        prefix: int,
        prefix_digest: str,
        suffix: tuple[bytes, ...],
        encoded: list[str],
    ) -> dict[str, object]:
        transaction_id = hashlib.sha256(b"\x00".join((
            self.store_key.encode("utf-8"),
            str(prefix).encode("ascii"),
            prefix_digest.encode("ascii"),
            *(hashlib.sha256(item).digest() for item in suffix),
        ))).hexdigest()
        begun = self._call_host("sidecar.replace_begin", {
            "store_key": self.store_key,
            "transaction_id": transaction_id,
            "common_prefix": prefix,
            "common_prefix_sha256": prefix_digest,
            "total_records": len(encoded),
        })
        if begun.get("staged") is not True:
            raise OSError("host rejected sidecar replacement plan")
        chunks: list[list[str]] = []
        active: list[str] = []
        active_bytes = 0
        for item in encoded:
            if len(item) > _MAX_SINGLE_RECORD_BASE64_BYTES:
                raise OSError("one sidecar record exceeds the host frame budget")
            if active and active_bytes + len(item) > _DIRECT_SUFFIX_BASE64_BYTES:
                chunks.append(active)
                active = []
                active_bytes = 0
            active.append(item)
            active_bytes += len(item)
        if active:
            chunks.append(active)
        for index, chunk in enumerate(chunks):
            staged = self._call_host("sidecar.replace_chunk", {
                "store_key": self.store_key,
                "transaction_id": transaction_id,
                "chunk_index": index,
                "records": chunk,
            })
            if staged.get("staged") is not True:
                raise OSError("host rejected sidecar replacement chunk")
        return self._call_host("sidecar.replace_commit", {
            "store_key": self.store_key,
            "transaction_id": transaction_id,
        })


__all__ = ["HostAuthorizedSidecarRecordStore", "HostCall"]
