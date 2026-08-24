"""Structural ERG SimpleMRS decoding for D42-B, with exact span checks."""

from __future__ import annotations

from pathlib import Path
import sys


_VENDORED = Path(__file__).parent / "external" / "delphin" / "pydelphin-1.11.0"
if str(_VENDORED) not in sys.path:
    sys.path.insert(0, str(_VENDORED))

from delphin import lnk, mrs  # noqa: E402
from delphin.codecs import simplemrs  # noqa: E402


def extract_simplemrs_blocks(output: bytes) -> tuple[str, ...]:
    text = output.decode("utf-8", errors="strict")
    blocks: list[str] = []
    cursor = 0
    marker = "[ LTOP:"
    while (start := text.find(marker, cursor)) >= 0:
        depth = 0
        quoted = False
        escaped = False
        end = None
        for index in range(start, len(text)):
            character = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
                continue
            if character == '"':
                quoted = True
            elif character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
                if depth < 0:
                    raise ValueError("unbalanced SimpleMRS closing bracket")
        if end is None:
            raise ValueError("unterminated SimpleMRS block")
        blocks.append(text[start:end])
        cursor = end
    return tuple(blocks)


def decode_simplemrs_blocks(output: bytes) -> tuple[mrs.MRS, ...]:
    return tuple(simplemrs.decode(block) for block in extract_simplemrs_blocks(output))


def spans_reopen(semantic: mrs.MRS, source: str) -> bool:
    if not semantic.rels:
        return False
    for ep in semantic.rels:
        if ep.lnk is None or ep.lnk.type != lnk.Lnk.CHARSPAN:
            return False
        start, end = ep.lnk.data
        if not (0 <= start < end <= len(source)):
            return False
        if source[start:end] == "":
            return False
    return True


def shared_isomorphic_count(left: tuple[mrs.MRS, ...], right: tuple[mrs.MRS, ...],
                            *, properties: bool = True) -> int:
    matched = 0
    consumed_right: set[int] = set()
    for left_mrs in left:
        for index, right_mrs in enumerate(right):
            if index not in consumed_right and mrs.is_isomorphic(
                    left_mrs, right_mrs, properties=properties):
                matched += 1
                consumed_right.add(index)
                break
    return matched
