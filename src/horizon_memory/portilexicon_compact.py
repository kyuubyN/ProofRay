# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build/read a compact mmap POS index from the MIT-licensed PortiLexicon-UD TSV.

The format preserves the union of all coarse analyses for each surface form. It contains no
probabilities, ranking, benchmark labels or generated text. Promoted verbatim from
`lab/portilexicon_compact.py` as a dependency of the Portuguese atomic-relations surface-role
bridge (`portuguese_atomic_relations.py`). The compact artifact itself is not shipped in the
package -- see `portuguese_atomic_relations.py`'s own docstring for how to build it.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
import hashlib
import mmap
from pathlib import Path
import struct
import tempfile

from .finite_morphology_lattice import MorphClass


MAGIC = b"HPTLEX1\0"
VERSION = 1
BLOCK_SIZE = 64
_HEADER = struct.Struct("<8sHHIQ32sQ")
_BLOCK = struct.Struct("<QH")
_ENTRY = struct.Struct("<HHH")

_CLASS_BITS = {item: 1 << index for index, item in enumerate(MorphClass)}
_POS_CLASSES = {
    "ADJ": (MorphClass.MODIFIER,),
    "ADP": (MorphClass.ADPOSITION,),
    "ADV": (MorphClass.ADV,),
    "AUX": (MorphClass.AUX,),
    "CCONJ": (MorphClass.COORDINATOR,),
    "DET": (MorphClass.DET,),
    "NOUN": (MorphClass.NOMINAL,),
    "NUM": (MorphClass.NUMERIC,),
    "PROPN": (MorphClass.NOMINAL,),
    "PRON": (),
    "PUNCT": (MorphClass.PUNCT,),
    "SCONJ": (MorphClass.SUBORDINATOR,),
    "SYM": (MorphClass.CONTENT,),
    "VERB": (MorphClass.PREDICATE,),
}


def _mask(pos: str, features: str) -> int:
    classes = set(_POS_CLASSES.get(pos, (MorphClass.CONTENT,)))
    if pos == "PRON":
        classes.add(MorphClass.NOMINAL)
        if "PronType=Rel" in features:
            classes.add(MorphClass.REL)
        if "Case=Acc" in features or "Case=Dat" in features:
            classes.add(MorphClass.CLITIC)
    return sum(_CLASS_BITS[item] for item in classes)


def mask_classes(mask: int) -> frozenset[MorphClass]:
    return frozenset(item for item, bit in _CLASS_BITS.items() if mask & bit)


def _group_rows(tsv: Path) -> Iterator[tuple[str, int]]:
    current = None
    union = 0
    with tsv.open("r", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                raise ValueError(f"invalid PortiLexicon row {line_number}")
            surface, _lemma, pos, features = fields
            normalized = surface.casefold()
            if current is not None and normalized < current:
                raise ValueError("PortiLexicon input is not canonically sorted")
            if normalized != current:
                if current is not None:
                    yield current, union
                current, union = normalized, 0
            union |= _mask(pos, features)
    if current is not None:
        yield current, union


def build_compact_portilexicon(tsv: Path, output: Path) -> dict[str, int | str]:
    source_sha = hashlib.sha256(tsv.read_bytes()).digest()
    blocks: list[tuple[str, int]] = []
    entry_count = 0
    with tempfile.TemporaryFile() as data:
        previous = ""
        for surface, mask in _group_rows(tsv):
            if entry_count % BLOCK_SIZE == 0:
                blocks.append((surface, data.tell()))
                previous = ""
            prefix = 0
            limit = min(len(previous), len(surface), 0xFFFF)
            while prefix < limit and previous[prefix] == surface[prefix]:
                prefix += 1
            suffix = surface[prefix:].encode("utf-8")
            if len(suffix) > 0xFFFF or mask > 0xFFFF:
                raise ValueError("compact lexicon field exceeds format")
            data.write(_ENTRY.pack(prefix, len(suffix), mask))
            data.write(suffix)
            previous = surface
            entry_count += 1
        data_bytes = data.tell()
        table_bytes = sum(_BLOCK.size + len(key.encode("utf-8")) for key, _ in blocks)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as target:
            target.write(_HEADER.pack(
                MAGIC, VERSION, BLOCK_SIZE, len(blocks), entry_count, source_sha, table_bytes))
            for key, offset in blocks:
                raw = key.encode("utf-8")
                target.write(_BLOCK.pack(offset, len(raw)))
                target.write(raw)
            data.seek(0)
            while chunk := data.read(1 << 20):
                target.write(chunk)
    return {
        "entries": entry_count,
        "blocks": len(blocks),
        "data_bytes": data_bytes,
        "file_bytes": output.stat().st_size,
        "source_sha256": source_sha.hex(),
        "artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


class CompactPortiLexicon:
    def __init__(self, path: Path, *, expected_source_sha256: str | None = None):
        self._stream = path.open("rb")
        self._mmap = mmap.mmap(self._stream.fileno(), 0, access=mmap.ACCESS_READ)
        if len(self._mmap) < _HEADER.size:
            raise ValueError("truncated compact lexicon")
        magic, version, block_size, nblocks, self.entry_count, source_sha, table_bytes = \
            _HEADER.unpack_from(self._mmap)
        if magic != MAGIC or version != VERSION or block_size != BLOCK_SIZE:
            raise ValueError("incompatible compact lexicon")
        if expected_source_sha256 and source_sha.hex() != expected_source_sha256:
            raise ValueError("PortiLexicon source authority mismatch")
        self.artifact_sha256 = hashlib.sha256(self._mmap).hexdigest()
        cursor = _HEADER.size
        data_start = _HEADER.size + table_bytes
        keys, offsets = [], []
        for _ in range(nblocks):
            offset, key_length = _BLOCK.unpack_from(self._mmap, cursor)
            cursor += _BLOCK.size
            key = self._mmap[cursor:cursor + key_length].decode("utf-8")
            cursor += key_length
            keys.append(key)
            offsets.append(data_start + offset)
        if cursor != data_start or offsets != sorted(offsets):
            raise ValueError("invalid compact lexicon table")
        self.source_sha256 = source_sha.hex()
        self._keys = tuple(keys)
        self._offsets = tuple(offsets)

    def close(self) -> None:
        self._mmap.close()
        self._stream.close()

    def __enter__(self) -> "CompactPortiLexicon":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def lookup_mask(self, surface: str) -> int:
        target = surface.casefold()
        block = bisect_right(self._keys, target) - 1
        if block < 0:
            return 0
        cursor = self._offsets[block]
        stop = self._offsets[block + 1] if block + 1 < len(self._offsets) else len(self._mmap)
        previous = ""
        while cursor < stop:
            prefix, suffix_length, mask = _ENTRY.unpack_from(self._mmap, cursor)
            cursor += _ENTRY.size
            suffix = self._mmap[cursor:cursor + suffix_length].decode("utf-8")
            cursor += suffix_length
            word = previous[:prefix] + suffix
            if word == target:
                return mask
            if word > target:
                return 0
            previous = word
        return 0

    def lookup(self, surface: str) -> frozenset[MorphClass]:
        return mask_classes(self.lookup_mask(surface))


__all__ = ["CompactPortiLexicon", "build_compact_portilexicon", "mask_classes"]
