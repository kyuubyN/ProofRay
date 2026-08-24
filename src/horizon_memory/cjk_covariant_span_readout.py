# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic interrogative-hole transport for Simplified Chinese extractive QA."""
from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
import bz2
import hashlib
import itertools
import json
from pathlib import Path
import multiprocessing
import re
import subprocess
import numpy as np
import zlib

from .erg_mrs_structural import decode_simplemrs_blocks, extract_simplemrs_blocks, lnk
from delphin import ace as delphin_ace
from horizon_memory.raw_causal_channels import observe_raw_text

_PUNCT = frozenset(" \t\r\n，。！？：；、,.!?:;（）()《》〈〉【】[]“”‘’\"'")
_CLAUSE_END = frozenset("，。！？；,.!?;")
_NUMBER = frozenset("零〇一二两三四五六七八九十百千万亿0123456789")
_MEASURE_UNIT = (
    "年|个月|月|日|天|号|小时|时|点|分钟|分|秒|人|名|个|家|次|岁|米|公里|"
    "千米|公斤|千克|克|吨|元|美元|欧元|英镑|百分比|%|％|倍|章|部|集|场|座|条|只|件"
)
_ARABIC_MEASURE = re.compile(
    rf"\d+(?:[.,]\d+)*(?:万|亿)?(?:{_MEASURE_UNIT})?")
_CHINESE_MEASURE = re.compile(
    rf"[零〇一二两三四五六七八九十百千万亿]+(?:{_MEASURE_UNIT})")
_TEMPORAL_PATTERNS = (
    re.compile(r"(?:公元前|西元前)?\d+(?:\.\d+)?(?:世紀|世纪|年代|年)"
               r"(?:\d{1,2}月(?:\d{1,2}(?:日|號|号))?)?(?:初期|中期|晚期|末期)?"
               r"(?:上午|下午|晚上|凌晨)?"),
    re.compile(r"[\u3400-\u9fff]{1,6}(?:元|[零〇一二兩两三四五六七八九十百千]+)年"
               r"(?:[零〇一二兩两三四五六七八九十]+月"
               r"(?:[零〇一二兩两三四五六七八九十]+(?:日|號|号))?)?"),
    re.compile(r"\d+(?:\.\d+)?(?:億|亿|萬|万)?(?:年|個月|个月|月|週|周|天|日|小時|小时|分鐘|分钟|秒)"
               r"(?:零[零〇一二兩两三四五六七八九十]+(?:個月|个月|月|週|周|天|日|小時|小时|分鐘|分钟|秒))?"),
    re.compile(r"(?:每年)?(?:農曆|农历)?[零〇一二兩两三四五六七八九十]+月"
               r"[初廿零〇一二兩两三四五六七八九十]+(?:日|重陽節|重阳节)?"),
    re.compile(r"(?:春|夏|秋|冬)季|(?:早上|上午|中午|下午|傍晚|晚上|凌晨)(?:時份|时分)?"),
    re.compile(r"[\u3400-\u9fff]{1,8}(?:代末|代初|朝末|朝初|中期|晚期|時期|时期)"),
    re.compile(r"當[^，。！？；,.!?;]{1,32}?(?:之後|之后|之前|時|时)"),
)
_CAUSAL_QUESTION = re.compile(r"因為什麼|因为什么|為什麼|为什么|為何|为何|何故|緣何|缘何")
_NON_CAUSAL_WHY = re.compile(
    r"(?:稱|称|叫|名|算|視|视|譽|誉|表現|表现|定義|定义|改名|更名)(?:之)?(?:為什麼|为什么)")

_ROOT = Path(__file__).resolve().parents[1]
_ZHONG_ACE = _ROOT / "lab/external/delphin/ace-0.9.31/ace"
_ZHONG_IMAGE = _ROOT / "lab/external/delphin/zhong/zhong-zhs_2018.03.30.dat"
_POS_MAP = {
    "n": ("NN",), "ng": ("NN",), "nr": ("NR",), "nrfg": ("NR",),
    "nrt": ("NR",), "ns": ("NR",), "nt": ("NR",), "nz": ("NR",),
    "v": ("VV",), "vd": ("VV",), "vn": ("VV",),
    "a": ("VA", "JJ"), "ad": ("VA", "JJ"), "an": ("VA", "JJ"),
    "m": ("CD",), "q": ("M",), "t": ("NT",), "tg": ("NT",),
    "eng": ("FW",), "x": ("FW",),
}
_DEMONSTRATIVE_SPLIT = {"这个": ("这", "个"), "这种": ("这", "种"),
                        "这些": ("这", "些"), "那个": ("那", "个"),
                        "那种": ("那", "种"), "那些": ("那", "些")}

# Longest first. This is a language grammar resource, never an answer/entity dictionary.
_HOLES = (
    ("什麼時候", "time"), ("什么时候", "time"),
    ("為什麼", "cause"), ("为什么", "cause"),
    ("多長時間", "time"), ("多长时间", "time"),
    ("哪一年", "time"), ("哪一月", "time"), ("哪一天", "time"),
    ("怎麼樣", "manner"), ("怎么样", "manner"),
    ("怎樣", "manner"), ("怎样", "manner"), ("怎麼", "manner"), ("怎么", "manner"),
    ("如何", "manner"), ("哪兩個", "entity"), ("哪两个", "entity"),
    ("哪一個", "entity"), ("哪一个", "entity"), ("哪位", "person"),
    ("哪家", "entity"), ("哪所", "entity"), ("哪類", "entity"), ("哪类", "entity"),
    ("哪個", "entity"), ("哪个", "entity"), ("哪種", "entity"), ("哪种", "entity"),
    ("哪裡", "place"), ("哪里", "place"), ("哪兒", "place"), ("哪儿", "place"),
    ("多少", "quantity"), ("多久", "time"), ("多長", "quantity"), ("多长", "quantity"),
    ("多大", "quantity"), ("多高", "quantity"), ("多遠", "quantity"), ("多远", "quantity"),
    ("何時", "time"), ("何时", "time"), ("何處", "place"), ("何处", "place"),
    ("何地", "place"), ("何人", "person"), ("何種", "entity"), ("何种", "entity"),
    ("有何", "entity"), ("為何", "cause"), ("为何", "cause"),
    ("甚麼", "entity"), ("什麼", "entity"), ("什么", "entity"),
    ("誰", "person"), ("谁", "person"), ("幾", "quantity"), ("几", "quantity"),
    ("哪", "entity"),
)


@dataclass(frozen=True)
class CJKQueryHole:
    kind: str
    surface: str
    start: int
    end: int


@dataclass(frozen=True)
class CJKSpanCandidate:
    text: str
    sentence_index: int
    source_span: tuple[int, int]
    anchor_before: int
    anchor_after: int
    score: float


@dataclass(frozen=True)
class CJKReadoutResult:
    state: str
    answer: str
    candidate: CJKSpanCandidate | None
    reason: str


@dataclass(frozen=True)
class FenciSpan:
    text: str
    source_span: tuple[int, int]
    token_count: int
    tags: tuple[str, ...]


POINTER_FEATURES = (
    "bias", "char_in_query", "prev_in_query", "next_in_query", "left_bigram_in_query",
    "right_bigram_in_query", "digit", "cjk", "latin", "punct", "position",
    "clause_start", "clause_end", "pos_nr", "pos_ns", "pos_nt", "pos_noun",
    "pos_verb", "pos_number", "hole_quantity", "hole_time", "hole_person", "hole_place",
    "hole_entity",
)


@dataclass(frozen=True)
class LinearSpanPointer:
    start_weights: tuple[float, ...]
    end_weights: tuple[float, ...]
    max_answer_chars: int = 96

    def __post_init__(self) -> None:
        if len(self.start_weights) != len(POINTER_FEATURES) or \
                len(self.end_weights) != len(POINTER_FEATURES):
            raise ValueError("pointer weights do not match feature schema")
        if self.max_answer_chars < 1:
            raise ValueError("max_answer_chars must be positive")

    def to_dict(self) -> dict:
        return {"schema": "horizon.cjk-linear-span-pointer.v1",
                "features": POINTER_FEATURES, "start_weights": self.start_weights,
                "end_weights": self.end_weights, "max_answer_chars": self.max_answer_chars}

    @classmethod
    def from_dict(cls, value: dict) -> "LinearSpanPointer":
        if tuple(value["features"]) != POINTER_FEATURES:
            raise ValueError("pointer feature schema mismatch")
        return cls(tuple(value["start_weights"]), tuple(value["end_weights"]),
                   int(value["max_answer_chars"]))


HASH_DIM = 8192


@dataclass(frozen=True)
class HashedSpanPointer:
    start_weights: tuple[float, ...]
    end_weights: tuple[float, ...]
    max_answer_chars: int = 96

    def __post_init__(self) -> None:
        if len(self.start_weights) != HASH_DIM or len(self.end_weights) != HASH_DIM:
            raise ValueError("hashed pointer dimension mismatch")

    def to_dict(self) -> dict:
        return {"schema": "horizon.cjk-hashed-span-pointer.v1", "hash_dim": HASH_DIM,
                "start_weights": self.start_weights, "end_weights": self.end_weights,
                "max_answer_chars": self.max_answer_chars}

    @classmethod
    def from_dict(cls, value: dict) -> "HashedSpanPointer":
        if int(value["hash_dim"]) != HASH_DIM:
            raise ValueError("hashed pointer hash dimension mismatch")
        return cls(tuple(value["start_weights"]), tuple(value["end_weights"]),
                   int(value["max_answer_chars"]))


@dataclass(frozen=True)
class CJKHybridReadoutResult:
    state: str
    answer: str
    used_span: bool
    span_result: CJKReadoutResult
    reason: str


# H-DEM reference kernel. It deliberately lives in the one CJK living-program module until the
# enumerative and packed semantics are proven equivalent; no linguistic mechanism may depend on it
# before that gate passes.
@dataclass(frozen=True, order=True)
class HDEMValue:
    value: str
    fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.value or self.fact_ids != tuple(sorted(set(self.fact_ids))) \
                or any(fact_id < 0 for fact_id in self.fact_ids):
            raise ValueError("H-DEM values require text and canonical non-negative FactIds")


@dataclass(frozen=True, order=True)
class HDEMVariable:
    name: str
    domain: tuple[HDEMValue, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.domain or self.domain != tuple(sorted(self.domain)) \
                or len({item.value for item in self.domain}) != len(self.domain):
            raise ValueError("H-DEM variable domain must be non-empty, unique and canonical")


@dataclass(frozen=True, order=True)
class HDEMConstraint:
    constraint_id: str
    variables: tuple[str, ...]
    allowed: tuple[tuple[str, ...], ...]
    witness_fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.constraint_id or not self.variables \
                or len(set(self.variables)) != len(self.variables):
            raise ValueError("H-DEM constraint needs a unique non-empty scope")
        if not self.allowed or self.allowed != tuple(sorted(set(self.allowed))) \
                or any(len(row) != len(self.variables) for row in self.allowed):
            raise ValueError("H-DEM allowed relation must be non-empty, canonical and arity-correct")
        if self.witness_fact_ids != tuple(sorted(set(self.witness_fact_ids))) \
                or any(fact_id < 0 for fact_id in self.witness_fact_ids):
            raise ValueError("H-DEM constraint witnesses must be canonical FactIds")


@dataclass(frozen=True)
class HDEMProblem:
    variables: tuple[HDEMVariable, ...]
    constraints: tuple[HDEMConstraint, ...]
    answer_variables: tuple[str, ...]

    def __post_init__(self) -> None:
        names = tuple(item.name for item in self.variables)
        if not names or names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError("H-DEM variables must have unique canonical names")
        if tuple(item.constraint_id for item in self.constraints) != \
                tuple(sorted(item.constraint_id for item in self.constraints)) \
                or len({item.constraint_id for item in self.constraints}) != len(self.constraints):
            raise ValueError("H-DEM constraints must have unique canonical identities")
        if not self.answer_variables or len(set(self.answer_variables)) != len(self.answer_variables) \
                or any(name not in names for name in self.answer_variables):
            raise ValueError("H-DEM answer variables must be unique known variables")
        domains = {item.name: {value.value for value in item.domain} for item in self.variables}
        for constraint in self.constraints:
            if any(name not in domains for name in constraint.variables):
                raise ValueError("H-DEM constraint references an unknown variable")
            for row in constraint.allowed:
                if any(value not in domains[name]
                       for name, value in zip(constraint.variables, row)):
                    raise ValueError("H-DEM allowed tuple contains an out-of-domain value")

    def canonical_sha256(self) -> str:
        payload = {
            "variables": [(item.name, [(value.value, value.fact_ids)
                                        for value in item.domain])
                          for item in self.variables],
            "constraints": [(item.constraint_id, item.variables, item.allowed,
                              item.witness_fact_ids) for item in self.constraints],
            "answer_variables": self.answer_variables,
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, order=True)
class HDEMWorld:
    assignment: tuple[tuple[str, str], ...]
    answer: tuple[str, ...]
    provenance: tuple[int, ...]


@dataclass(frozen=True, order=True)
class HDEMAnswerProof:
    answer: tuple[str, ...]
    monomials: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class HDEMResult:
    state: str
    answer: tuple[str, ...] | None
    proofs: tuple[HDEMAnswerProof, ...]
    worlds: tuple[HDEMWorld, ...]
    complete: bool
    explored_states: int
    pruned_values: int
    problem_sha256: str
    reason: str


@dataclass(frozen=True)
class HDCAResult:
    state: str
    answer: tuple[str, ...] | None
    domains: tuple[tuple[str, tuple[str, ...]], ...]
    proof_fact_ids: tuple[int, ...]
    certified_acyclic: bool
    revisions: int
    problem_sha256: str
    reason: str


@dataclass(frozen=True, order=True)
class HDCAFrameRole:
    role: str
    source_span: tuple[int, int]
    surface: str


@dataclass(frozen=True, order=True)
class HDCAFrame:
    constructor: str
    roles: tuple[HDCAFrameRole, ...]
    rule_id: str
    sentence_index: int = 0

    def role(self, name: str) -> HDCAFrameRole | None:
        return next((item for item in self.roles if item.role == name), None)


@dataclass(frozen=True, order=True)
class PinyinAtom:
    source_span: tuple[int, int]
    surface: str
    toned: tuple[str, ...]
    toneless: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.source_span) != 2 or self.source_span[0] < 0 \
                or self.source_span[1] <= self.source_span[0] or not self.surface:
            raise ValueError("pinyin atom requires an exact non-empty source span")
        if not self.toned or self.toned != tuple(sorted(set(self.toned))) \
                or self.toneless != tuple(sorted(set(self.toneless))):
            raise ValueError("pinyin readings must be non-empty canonical domains")


@dataclass(frozen=True)
class PinyinProjection:
    source_sha256: str
    resource_sha256: str
    atoms: tuple[PinyinAtom, ...]

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            return False
        return all(source[start:end] == atom.surface
                   for atom in self.atoms for start, end in (atom.source_span,))


@dataclass(frozen=True)
class HDEMFragment:
    variables: tuple[HDEMVariable, ...]
    constraints: tuple[HDEMConstraint, ...]


@dataclass(frozen=True, order=True)
class RomanizedPinyinAtom:
    source_span: tuple[int, int]
    surface: str
    syllable: str
    toned: bool


@dataclass(frozen=True, order=True)
class PinyinBridge:
    query_span: tuple[int, int]
    evidence_span: tuple[int, int]
    query_syllables: tuple[str, ...]
    evidence_surface: str


@dataclass(frozen=True, order=True)
class SurfaceToken:
    token_id: str
    source_span: tuple[int, int]
    surface: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PackedSegmentationLattice:
    source_sha256: str
    source_length: int
    tokens: tuple[SurfaceToken, ...]

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256 \
                or len(source) != self.source_length:
            return False
        return all(source[a:b] == token.surface
                   for token in self.tokens for a, b in (token.source_span,))


@dataclass(frozen=True)
class SegmentationPaths:
    paths: tuple[tuple[str, ...], ...]
    complete: bool
    explored_states: int


@dataclass(frozen=True)
class SegmentationSummary:
    path_count: int
    saturated: bool
    reachable_token_ids: tuple[str, ...]


@dataclass(frozen=True, order=True)
class HDCAChartCell:
    source_span: tuple[int, int]
    categories: tuple[str, ...]


@dataclass(frozen=True)
class HDCAChart:
    source_sha256: str
    root_span: tuple[int, int]
    root_categories: tuple[str, ...]
    cells: tuple[HDCAChartCell, ...]
    complete: bool
    updates: int
    reason: str


@dataclass(frozen=True)
class HDCAChartView:
    source_span: tuple[int, int]
    chart: HDCAChart


_CHART_CATEGORIES = (
    "A", "ADV", "BA", "CLAUSE", "CONJ", "COP", "D", "H", "N", "NBA", "NBAO",
    "NCOP", "NP", "NPA", "NPASS", "NPC", "NP_D", "P", "PASS", "PP", "Q", "S",
    "SC", "S_D", "T", "V", "VP", "VP_D", "X",
)
_CHART_INDEX = {name: index for index, name in enumerate(_CHART_CATEGORIES)}
_CHART_UNARY = {
    "N": ("NP",), "T": ("NP",), "H": ("NP",), "V": ("VP",), "S": ("CLAUSE",),
}
_CHART_BINARY = {
    ("A", "NP"): ("NP",), ("Q", "N"): ("NP",),
    ("NP", "N"): ("NP",), ("N", "NP"): ("NP",),
    ("NP", "D"): ("NP_D",), ("NP_D", "NP"): ("NP",),
    ("P", "NP"): ("PP",), ("PP", "VP"): ("VP",),
    ("V", "NP"): ("VP",), ("V", "PP"): ("VP",),
    ("VP", "PP"): ("VP",), ("ADV", "VP"): ("VP",), ("VP", "D"): ("VP", "VP_D"),
    ("VP", "VP"): ("VP",), ("S", "VP"): ("S",), ("ADV", "S"): ("S",),
    ("VP_D", "NP"): ("NP",),
    ("S", "D"): ("S_D",), ("S_D", "NP"): ("NP",),
    ("NP", "VP"): ("S",), ("NP", "COP"): ("NCOP",), ("NCOP", "NP"): ("S",),
    ("NP", "PASS"): ("NPASS",), ("NPASS", "VP"): ("S",),
    ("NPASS", "NP"): ("NPA",), ("NPA", "VP"): ("S",),
    ("NP", "BA"): ("NBA",), ("NBA", "NP"): ("NBAO",), ("NBAO", "VP"): ("S",),
    ("NP", "CONJ"): ("NPC",), ("NPC", "NP"): ("NP",),
    ("S", "CONJ"): ("SC",), ("SC", "S"): ("S",),
    ("NP", "PP"): ("NP",), ("NP", "A"): ("NP",),
    ("Q", "NP"): ("NP",), ("NP", "Q"): ("NP",),
}


def _chart_lexical_categories(token: SurfaceToken) -> tuple[str, ...]:
    surface = token.surface
    if surface in ("是", "為", "为"):
        return ("COP",)
    if surface == "被":
        return ("PASS",)
    if surface in ("把", "將", "将"):
        return ("BA",)
    if surface in ("在", "於", "于", "由", "從", "从", "向", "對", "对", "以"):
        return ("P",)
    if surface in ("和", "與", "与", "及", "或", "以及", "並", "并"):
        return ("CONJ",)
    result = set()
    for tag in token.tags:
        if tag.startswith("n") or tag in ("r", "s", "f", "eng"):
            result.add("N")
        elif tag.startswith("v"):
            result.add("V")
        elif tag.startswith("a"):
            result.add("A")
        elif tag in ("m", "q"):
            result.add("Q")
        elif tag.startswith("t"):
            result.add("T")
        elif tag in ("p",):
            result.add("P")
        elif tag in ("c",):
            result.add("CONJ")
        elif tag.startswith("d"):
            result.add("ADV")
        elif tag.startswith("u"):
            result.add("D")
        elif tag == "x":
            result.add("X")
        elif tag.startswith("z"):
            result.add("N")
    if surface in ("這", "这", "那", "此", "其"):
        result.add("N")
    if all("\u3400" <= char <= "\u9fff" for char in surface):
        # Dictionary POS is evidence, not authority. A composed Han lexeme retains a nominal
        # fallback so one bad tag (real example: `首都` tagged `d`) cannot erase the correct parse.
        result.add("N")
        if len(surface) >= 2:
            result.add("V")
    return tuple(sorted(result))


def parse_hdca_chart(source: str, *, hole_span: tuple[int, int] | None = None,
                     max_cells: int = 65_536, max_updates: int = 1_000_000) -> HDCAChart:
    if not source or len(source) > 4096 or max_cells < 1 or max_updates < 1:
        raise ValueError("H-DCA chart input or budget is invalid")
    lattice = build_segmentation_lattice(source, max_tokens=max_cells)
    meaningful = [index for index, char in enumerate(source)
                  if not char.isspace() and char not in _PUNCT]
    if not meaningful:
        return HDCAChart(hashlib.sha256(source.encode()).hexdigest(), (0, 0), (), (), True, 0,
                         "no meaningful token")
    root_span = (min(meaningful), max(meaningful) + 1)
    masks: dict[tuple[int, int], int] = {}

    def add(span: tuple[int, int], category: str) -> bool:
        bit = 1 << _CHART_INDEX[category]
        previous = masks.get(span, 0)
        masks[span] = previous | bit
        return not previous & bit

    for token in lattice.tokens:
        a, b = token.source_span
        if a < root_span[0] or b > root_span[1] or token.tags in (("space",), ("punct",)):
            continue
        if hole_span is not None and a < hole_span[1] and b > hole_span[0]:
            continue
        for category in _chart_lexical_categories(token):
            add((a, b), category)
    if hole_span is not None:
        if not (root_span[0] <= hole_span[0] < hole_span[1] <= root_span[1]):
            raise ValueError("H-DCA hole lies outside the parse root")
        add(hole_span, "H")

    updates = 0
    changed = True
    while changed:
        changed = False
        for span, mask in tuple(masks.items()):
            for source_category, targets in _CHART_UNARY.items():
                if mask & (1 << _CHART_INDEX[source_category]):
                    for target in targets:
                        if add(span, target):
                            updates += 1;changed = True
        left_cells = tuple(masks.items())
        by_start: dict[int, list[tuple[tuple[int, int], int]]] = {}
        for span, mask in left_cells:
            by_start.setdefault(span[0], []).append((span, mask))
        for left_span, left_mask in left_cells:
            for right_span, right_mask in by_start.get(left_span[1], ()):
                combined = (left_span[0], right_span[1])
                for (left_category, right_category), targets in _CHART_BINARY.items():
                    if left_mask & (1 << _CHART_INDEX[left_category]) \
                            and right_mask & (1 << _CHART_INDEX[right_category]):
                        for target in targets:
                            if add(combined, target):
                                updates += 1;changed = True
                                if len(masks) > max_cells or updates > max_updates:
                                    cells = tuple(HDCAChartCell(span, tuple(
                                        name for name in _CHART_CATEGORIES
                                        if mask & (1 << _CHART_INDEX[name])))
                                                  for span, mask in sorted(masks.items()))
                                    return HDCAChart(
                                        hashlib.sha256(source.encode()).hexdigest(), root_span, (),
                                        cells, False, updates, "chart budget exhausted")
    cells = tuple(HDCAChartCell(span, tuple(name for name in _CHART_CATEGORIES
                                            if mask & (1 << _CHART_INDEX[name])))
                  for span, mask in sorted(masks.items()))
    root_mask = masks.get(root_span, 0)
    roots = tuple(name for name in _CHART_CATEGORIES
                  if root_mask & (1 << _CHART_INDEX[name]))
    return HDCAChart(hashlib.sha256(source.encode()).hexdigest(), root_span, roots, cells, True,
                     updates, "complete packed chart fixed point")


def parse_hdca_clause_charts(source: str) -> tuple[HDCAChartView, ...]:
    return tuple(HDCAChartView((start, end), parse_hdca_chart(clause))
                 for start, end, clause in _frame_clauses(source))


def parse_hdca_query_local_chart(question: str) -> HDCAChartView | None:
    hole = compile_cjk_hole(question)
    if hole is None:
        return None
    for start, end, clause in _frame_clauses(question):
        if start <= hole.start < hole.end <= end:
            chart = parse_hdca_chart(
                clause, hole_span=(hole.start - start, hole.end - start))
            return HDCAChartView((start, end), chart)
    return None


class PinyinTable:
    """Explicit language-pack reader. No path discovery, probability or preferred reading."""

    def __init__(self, mapping: dict[str, tuple[str, ...]], resource_sha256: str):
        if not mapping or len(resource_sha256) != 64:
            raise ValueError("pinyin table requires mappings and a resource digest")
        self._mapping = mapping
        self.resource_sha256 = resource_sha256

    @classmethod
    def from_bz2(cls, path: str | Path, *, max_compressed_bytes: int = 1 << 20,
                 max_uncompressed_bytes: int = 8 << 20,
                 max_entries: int = 100_000) -> "PinyinTable":
        resource = Path(path)
        size = resource.stat().st_size
        if size <= 0 or size > max_compressed_bytes:
            raise ValueError("pinyin resource exceeds its compressed-byte budget")
        compressed = resource.read_bytes()
        digest = hashlib.sha256(compressed).hexdigest()
        decompressor = bz2.BZ2Decompressor()
        output = bytearray()
        cursor = 0
        try:
            while cursor < len(compressed) or not decompressor.needs_input:
                chunk = compressed[cursor:cursor + 4096] if decompressor.needs_input else b""
                cursor += len(chunk)
                room = max_uncompressed_bytes + 1 - len(output)
                output.extend(decompressor.decompress(chunk, max_length=max(1, room)))
                if len(output) > max_uncompressed_bytes:
                    raise ValueError("pinyin resource exceeds its uncompressed-byte budget")
                if decompressor.eof:
                    break
            if not decompressor.eof:
                raise ValueError("pinyin resource is truncated")
            if decompressor.unused_data or cursor < len(compressed):
                raise ValueError("pinyin resource has unauthenticated trailing data")
            raw = bytes(output)
        except OSError as exc:
            raise ValueError("pinyin resource is not valid bzip2") from exc
        mapping: dict[str, tuple[str, ...]] = {}
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("pinyin resource is not UTF-8") from exc
        for line in lines:
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 2 or len(fields[0]) != 1:
                continue
            readings = tuple(sorted(set(
                value.casefold() for value in fields[1].split()
                if re.fullmatch(r"[a-züv:]+[1-5]?", value.casefold()))))
            if not readings:
                continue
            mapping[fields[0]] = tuple(sorted(set(mapping.get(fields[0], ())).union(readings)))
            if len(mapping) > max_entries:
                raise ValueError("pinyin resource exceeds its entry budget")
        return cls(mapping, digest)

    def readings(self, surface: str) -> tuple[str, ...]:
        return self._mapping.get(surface, ())

    @property
    def entry_count(self) -> int:
        return len(self._mapping)

    @property
    def toneless_syllables(self) -> frozenset[str]:
        return frozenset(re.sub(r"[1-5]$", "", reading)
                         for readings in self._mapping.values() for reading in readings)


def project_pinyin(source: str, table: PinyinTable) -> PinyinProjection:
    if not source or len(source) > 8192:
        raise ValueError("pinyin projection requires bounded non-empty text")
    atoms = []
    for index, char in enumerate(source):
        toned = table.readings(char) or (char.casefold(),)
        toneless = tuple(sorted(set(re.sub(r"[1-5]$", "", value) for value in toned)))
        atoms.append(PinyinAtom((index, index + 1), char, toned, toneless))
    return PinyinProjection(
        hashlib.sha256(source.encode()).hexdigest(), table.resource_sha256, tuple(atoms))


def pinyin_hdem_fragment(projection: PinyinProjection, prefix: str,
                         *, fact_id: int | None = None) -> HDEMFragment:
    """Lift reversible pinyin gauges into H-DEM domains without choosing a pronunciation."""
    if not prefix or (fact_id is not None and fact_id < 0):
        raise ValueError("pinyin H-DEM fragment needs a prefix and optional valid FactId")
    witnesses = () if fact_id is None else (fact_id,)
    variables, constraints = [], []
    for index, atom in enumerate(projection.atoms):
        stem = f"{prefix}.{index:04d}"
        surface_name, tone_name, plain_name = (
            f"{stem}.surface", f"{stem}.tone", f"{stem}.plain")
        variables.extend((
            HDEMVariable(surface_name, (HDEMValue(atom.surface, witnesses),)),
            HDEMVariable(tone_name, tuple(HDEMValue(value, witnesses) for value in atom.toned)),
            HDEMVariable(plain_name, tuple(HDEMValue(value, witnesses)
                                           for value in atom.toneless)),
        ))
        surface_rows = tuple((atom.surface, reading) for reading in atom.toned)
        tone_rows = tuple(sorted((reading, re.sub(r"[1-5]$", "", reading))
                                 for reading in atom.toned))
        constraints.extend((
            HDEMConstraint(f"{stem}.surface_tone", (surface_name, tone_name),
                           surface_rows, witnesses),
            HDEMConstraint(f"{stem}.tone_plain", (tone_name, plain_name),
                           tone_rows, witnesses),
        ))
    return HDEMFragment(tuple(sorted(variables)), tuple(sorted(constraints)))


_ROMANIZED_PINYIN = re.compile(r"(?<![A-Za-züÜvV:])[A-Za-züÜvV:]+[1-5]?(?![A-Za-z0-9])")


def project_romanized_pinyin(source: str, table: PinyinTable) \
        -> tuple[RomanizedPinyinAtom, ...]:
    if not source or len(source) > 4096:
        raise ValueError("romanized pinyin projection requires bounded non-empty text")
    valid = table.toneless_syllables
    candidates = []
    for match in _ROMANIZED_PINYIN.finditer(source):
        raw = match.group().casefold().replace("u:", "v").replace("ü", "v")
        plain = re.sub(r"[1-5]$", "", raw)
        if plain in valid:
            candidates.append(RomanizedPinyinAtom(
                match.span(), match.group(), raw, raw[-1:].isdigit()))
    if len(candidates) == 1 and not candidates[0].toned:
        return ()
    return tuple(candidates)


def pinyin_bridges(query: str, evidence: str, table: PinyinTable) \
        -> tuple[PinyinBridge, ...]:
    query_atoms = project_romanized_pinyin(query, table)
    if not query_atoms:
        return ()
    evidence_projection = project_pinyin(evidence, table)
    size = len(query_atoms)
    bridges = []
    for start in range(0, len(evidence_projection.atoms) - size + 1):
        window = evidence_projection.atoms[start:start + size]
        if all((query_atom.syllable in atom.toned if query_atom.toned
                else query_atom.syllable in atom.toneless)
               for query_atom, atom in zip(query_atoms, window)):
            a, b = window[0].source_span[0], window[-1].source_span[1]
            bridges.append(PinyinBridge(
                (query_atoms[0].source_span[0], query_atoms[-1].source_span[1]),
                (a, b), tuple(item.syllable for item in query_atoms), evidence[a:b]))
    return tuple(bridges)


def build_segmentation_lattice(source: str, *, max_token_chars: int = 16,
                               max_tokens: int = 65_536) -> PackedSegmentationLattice:
    if not source or len(source) > 8192 or not 1 <= max_token_chars <= 32 \
            or max_tokens < len(source):
        raise ValueError("segmentation lattice input or budget is invalid")
    lexicon = _zh_pos_lexicon()
    tokens = []
    for start, char in enumerate(source):
        candidates: dict[tuple[int, str], set[str]] = {}
        if char.isspace():
            candidates[(start + 1, char)] = {"space"}
        elif char in _PUNCT:
            candidates[(start + 1, char)] = {"punct"}
        else:
            for end in range(start + 1, min(len(source), start + max_token_chars) + 1):
                surface = source[start:end]
                tag = lexicon.get(surface)
                if tag:
                    candidates.setdefault((end, surface), set()).add(tag)
            # Lossless fallback is always present. If the lexicon already knows the character,
            # this merges with that edge rather than creating a duplicate interpretation.
            candidates.setdefault((start + 1, char), set()).add(lexicon.get(char, "x"))
        for (end, surface), tags in sorted(candidates.items()):
            token_id = f"t:{start:04x}:{end:04x}:{hashlib.sha256(surface.encode()).hexdigest()[:12]}"
            tokens.append(SurfaceToken(
                token_id, (start, end), surface, tuple(sorted(tags))))
            if len(tokens) > max_tokens:
                raise ValueError("segmentation lattice exceeds its token budget")
    return PackedSegmentationLattice(
        hashlib.sha256(source.encode()).hexdigest(), len(source), tuple(sorted(tokens)))


def _lattice_edges(lattice: PackedSegmentationLattice,
                   allowed_token_ids: frozenset[str] | None = None) \
        -> dict[int, tuple[SurfaceToken, ...]]:
    allowed = allowed_token_ids
    result: dict[int, list[SurfaceToken]] = {}
    for token in lattice.tokens:
        if allowed is None or token.token_id in allowed:
            result.setdefault(token.source_span[0], []).append(token)
    return {position: tuple(sorted(tokens)) for position, tokens in result.items()}


def enumerate_segmentation_paths(lattice: PackedSegmentationLattice, *, max_paths: int = 1_000_000,
                                 allowed_token_ids: frozenset[str] | None = None) \
        -> SegmentationPaths:
    if max_paths < 1:
        raise ValueError("segmentation path budget must be positive")
    edges = _lattice_edges(lattice, allowed_token_ids)
    paths = []
    explored = 0
    exhausted = False

    def visit(position: int, path: tuple[str, ...]) -> None:
        nonlocal explored, exhausted
        if exhausted:
            return
        explored += 1
        if position == lattice.source_length:
            if len(paths) >= max_paths:
                exhausted = True
            else:
                paths.append(path)
            return
        for token in edges.get(position, ()):
            visit(token.source_span[1], path + (token.token_id,))

    visit(0, ())
    return SegmentationPaths(tuple(paths), not exhausted, explored)


def summarize_segmentation_lattice(lattice: PackedSegmentationLattice, *, max_count: int = 1_000_000,
                                   allowed_token_ids: frozenset[str] | None = None) \
        -> SegmentationSummary:
    if max_count < 1:
        raise ValueError("segmentation count budget must be positive")
    edges = _lattice_edges(lattice, allowed_token_ids)
    forward = {0: 1}
    for position in range(lattice.source_length):
        count = forward.get(position, 0)
        if not count:
            continue
        for token in edges.get(position, ()):
            end = token.source_span[1]
            forward[end] = min(max_count + 1, forward.get(end, 0) + count)
    backward = {lattice.source_length: True}
    for position in range(lattice.source_length - 1, -1, -1):
        backward[position] = any(backward.get(token.source_span[1], False)
                                 for token in edges.get(position, ()))
    reachable = tuple(sorted(token.token_id for start, tokens in edges.items()
                             if forward.get(start, 0) for token in tokens
                             if backward.get(token.source_span[1], False)))
    total = forward.get(lattice.source_length, 0)
    return SegmentationSummary(min(total, max_count), total > max_count, reachable)


def _hdem_world(problem: HDEMProblem, assignment: dict[str, str]) -> HDEMWorld:
    by_variable = {item.name: {value.value: value for value in item.domain}
                   for item in problem.variables}
    provenance = {fact_id for name, value in assignment.items()
                  for fact_id in by_variable[name][value].fact_ids}
    provenance.update(fact_id for item in problem.constraints
                      for fact_id in item.witness_fact_ids)
    return HDEMWorld(
        tuple(sorted(assignment.items())),
        tuple(assignment[name] for name in problem.answer_variables),
        tuple(sorted(provenance)),
    )


def _hdem_result(problem: HDEMProblem, worlds: tuple[HDEMWorld, ...], *, complete: bool,
                 explored_states: int, pruned_values: int, reason: str) -> HDEMResult:
    grouped: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    for world in worlds:
        grouped.setdefault(world.answer, set()).add(world.provenance)
    proofs = tuple(HDEMAnswerProof(answer, tuple(sorted(monomials)))
                   for answer, monomials in sorted(grouped.items()))
    if not complete:
        state, answer = "abstain", None
    elif not proofs:
        state, answer = "abstain", None
    elif len(proofs) == 1:
        state, answer = "resolved", proofs[0].answer
    else:
        state, answer = "contested", None
    return HDEMResult(state, answer, proofs, worlds, complete, explored_states, pruned_values,
                      problem.canonical_sha256(), reason)


def solve_hdem_enumerative(problem: HDEMProblem, *, max_assignments: int = 1_000_000) \
        -> HDEMResult:
    """Obvious finite possible-world oracle. Slow by design; correctness control only."""
    if max_assignments < 1:
        raise ValueError("max_assignments must be positive")
    names = tuple(item.name for item in problem.variables)
    domains = tuple(tuple(value.value for value in item.domain) for item in problem.variables)
    constraints = tuple((item.variables, frozenset(item.allowed)) for item in problem.constraints)
    worlds = []
    explored = 0
    for values in itertools.product(*domains):
        explored += 1
        if explored > max_assignments:
            return _hdem_result(problem, tuple(worlds), complete=False,
                                explored_states=explored - 1, pruned_values=0,
                                reason="enumerative assignment budget exhausted")
        assignment = dict(zip(names, values))
        if all(tuple(assignment[name] for name in variables) in allowed
               for variables, allowed in constraints):
            worlds.append(_hdem_world(problem, assignment))
    return _hdem_result(problem, tuple(sorted(worlds)), complete=True,
                        explored_states=explored, pruned_values=0,
                        reason="complete explicit possible-world enumeration")


def _hdem_propagate(problem: HDEMProblem, domains: dict[str, set[str]]) -> tuple[bool, int]:
    """Generalized arc consistency over extensional relations, to a monotone fixed point."""
    pruned = 0
    changed = True
    while changed:
        changed = False
        for constraint in problem.constraints:
            for position, name in enumerate(constraint.variables):
                supported = {
                    row[position] for row in constraint.allowed
                    if all(row[index] in domains[other]
                           for index, other in enumerate(constraint.variables))
                }
                remove = domains[name].difference(supported)
                if remove:
                    domains[name].difference_update(remove)
                    pruned += len(remove)
                    changed = True
                    if not domains[name]:
                        return False, pruned
    return True, pruned


def solve_hdem_packed(problem: HDEMProblem, *, max_states: int = 100_000,
                      max_worlds: int = 1_000_000) -> HDEMResult:
    """Packed-domain fast path plus lazy splits; must equal the enumerative oracle exactly."""
    if max_states < 1 or max_worlds < 1:
        raise ValueError("H-DEM packed budgets must be positive")
    initial = {item.name: {value.value for value in item.domain} for item in problem.variables}
    worlds: set[HDEMWorld] = set()
    visited: set[tuple[tuple[str, tuple[str, ...]], ...]] = set()
    explored = 0
    pruned_total = 0
    exhausted = False

    def visit(domains: dict[str, set[str]]) -> None:
        nonlocal explored, pruned_total, exhausted
        if exhausted:
            return
        signature = tuple((name, tuple(sorted(values)))
                          for name, values in sorted(domains.items()))
        if signature in visited:
            return
        if explored >= max_states:
            exhausted = True
            return
        visited.add(signature)
        explored += 1
        local = {name: set(values) for name, values in domains.items()}
        consistent, pruned = _hdem_propagate(problem, local)
        pruned_total += pruned
        if not consistent:
            return
        open_names = [name for name, values in local.items() if len(values) > 1]
        if not open_names:
            assignment = {name: next(iter(values)) for name, values in local.items()}
            worlds.add(_hdem_world(problem, assignment))
            if len(worlds) > max_worlds:
                exhausted = True
            return
        name = min(open_names, key=lambda item: (len(local[item]), item))
        for value in sorted(local[name]):
            child = {key: set(values) for key, values in local.items()}
            child[name] = {value}
            visit(child)

    visit(initial)
    ordered = tuple(sorted(worlds)) if not exhausted else tuple(sorted(worlds))[:max_worlds]
    return _hdem_result(
        problem, ordered, complete=not exhausted, explored_states=explored,
        pruned_values=pruned_total,
        reason=("packed GAC fixed point with complete lazy environment split"
                if not exhausted else "packed state/world budget exhausted"),
    )


def _hdca_is_binary_forest(problem: HDEMProblem) -> bool:
    parent = {item.name: item.name for item in problem.variables}

    def root(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    seen_edges = set()
    for constraint in problem.constraints:
        if len(constraint.variables) > 2:
            return False
        if len(constraint.variables) < 2:
            continue
        edge = frozenset(constraint.variables)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        left, right = constraint.variables
        a, b = root(left), root(right)
        if a == b:
            return False
        parent[a] = b
    return True


def solve_hdca(problem: HDEMProblem, *, max_revisions: int = 1_000_000) -> HDCAResult:
    """Edge candidate: integer-bitset arc consistency; cyclic/higher-arity networks abstain."""
    if max_revisions < 1:
        raise ValueError("H-DCA revision budget must be positive")
    by_name = {item.name: item for item in problem.variables}
    value_index = {item.name: {value.value: index for index, value in enumerate(item.domain)}
                   for item in problem.variables}
    masks = {item.name: (1 << len(item.domain)) - 1 for item in problem.variables}
    acyclic = _hdca_is_binary_forest(problem)
    if not acyclic:
        return HDCAResult("abstain", None, tuple(
            (item.name, tuple(value.value for value in item.domain)) for item in problem.variables),
            (), False, 0, problem.canonical_sha256(),
            "cyclic or higher-arity context requires the H-DEM oracle")

    revisions = 0
    changed = True
    while changed:
        changed = False
        for constraint in problem.constraints:
            variables = constraint.variables
            for position, name in enumerate(variables):
                current = masks[name]
                supported_mask = 0
                for row in constraint.allowed:
                    supported = True
                    for other_position, other_name in enumerate(variables):
                        bit = 1 << value_index[other_name][row[other_position]]
                        if not masks[other_name] & bit:
                            supported = False
                            break
                    if supported:
                        supported_mask |= 1 << value_index[name][row[position]]
                revised = current & supported_mask
                removed = (current ^ revised).bit_count()
                if removed:
                    revisions += removed
                    if revisions > max_revisions:
                        domains = tuple((item.name, tuple(
                            value.value for index, value in enumerate(item.domain)
                            if masks[item.name] & (1 << index))) for item in problem.variables)
                        return HDCAResult("abstain", None, domains, (), True, revisions - removed,
                                          problem.canonical_sha256(),
                                          "H-DCA revision budget exhausted")
                    masks[name] = revised
                    changed = True
                    if not revised:
                        domains = tuple((item.name, tuple(
                            value.value for index, value in enumerate(item.domain)
                            if masks[item.name] & (1 << index))) for item in problem.variables)
                        return HDCAResult("abstain", None, domains, (), True, revisions,
                                          problem.canonical_sha256(),
                                          "acyclic context has no consistent assignment")

    domains = tuple((item.name, tuple(value.value for index, value in enumerate(item.domain)
                                      if masks[item.name] & (1 << index)))
                    for item in problem.variables)
    answer_domains = tuple(dict(domains)[name] for name in problem.answer_variables)
    proof = {fact_id for item in problem.constraints for fact_id in item.witness_fact_ids}
    for name in problem.answer_variables:
        variable = by_name[name]
        for index, value in enumerate(variable.domain):
            if masks[name] & (1 << index):
                proof.update(value.fact_ids)
    if all(len(values) == 1 for values in answer_domains):
        answer = tuple(values[0] for values in answer_domains)
        return HDCAResult("resolved", answer, domains, tuple(sorted(proof)), True, revisions,
                          problem.canonical_sha256(), "unique answer bit in acyclic fixed point")
    return HDCAResult("contested", None, domains, tuple(sorted(proof)), True, revisions,
                      problem.canonical_sha256(), "multiple globally extensible answer bits")


_FRAME_HOLE = "◊"
_FRAME_RULES = (
    ("based_on", "hdca.chi.based_on.v1", re.compile(
        r"^(?P<entity>.{1,64}?)(?:以|採用|采用)(?P<basis>.{1,48}?)"
        r"(?:為|为|做為|作为)(?:系統|系统)?基礎(?:的)?$")),
    ("described_as", "hdca.chi.described_as.v1", re.compile(
        r"^(?P<entity>.{1,64}?)(?:被|獲|获)?(?:譽為|誉为|稱為|称为)"
        r"(?P<description>.{1,64})$")),
    ("named_as", "hdca.chi.named_as.v1", re.compile(
        r"^(?P<entity>.{1,64}?)(?:名為|名为|叫做|簡稱|简称)(?P<name>.{1,48})$")),
    ("located_at", "hdca.chi.located_at.v1", re.compile(
        r"^(?P<entity>.{1,64}?)(?:位於|位于|坐落於|坐落于)(?P<location>.{1,64})$")),
    ("copula", "hdca.chi.copula.v1", re.compile(
        r"^(?P<subject>.{1,64}?)(?:是|為|为)(?P<attribute>.{1,64})$")),
)


def _frame_clauses(text: str) -> tuple[tuple[int, int, str], ...]:
    result = []
    for match in re.finditer(r"[^，。！？；,.!?;]+", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            result.append((start, end, text[start:end]))
    return tuple(result)


def compile_hdca_frames(text: str, *, sentence_index: int = 0) -> tuple[HDCAFrame, ...]:
    frames = []
    for clause_start, _clause_end, clause in _frame_clauses(text):
        clause_frames = []
        for constructor, rule_id, pattern in _FRAME_RULES:
            match = pattern.fullmatch(clause)
            if not match:
                continue
            parsed_roles = []
            for role in match.groupdict():
                raw = match.group(role)
                leading = len(raw) - len(raw.lstrip())
                trailing = len(raw) - len(raw.rstrip())
                start = clause_start + match.start(role) + leading
                end = clause_start + match.end(role) - trailing
                parsed_roles.append(HDCAFrameRole(role, (start, end), text[start:end]))
            roles = tuple(sorted(parsed_roles))
            if all(item.surface for item in roles):
                clause_frames.append(HDCAFrame(constructor, roles, rule_id, sentence_index))
        if any(frame.constructor != "copula" for frame in clause_frames):
            clause_frames = [frame for frame in clause_frames if frame.constructor != "copula"]
        frames.extend(clause_frames)
    return tuple(sorted(frames))


def _frame_surface_compatible(left: str, right: str) -> bool:
    a, _ = _normalized(left)
    b, _ = _normalized(right)
    return bool(a and b and (a == b or a in b or b in a))


def hdca_frame_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    hole = compile_cjk_hole(question)
    if hole is None:
        return CJKReadoutResult("unsupported", "", None, "hdca_frame_no_hole")
    gauged = question[:hole.start] + _FRAME_HOLE + question[hole.end:]
    query_frames = compile_hdca_frames(gauged)
    query_open = []
    for frame in query_frames:
        open_roles = tuple(item.role for item in frame.roles if _FRAME_HOLE in item.surface)
        if len(open_roles) == 1:
            query_open.append((frame, open_roles[0]))
    if not query_open:
        return CJKReadoutResult("unsupported", "", None, "hdca_frame_query_unparsed")
    for sentence_index, sentence in enumerate(sentences):
        answers = []
        for evidence_frame in compile_hdca_frames(sentence, sentence_index=sentence_index):
            for query_frame, open_role in query_open:
                if query_frame.constructor != evidence_frame.constructor:
                    continue
                compatible = True
                for query_role in query_frame.roles:
                    if query_role.role == open_role:
                        continue
                    evidence_role = evidence_frame.role(query_role.role)
                    if evidence_role is None or not _frame_surface_compatible(
                            query_role.surface, evidence_role.surface):
                        compatible = False
                        break
                answer_role = evidence_frame.role(open_role)
                if compatible and answer_role is not None and answer_role.surface not in question:
                    answers.append((answer_role, evidence_frame))
        values = {item[0].surface for item in answers}
        if len(values) == 1:
            answer_role, frame = min(answers, key=lambda item: (
                item[0].source_span, item[0].surface, item[1].rule_id))
            candidate = CJKSpanCandidate(
                answer_role.surface, sentence_index, answer_role.source_span, 0, 0, 1.0)
            return CJKReadoutResult(
                "resolved", answer_role.surface, candidate,
                f"hdca_frame_{frame.constructor}")
        if len(values) > 1:
            return CJKReadoutResult("contested", "", None, "hdca_frame_multiple_values")
    return CJKReadoutResult("abstain", "", None, "hdca_frame_unbound")


def compile_cjk_hole(question: str) -> CJKQueryHole | None:
    matches = []
    for surface, kind in _HOLES:
        start = question.find(surface)
        if start >= 0:
            matches.append((start, -len(surface), surface, kind))
    if not matches:
        return None
    start, _negative_length, surface, kind = min(matches)
    if kind == "cause" and _NON_CAUSAL_WHY.search(question):
        # In `称为什么`, 为 belongs to the predicate `称为`; only `什么` is the hole.
        return CJKQueryHole("entity", "什么", start + 1, start + len(surface))
    elif kind == "cause":
        return None  # cause needs a causal operator, not a missing-span interval
    return CJKQueryHole(kind, surface, start, start + len(surface))


def _normalized(text: str) -> tuple[str, tuple[int, ...]]:
    chars, offsets = [], []
    for index, char in enumerate(text):
        if char in _PUNCT:
            continue
        folded = char.casefold()
        chars.extend(folded)
        offsets.extend([index] * len(folded))
    return "".join(chars), tuple(offsets)


def _clause_bound(text: str, index: int, direction: int) -> int:
    cursor = index
    while 0 <= cursor < len(text):
        if text[cursor] in _CLAUSE_END:
            return cursor
        cursor += direction
    return 0 if direction < 0 else len(text)


def _compatible(kind: str, value: str) -> bool:
    if kind == "quantity":
        return any(char in _NUMBER for char in value)
    if kind == "time":
        return (any(char in _NUMBER for char in value) or
                any(unit in value for unit in ("年", "月", "日", "号", "时", "点", "分", "秒")))
    return True


def align_hole_to_sentence(question: str, sentence: str, sentence_index: int = 0) \
        -> CJKSpanCandidate | None:
    hole = compile_cjk_hole(question)
    if hole is None:
        return None
    qnorm, qmap = _normalized(question)
    snorm, smap = _normalized(sentence)
    if not qnorm or not snorm:
        return None
    hole_positions = [i for i, original in enumerate(qmap)
                      if hole.start <= original < hole.end]
    if not hole_positions:
        return None
    hole_start, hole_end = min(hole_positions), max(hole_positions) + 1
    # SequenceMatcher appends a zero-length sentinel at (len(a), len(b)); it is not an
    # evidential anchor and can otherwise create an out-of-range one-sided gap.
    blocks = tuple(block for block in SequenceMatcher(
        None, qnorm, snorm, autojunk=False).get_matching_blocks() if block.size)
    before = [block for block in blocks if block.size and block.a + block.size <= hole_start]
    after = [block for block in blocks if block.size and block.a >= hole_end]
    left = max(before, key=lambda block: (block.a + block.size, block.size), default=None)
    right = min(after, key=lambda block: (block.a, -block.size), default=None)
    if left is None and right is None:
        return None
    start_norm = left.b + left.size if left is not None else 0
    end_norm = right.b if right is not None else len(snorm)
    if start_norm > end_norm:
        return None
    if start_norm == end_norm:
        return None
    start = smap[start_norm]
    end = smap[end_norm - 1] + 1
    if left is None:
        start = _clause_bound(sentence, end - 1, -1)
        if start < len(sentence) and sentence[start] in _CLAUSE_END:
            start += 1
    if right is None:
        end = _clause_bound(sentence, start, 1)
    while start < end and sentence[start] in _PUNCT:
        start += 1
    while end > start and sentence[end - 1] in _PUNCT:
        end -= 1
    value = sentence[start:end].strip()
    if not value or len(value) > 96 or hole.surface in value or not _compatible(hole.kind, value):
        return None
    before_size = left.size if left is not None else 0
    after_size = right.size if right is not None else 0
    score = before_size + after_size - 0.02 * len(value)
    return CJKSpanCandidate(value, sentence_index, (start, end), before_size, after_size, score)


def readout_cjk_span(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    if compile_cjk_hole(question) is None:
        return CJKReadoutResult("unsupported", "", None, "no_supported_interrogative_hole")
    candidates = tuple(candidate for index, sentence in enumerate(sentences)
                       if (candidate := align_hole_to_sentence(question, sentence, index)) is not None)
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "no_transportable_span")
    ranked = sorted(candidates, key=lambda item: (-item.score, len(item.text), item.sentence_index,
                                                   item.source_span, item.text))
    best = ranked[0]
    same_score = {item.text for item in ranked if abs(item.score - best.score) < 1e-12}
    if len(same_score) > 1:
        return CJKReadoutResult("contested", "", None, "equal_action_distinct_spans")
    return CJKReadoutResult("resolved", best.text, best, "minimal_covariant_gap")


def has_strong_transport_certificate(result: CJKReadoutResult) -> bool:
    candidate = result.candidate
    return bool(result.state == "resolved" and candidate is not None
                and candidate.anchor_before >= 2 and candidate.anchor_after >= 2
                and candidate.anchor_before + candidate.anchor_after >= 6
                and 1 <= len(candidate.text) <= 48)


def certified_cjk_readout(question: str, sentences: tuple[str, ...]) -> CJKHybridReadoutResult:
    span = readout_cjk_span(question, sentences)
    if has_strong_transport_certificate(span):
        return CJKHybridReadoutResult(
            "resolved", span.answer, True, span, "strong_bilateral_transport")
    fallback = "\n".join(sentences)
    return CJKHybridReadoutResult(
        "fallback", fallback, False, span, "hpps_fallback_preserves_verified_evidence")


def _counterfactual_candidates(question: str, sentence: str, sentence_index: int) \
        -> tuple[CJKSpanCandidate, ...]:
    hole = compile_cjk_hole(question)
    if hole is None:
        return ()
    qnorm, qmap = _normalized(question)
    snorm, smap = _normalized(sentence)
    positions = [i for i, original in enumerate(qmap) if hole.start <= original < hole.end]
    if not positions or not snorm:
        return ()
    hs, he = min(positions), max(positions) + 1
    blocks = tuple(block for block in SequenceMatcher(
        None, qnorm, snorm, autojunk=False).get_matching_blocks() if block.size)
    lefts = tuple(block for block in blocks if block.a + block.size <= hs)
    rights = tuple(block for block in blocks if block.a >= he)
    raw_ranges: set[tuple[int, int, int, int]] = set()
    for left in lefts:
        for right in rights:
            start_norm, end_norm = left.b + left.size, right.b
            if 0 <= start_norm < end_norm <= len(snorm):
                raw_ranges.add((start_norm, end_norm, left.size, right.size))
    for left in lefts:
        start_norm = left.b + left.size
        if start_norm < len(snorm):
            end_original = _clause_bound(sentence, smap[start_norm], 1)
            end_norm = next((i for i, pos in enumerate(smap) if pos >= end_original), len(snorm))
            if start_norm < end_norm:
                raw_ranges.add((start_norm, end_norm, left.size, 0))
    for right in rights:
        end_norm = right.b
        if end_norm > 0:
            start_original = _clause_bound(sentence, smap[end_norm - 1], -1)
            if start_original < len(sentence) and sentence[start_original] in _CLAUSE_END:
                start_original += 1
            start_norm = next((i for i, pos in enumerate(smap) if pos >= start_original), 0)
            if start_norm < end_norm:
                raw_ranges.add((start_norm, end_norm, 0, right.size))

    candidates = []
    for start_norm, end_norm, before, after in raw_ranges:
        start, end = smap[start_norm], smap[end_norm - 1] + 1
        while start < end and sentence[start] in _PUNCT:
            start += 1
        while end > start and sentence[end - 1] in _PUNCT:
            end -= 1
        value = sentence[start:end].strip()
        if not value or len(value) > 64 or hole.surface in value or not _compatible(hole.kind, value):
            continue
        reconstructed = question[:hole.start] + value + question[hole.end:]
        rnorm, _ = _normalized(reconstructed)
        similarity = SequenceMatcher(None, rnorm, snorm, autojunk=False).ratio()
        anchor_fraction = (before + after) / max(1, len(qnorm) - (he - hs))
        # Global statement fit dominates; conserved anchors break close calls; shorter source
        # action wins when both explain the sentence equally well.
        score = 4.0 * similarity + anchor_fraction - 0.004 * len(value)
        candidates.append(CJKSpanCandidate(
            value, sentence_index, (start, end), before, after, score))
    return tuple(candidates)


def counterfactual_cjk_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    if compile_cjk_hole(question) is None:
        return CJKReadoutResult("unsupported", "", None, "no_supported_interrogative_hole")
    candidates = tuple(candidate for index, sentence in enumerate(sentences)
                       for candidate in _counterfactual_candidates(question, sentence, index))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "no_counterfactual_span")
    ranked = sorted(candidates, key=lambda item: (-item.score, len(item.text), item.sentence_index,
                                                   item.source_span, item.text))
    best = ranked[0]
    tied = {item.text for item in ranked if abs(item.score - best.score) < 1e-12}
    if len(tied) > 1:
        return CJKReadoutResult("contested", "", None, "equal_action_counterfactuals")
    return CJKReadoutResult("resolved", best.text, best, "minimum_counterfactual_action")


def typed_cjk_fiber_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    hole = compile_cjk_hole(question)
    if hole is None or hole.kind not in ("quantity", "time"):
        return CJKReadoutResult("unsupported", "", None, "no_typed_numeric_fiber")
    qnorm, _ = _normalized(question)
    candidates = []
    for sentence_index, sentence in enumerate(sentences):
        seen_spans = set()
        patterns = _TEMPORAL_PATTERNS if hole.kind == "time" \
            else (_ARABIC_MEASURE, _CHINESE_MEASURE)
        for pattern in patterns:
            for match in pattern.finditer(sentence):
                start, end = match.span()
                if (start, end) in seen_spans:
                    continue
                seen_spans.add((start, end))
                value = match.group()
                vnorm, _ = _normalized(value)
                if not vnorm or vnorm in qnorm or len(value) > 32:
                    continue
                masked = sentence[:start] + hole.surface + sentence[end:]
                mnorm, _ = _normalized(masked)
                similarity = SequenceMatcher(None, qnorm, mnorm, autojunk=False).ratio()
                clause_start = _clause_bound(sentence, start, -1)
                if clause_start < len(sentence) and sentence[clause_start] in _CLAUSE_END:
                    clause_start += 1
                clause_end = _clause_bound(sentence, end, 1)
                clause = sentence[clause_start:clause_end]
                query_tokens = set(observe_raw_text(question, question=True).lexical)
                clause_tokens = set(observe_raw_text(clause).lexical)
                overlap = len(query_tokens.intersection(clause_tokens))
                score = 4.0 * similarity + .25 * overlap \
                        - 0.08 * sentence_index - 0.004 * len(value)
                candidates.append(CJKSpanCandidate(
                    value, sentence_index, (start, end), 0, 0, score))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "empty_typed_numeric_fiber")
    ranked = sorted(candidates, key=lambda item: (-item.score, len(item.text), item.sentence_index,
                                                   item.source_span, item.text))
    best = ranked[0]
    tied = {item.text for item in ranked if abs(item.score - best.score) < 1e-12}
    if len(tied) > 1:
        return CJKReadoutResult("contested", "", None, "typed_numeric_fiber_contested")
    return CJKReadoutResult("resolved", best.text, best, f"typed_{hole.kind}_fiber")


def _causal_query_tokens(question: str) -> frozenset[str]:
    stripped = _CAUSAL_QUESTION.sub("", question)
    stripped = re.sub(r"[吗呢啊？?]", "", stripped)
    return frozenset(observe_raw_text(stripped, question=True).lexical)


def _causal_spans(sentence: str) -> tuple[tuple[int, int, str, str], ...]:
    """Compile explicit Chinese causal morphology into source-exact cause/effect spans."""
    proposals: list[tuple[int, int, str, str]] = []

    # RESULT, because CAUSE / because CAUSE, RESULT / in-order-to PURPOSE, ACTION.
    for marker in ("是因為", "是因为", "因為", "因为", "由於", "由于", "緣於", "缘于",
                   "源於", "源于", "得益於", "得益于", "歸因於", "归因于", "因"):
        start = sentence.find(marker)
        if start < 0:
            continue
        cause_start = start + len(marker)
        boundary = _clause_bound(sentence, cause_start, 1)
        # Prefix markers end at the first clause boundary; medial markers normally carry the
        # cause to the end of the clause/sentence. Both remain explicit, separately ranked.
        if boundary > cause_start:
            a, b, value = _strip_span(sentence, cause_start, boundary)
            if value:
                proposals.append((a, b, value, sentence[:start]))
        if start > 0:
            end = _clause_bound(sentence, cause_start, 1)
            a, b, value = _strip_span(sentence, cause_start, end)
            if value:
                proposals.append((a, b, value, sentence[:start]))

    for marker in ("為了", "为了", "為的是", "为的是"):
        start = sentence.find(marker)
        if start >= 0:
            cause_start = start + len(marker)
            end = _clause_bound(sentence, cause_start, 1)
            a, b, value = _strip_span(sentence, cause_start, end)
            if value:
                proposals.append((a, b, value, sentence[end:]))

    # CAUSE leads-to EFFECT: the physically prior clause is the cause.
    for marker in ("導致", "导致", "致使", "使得", "造成", "從而", "从而", "因此", "所以"):
        start = sentence.find(marker)
        if start > 0:
            cursor = start - 1
            while cursor >= 0 and sentence[cursor] in _CLAUSE_END:
                cursor -= 1
            begin = _clause_bound(sentence, cursor, -1)
            if begin < len(sentence) and sentence[begin] in _CLAUSE_END:
                begin += 1
            a, b, value = _strip_span(sentence, begin, start)
            if value:
                proposals.append((a, b, value, sentence[start + len(marker):]))
    # Canonical identity prevents a repeated marker route from voting twice.
    return tuple(dict.fromkeys(proposals))


def _causal_ellipsis_spans(question: str, sentence: str) \
        -> tuple[tuple[int, int, str, str], ...]:
    """Close `ANTECEDENT, EFFECT` only when the effect binds to the why-query.

    Punctuation and the adversative/result connector `而` are observable discourse boundaries.
    The entire contiguous prefix before the best-bound effect clause is conserved; choosing a
    cosmetically shorter fragment would silently discard coordinated causes.
    """
    raw_segments = []
    begin = 0
    for match in re.finditer(r"[，,；;。！？!?]|而(?=[\u3400-\u9fff])", sentence):
        if match.start() > begin:
            a, b, value = _strip_span(sentence, begin, match.start())
            if value:
                raw_segments.append((a, b, value))
        begin = match.end()
    if begin < len(sentence):
        a, b, value = _strip_span(sentence, begin, len(sentence))
        if value:
            raw_segments.append((a, b, value))
    if len(raw_segments) < 2:
        return ()

    stripped = _CAUSAL_QUESTION.sub("", question)
    qnorm, _ = _normalized(stripped)
    qtokens = _causal_query_tokens(question)
    scored = []
    for index, (_start, _end, clause) in enumerate(raw_segments):
        if index == 0:
            continue  # no observable antecedent to the left
        cnorm, _ = _normalized(clause)
        ctokens = set(observe_raw_text(clause).lexical)
        token_overlap = len(qtokens.intersection(ctokens))
        longest = SequenceMatcher(None, qnorm, cnorm, autojunk=False).find_longest_match().size
        score = 4.0 * token_overlap + longest / max(1, len(qnorm)) - .01 * index
        scored.append((score, token_overlap, longest, -index, index))
    if not scored:
        return ()
    best = max(scored)
    if best[1] == 0 and best[2] < 2:
        return ()
    effect_index = best[-1]
    cause_start, cause_end = raw_segments[0][0], raw_segments[effect_index - 1][1]
    a, b, value = _strip_span(sentence, cause_start, cause_end)
    if not value or len(value) > 192:
        return ()
    effect = sentence[raw_segments[effect_index][0]:]
    return ((a, b, value, effect),)


def causal_cjk_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    if not _CAUSAL_QUESTION.search(question) or _NON_CAUSAL_WHY.search(question):
        return CJKReadoutResult("unsupported", "", None, "no_causal_obligation")
    query_tokens = _causal_query_tokens(question)
    explicit_candidates = []
    for sentence_index, sentence in enumerate(sentences):
        for start, end, value, effect_surface in _causal_spans(sentence):
            if not value or len(value) > 128:
                continue
            effect_tokens = set(observe_raw_text(effect_surface).lexical)
            full_tokens = set(observe_raw_text(sentence).lexical)
            overlap = len(query_tokens.intersection(effect_tokens))
            fallback_overlap = len(query_tokens.intersection(full_tokens))
            score = 4.0 * overlap + fallback_overlap - .05 * sentence_index - .002 * len(value)
            explicit_candidates.append(CJKSpanCandidate(
                value, sentence_index, (start, end), overlap, fallback_overlap, score))
    candidates = explicit_candidates
    reason = "typed_causal_edge"
    if not candidates:
        reason = "typed_causal_ellipsis"
        candidates = []
        for sentence_index, sentence in enumerate(sentences):
            for start, end, value, effect_surface in _causal_ellipsis_spans(question, sentence):
                effect_tokens = set(observe_raw_text(effect_surface).lexical)
                overlap = len(query_tokens.intersection(effect_tokens))
                qnorm, _ = _normalized(_CAUSAL_QUESTION.sub("", question))
                enorm, _ = _normalized(effect_surface)
                longest = SequenceMatcher(None, qnorm, enorm, autojunk=False).find_longest_match().size
                score = 4.0 * overlap + longest / max(1, len(qnorm)) \
                        - .05 * sentence_index - .002 * len(value)
                candidates.append(CJKSpanCandidate(
                    value, sentence_index, (start, end), overlap, longest, score))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "causal_edge_unobserved")
    ranked = sorted(candidates, key=lambda item: (-item.score, len(item.text), item.sentence_index,
                                                   item.source_span, item.text))
    best = ranked[0]
    if len(ranked) > 1 and abs(best.score - ranked[1].score) < 1e-12 \
            and best.text != ranked[1].text:
        return CJKReadoutResult("contested", "", None, "causal_edge_contested")
    return CJKReadoutResult("resolved", best.text, best, reason)


@lru_cache(maxsize=1)
def _zh_proper_lexicon() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "lang/china/dict.txt.big"
    result = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip().split()
            if len(fields) >= 3 and fields[2] in ("nr", "ns", "nt", "nz"):
                result[fields[0]] = fields[2]
    return result


@lru_cache(maxsize=1)
def _zh_pos_lexicon() -> dict[str, str]:
    path = _ROOT / "lang/china/dict.txt.big"
    result = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip().split()
            if len(fields) >= 3:
                result[fields[0]] = fields[2]
    return result


def _zhong_tokens(text: str, protected_surfaces: tuple[str, ...] = ()) \
        -> tuple[tuple[str, int, int, tuple[str, ...]], ...]:
    lexicon = _zh_pos_lexicon()
    protected = tuple(sorted(set(protected_surfaces), key=lambda value: (-len(value), value)))
    raw_tokens = []
    cursor = 0
    while cursor < len(text):
        if text[cursor].isspace():
            cursor += 1
            continue
        protected_here = next((value for value in protected if text.startswith(value, cursor)), None)
        latin = re.match(r"[A-Za-zΑ-ω0-9+._-]+", text[cursor:])
        if protected_here is not None:
            end = cursor + len(protected_here)
        elif latin:
            end = cursor + len(latin.group())
        elif not ("\u4e00" <= text[cursor] <= "\u9fff"):
            end = cursor + 1
        else:
            end = cursor + 1
            for candidate_end in range(min(len(text), cursor + 16), cursor, -1):
                if text[cursor:candidate_end] in lexicon:
                    end = candidate_end
                    break
        raw_tokens.append((text[cursor:end], cursor, end))
        cursor = end

    expanded = []
    for surface, start, end in raw_tokens:
        parts = _DEMONSTRATIVE_SPLIT.get(surface)
        if parts is None:
            expanded.append((surface, start, end))
            continue
        offset = start
        for part in parts:
            expanded.append((part, offset, offset + len(part)))
            offset += len(part)

    result = []
    for surface, start, end in expanded:
        if not any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in surface):
            tags = ("PU",)
        elif surface.isdecimal():
            tags = ("CD",)
        elif surface in protected:
            tags = ("NR",)
        else:
            tags = _POS_MAP.get(lexicon.get(surface, ""), ())
            if not tags and len(surface) >= 2 and all("\u4e00" <= char <= "\u9fff"
                                                      for char in surface):
                tags = ("NN",)  # bounded OOV proposal; grammar still decides whether it composes
        result.append((surface, start, end, tags))
    return tuple(result)


def fenci_span_candidates(text: str, *, max_tokens: int = 8,
                          max_chars: int = 48) -> tuple[FenciSpan, ...]:
    if max_tokens < 1 or max_chars < 1:
        raise ValueError("fenci span bounds must be positive")
    tokens = _zhong_tokens(text)
    candidates = []
    seen = set()
    for start_index, (_surface, start, _end, _tags) in enumerate(tokens):
        for end_index in range(start_index, min(len(tokens), start_index + max_tokens)):
            end = tokens[end_index][2]
            if end - start > max_chars:
                break
            left, right, value = _strip_span(text, start, end)
            if not value or value in seen:
                continue
            if not any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in value):
                continue
            seen.add(value)
            tags = tuple(sorted({tag for token in tokens[start_index:end_index + 1]
                                 for tag in token[3]}))
            candidates.append(FenciSpan(
                value, (left, right), end_index - start_index + 1, tags))
    return tuple(candidates)


def pointer_features(text: str, question: str) -> np.ndarray:
    n = len(text)
    matrix = np.zeros((n, len(POINTER_FEATURES)), dtype=np.float64)
    qchars = set(question)
    hole = compile_cjk_hole(question)
    kind = hole.kind if hole is not None else "none"
    tokens = _zhong_tokens(text)
    raw_pos = _zh_pos_lexicon()
    raw_by_position = [set() for _ in range(n)]
    for surface, start, end, _tags in tokens:
        raw = raw_pos.get(surface, "")
        for index in range(start, min(end, n)):
            if raw:
                raw_by_position[index].add(raw)
    for index, char in enumerate(text):
        previous = text[index - 1] if index else ""
        following = text[index + 1] if index + 1 < n else ""
        raw = raw_by_position[index]
        matrix[index] = (
            1.0, float(char in qchars), float(previous in qchars), float(following in qchars),
            float(index > 0 and text[index - 1:index + 1] in question),
            float(index + 1 < n and text[index:index + 2] in question),
            float(char.isdigit()), float("\u4e00" <= char <= "\u9fff"),
            float(char.isascii() and char.isalpha()), float(char in _PUNCT),
            index / max(1, n - 1), float(index == 0 or previous in _CLAUSE_END),
            float(index + 1 == n or following in _CLAUSE_END),
            float(any(tag.startswith("nr") for tag in raw)), float("ns" in raw),
            float(any(tag.startswith("t") for tag in raw)),
            float(any(tag.startswith("n") for tag in raw)),
            float(any(tag.startswith("v") for tag in raw)),
            float(any(tag.startswith("m") or tag.startswith("q") for tag in raw)),
            float(kind == "quantity"), float(kind == "time"), float(kind == "person"),
            float(kind == "place"), float(kind == "entity"),
        )
    return matrix


def _hash_feature(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) % HASH_DIM


def hashed_pointer_indices(text: str, question: str) -> np.ndarray:
    hole = compile_cjk_hole(question)
    kind = hole.kind if hole is not None else "none"
    query_tokens = _zhong_tokens(question)
    verbs = [surface for surface, _start, _end, tags in query_tokens if "VV" in tags]
    predicate = verbs[-1] if verbs else "none"
    raw_pos = _zh_pos_lexicon()
    pos_by_char = ["none"] * len(text)
    for surface, start, end, _tags in _zhong_tokens(text):
        pos = raw_pos.get(surface, "none")
        for index in range(start, min(end, len(text))):
            pos_by_char[index] = pos
    rows = []
    for index, char in enumerate(text):
        prev = text[index - 1] if index else "^"
        nxt = text[index + 1] if index + 1 < len(text) else "$"
        features = (
            "bias", f"c={char}", f"pc={prev}{char}", f"cn={char}{nxt}",
            f"tri={prev}{char}{nxt}", f"kind={kind}|c={char}",
            f"kind={kind}|pos={pos_by_char[index]}", f"pred={predicate}|c={char}",
            f"inq={int(char in question)}|c={char}",
            f"bucket={min(9, int(10 * index / max(1, len(text))))}|kind={kind}",
        )
        rows.append(tuple(_hash_feature(value) for value in features))
    return np.asarray(rows, dtype=np.int32)


def _hashed_scores(weights: np.ndarray, indices: np.ndarray) -> np.ndarray:
    return weights[indices].sum(axis=1)


def predict_hashed_span(model: HashedSpanPointer, text: str, question: str,
                        *, top_k: int = 32) -> CJKReadoutResult:
    if not text:
        return CJKReadoutResult("abstain", "", None, "empty_hashed_pointer_source")
    indices = hashed_pointer_indices(text, question)
    start_scores = _hashed_scores(np.asarray(model.start_weights), indices)
    end_scores = _hashed_scores(np.asarray(model.end_weights), indices)
    starts = np.argsort(start_scores)[-min(top_k, len(text)):]
    ends = np.argsort(end_scores)[-min(top_k, len(text)):]
    candidates = []
    for start in starts:
        for end in ends:
            if end < start or end - start + 1 > model.max_answer_chars:
                continue
            value = text[int(start):int(end) + 1]
            if not value.strip() or any(mark in value for mark in "。！？"):
                continue
            score = float(start_scores[start] + end_scores[end] - .002 * len(value))
            candidates.append((score, len(value), int(start), int(end) + 1, value.strip()))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "hashed_pointer_pair_unavailable")
    score, _length, start, end, value = max(
        candidates, key=lambda row: (row[0], -row[1], -row[2], -row[3]))
    return CJKReadoutResult("resolved", value,
        CJKSpanCandidate(value, 0, (start, end), 0, 0, score), "hashed_span_pointer")


def predict_linear_span(model: LinearSpanPointer, text: str, question: str,
                        *, top_k: int = 32) -> CJKReadoutResult:
    if not text:
        return CJKReadoutResult("abstain", "", None, "empty_pointer_source")
    features = pointer_features(text, question)
    start_scores = features @ np.asarray(model.start_weights)
    end_scores = features @ np.asarray(model.end_weights)
    starts = np.argsort(start_scores)[-min(top_k, len(text)):]
    ends = np.argsort(end_scores)[-min(top_k, len(text)):]
    candidates = []
    for start in starts:
        for end in ends:
            if end < start or end - start + 1 > model.max_answer_chars:
                continue
            value = text[int(start):int(end) + 1]
            if not value.strip() or any(mark in value for mark in "。！？"):
                continue
            score = float(start_scores[start] + end_scores[end] - .002 * len(value))
            candidates.append((score, len(value), int(start), int(end) + 1, value.strip()))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "pointer_pair_unavailable")
    score, _length, start, end, value = max(
        candidates, key=lambda row: (row[0], -row[1], -row[2], -row[3]))
    candidate = CJKSpanCandidate(value, 0, (start, end), 0, 0, score)
    return CJKReadoutResult("resolved", value, candidate, "linear_span_pointer")


def zhong_yy_lattice(text: str, protected_surfaces: tuple[str, ...] = ()) -> str:
    import json
    parts = []
    for index, (surface, start, end, tags) in enumerate(_zhong_tokens(text, protected_surfaces)):
        fields = (f"({index}, {index}, {index + 1}, <{start}:{end}>, 1, "
                  f"{json.dumps(surface, ensure_ascii=False)}, 0, \"null\"")
        if tags:
            weight = 1.0 / len(tags)
            fields += ", " + " ".join(
                f"{json.dumps(tag)} {weight:.8f}" for tag in tags)
        parts.append(fields + ")")
    return " ".join(parts)


@lru_cache(maxsize=65536)
def parse_zhong_mrs(text: str, *, timeout: float = 2.0,
                    protected_surfaces: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not text or len(text) > 1024:
        raise ValueError("Zhong input must be non-empty and <=1024 characters")
    completed = subprocess.run(
        (str(_ZHONG_ACE), "-g", str(_ZHONG_IMAGE), "-y", "-1Tf",
         "--max-chart-megabytes", "256", "--max-unpack-megabytes", "384"),
        input=(zhong_yy_lattice(text, protected_surfaces) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if (completed.returncode != 0 or len(completed.stdout) > 16 * 1024 * 1024
            or len(completed.stderr) > 4 * 1024 * 1024):
        return ()
    try:
        return extract_simplemrs_blocks(completed.stdout)
    except (UnicodeDecodeError, ValueError):
        return ()


class ZhongParserSession:
    """Persistent bounded ACE/Zhong process; avoids reloading the 90MB image per clause."""
    def __init__(self) -> None:
        self._cache: dict[tuple[str, tuple[str, ...]], tuple[str, ...]] = {}
        self._parser = delphin_ace.ACEParser(
            _ZHONG_IMAGE, executable=_ZHONG_ACE, tsdbinfo=False,
            stderr=subprocess.DEVNULL,
            cmdargs=["-y", "-1", "--timeout", "2", "--max-chart-megabytes", "256",
                     "--max-unpack-megabytes", "384"])

    def parse(self, text: str, protected_surfaces: tuple[str, ...] = ()) -> tuple[str, ...]:
        key = (text, protected_surfaces)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            response = self._parser.interact(zhong_yy_lattice(text, protected_surfaces))
            result = tuple(str(item.get("mrs", "")) for item in response.results()
                           if item.get("mrs"))
        except Exception:
            result = ()
        self._cache[key] = result
        return result

    def close(self) -> None:
        self._parser.close()

    def __enter__(self) -> "ZhongParserSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False


def _safe_zhong_worker(connection) -> None:
    try:
        with ZhongParserSession() as parser:
            while True:
                command = connection.recv()
                if command is None:
                    return
                text, protected = command
                connection.send(parser.parse(text, protected))
    except (EOFError, BrokenPipeError):
        return
    finally:
        connection.close()


class SafeZhongParserSession:
    """Persistent parser behind a killable process boundary and parent-enforced deadline."""
    def __init__(self, *, timeout: float = 0.2) -> None:
        if timeout <= 0:
            raise ValueError("Zhong parser timeout must be positive")
        self.timeout = timeout
        self._cache: dict[tuple[str, tuple[str, ...]], tuple[str, ...]] = {}
        self._context = multiprocessing.get_context("fork")
        self._parent = None
        self._process = None
        self._start()

    def _start(self) -> None:
        parent, child = self._context.Pipe()
        process = self._context.Process(target=_safe_zhong_worker, args=(child,), daemon=True)
        process.start()
        child.close()
        self._parent, self._process = parent, process

    def _restart(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)
        if self._parent is not None:
            self._parent.close()
        self._start()

    def parse(self, text: str, protected_surfaces: tuple[str, ...] = ()) -> tuple[str, ...]:
        key = (text, protected_surfaces)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            self._parent.send(key)
            if not self._parent.poll(self.timeout):
                self._restart()
                result = ()
            else:
                result = tuple(self._parent.recv())
        except (EOFError, BrokenPipeError, OSError):
            self._restart()
            result = ()
        self._cache[key] = result
        return result

    def close(self) -> None:
        if self._parent is not None and self._process is not None:
            try:
                if self._process.is_alive():
                    self._parent.send(None)
                    self._process.join(timeout=1.0)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=1.0)
            except (EOFError, BrokenPipeError, OSError):
                pass
            self._parent.close()

    def __enter__(self) -> "SafeZhongParserSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False


def _parse_zhong_units_with(parse, text: str, *, question: bool,
                            protected_surfaces: tuple[str, ...]) \
        -> tuple[tuple[int, int, tuple[str, ...]], ...]:
    if len(text) <= 48:
        variants = _zhong_query_variants(text) if question else (text,)
        for variant in variants:
            blocks = parse(variant, protected_surfaces)
            if blocks:
                return ((0, len(text), blocks),)
    units = []
    for match in tuple(re.finditer(r"[^，；。！？!?;]+", text))[:4]:
        clause = match.group().strip()
        if len(clause) < 2 or len(clause) > 64:
            continue
        variants = _zhong_query_variants(clause) if question else (clause,)
        for variant in variants:
            local_protected = tuple(value for value in protected_surfaces if value in clause)
            blocks = parse(variant, local_protected)
            if blocks:
                units.append((match.start(), match.end(), blocks))
                break
    return tuple(units)


def _zhong_query_gauge(text: str) -> tuple[str, int | None]:
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    pattern = re.compile(r"哪([一二两三四五六七八九十]|\d+)(个|位|家|种|部|项|名)?")
    match = pattern.search(text)
    if match is None:
        return text, None
    raw = match.group(1)
    cardinality = int(raw) if raw.isdecimal() else numerals.get(raw)
    classifier = match.group(2) or "个"
    normalized = text[:match.start()] + "哪" + classifier + text[match.end():]
    return normalized, cardinality


def _zhong_query_variants(text: str) -> tuple[str, ...]:
    variants = [text]
    gauged, _cardinality = _zhong_query_gauge(text)
    if gauged not in variants:
        variants.append(gauged)
    hole = compile_cjk_hole(text)
    if hole is not None:
        if hole.kind == "person":
            placeholder = "他"
        elif hole.kind == "place":
            placeholder = "北京"
        elif hole.kind == "time":
            placeholder = "今天"
        elif hole.kind == "quantity":
            placeholder = "一"
        else:
            following = text[hole.end:hole.end + 2]
            placeholder = "一个" if following and following[0] not in "是有为叫称" else "东西"
        declarative = text[:hole.start] + placeholder + text[hole.end:]
        if declarative not in variants:
            variants.append(declarative)
    return tuple(variants)


@lru_cache(maxsize=65536)
def parse_zhong_semantic_units(text: str, *, question: bool = False,
                               protected_surfaces: tuple[str, ...] = ()) \
        -> tuple[tuple[int, int, tuple[str, ...]], ...]:
    """Parse whole text, then bounded clauses; local MRS spans remain transportable by offset."""
    return _parse_zhong_units_with(
        lambda value, protected: parse_zhong_mrs(
            value, protected_surfaces=protected),
        text, question=question, protected_surfaces=protected_surfaces)


def shared_zhong_nominal_anchors(question: str, sentences: tuple[str, ...]) -> tuple[str, ...]:
    """Exact shared surfaces whose unprotected tokenization contains no verb/particle channel."""
    proposals = set()
    for sentence in sentences:
        matcher = SequenceMatcher(None, question, sentence, autojunk=False)
        for block in matcher.get_matching_blocks():
            if 2 <= block.size <= 12:
                surface = question[block.a:block.a + block.size].strip("".join(_PUNCT))
                if len(surface) >= 2:
                    proposals.add(surface)
    accepted = []
    for surface in sorted(proposals, key=lambda value: (-len(value), value)):
        tokens = _zhong_tokens(surface)
        tags = {tag for _word, _start, _end, values in tokens for tag in values}
        if tags and tags <= {"NN", "NR", "JJ", "VA", "CD", "FW"}:
            if not any(surface in old or old in surface for old in accepted):
                accepted.append(surface)
    return tuple(sorted(accepted))


def _zhong_query_role(question: str, protected: tuple[str, ...],
                      parser: ZhongParserSession | None = None) \
        -> tuple[str, str] | None:
    hole = compile_cjk_hole(question)
    if hole is None or hole.kind not in ("person", "entity", "place"):
        return None
    blocks = (parser.parse(question, protected) if parser is not None
              else parse_zhong_mrs(question, protected_surfaces=protected))
    if not blocks:
        return None
    try:
        semantic = decode_simplemrs_blocks(blocks[0].encode("utf-8"))[0]
    except Exception:
        return None
    overlapping = [ep for ep in semantic.rels if ep.lnk is not None and
                   ep.lnk.type == lnk.Lnk.CHARSPAN and ep.lnk.data[0] < hole.end and
                   ep.lnk.data[1] > hole.start and "ARG0" in ep.args]
    if not overlapping:
        return None
    target = overlapping[0].args["ARG0"]
    # `哪个公司`: which modifies the head noun through compound_p; transport to that head.
    changed = True
    while changed:
        changed = False
        for ep in semantic.rels:
            if ep.predicate == "compound_p" and ep.args.get("ARG2") == target:
                target = ep.args.get("ARG1", target)
                changed = True
                break
    candidates = []
    for ep in semantic.rels:
        if ep.predicate in ("compound_p", "exist_q", "proper_q", "pronoun_q", "predsort"):
            continue
        for role, value in ep.args.items():
            if role != "ARG0" and value == target:
                candidates.append((ep.predicate, role))
    unique = tuple(sorted(set(candidates)))
    return unique[0] if len(unique) == 1 else None


def zhong_mrs_role_readout(question: str, sentences: tuple[str, ...], *,
                           parser: ZhongParserSession | None = None) -> CJKReadoutResult:
    protected = shared_zhong_nominal_anchors(question, sentences)
    query_role = _zhong_query_role(question, protected, parser)
    if query_role is None:
        return CJKReadoutResult("unsupported", "", None, "zhong_query_role_unavailable")
    predicate, role = query_role
    answers = []
    for sentence_index, sentence in enumerate(sentences):
        units = (_parse_zhong_units_with(parser.parse, sentence, question=False,
                                         protected_surfaces=protected)
                 if parser is not None else parse_zhong_semantic_units(
                     sentence, protected_surfaces=protected))
        for unit_start, _unit_end, blocks in units:
            for block in blocks:
                try:
                    semantic = decode_simplemrs_blocks(block.encode("utf-8"))[0]
                except Exception:
                    continue
                for relation in semantic.rels:
                    if relation.predicate != predicate or role not in relation.args:
                        continue
                    target = relation.args[role]
                    referents = [ep for ep in semantic.rels if ep.args.get("ARG0") == target
                                 and ep.lnk is not None and ep.lnk.type == lnk.Lnk.CHARSPAN
                                 and ep.predicate not in ("exist_q", "proper_q", "pronoun_q")]
                    # In compounds the predicate role points to the typed head (`公司`), while
                    # the answer is its source-exact modifier+head span (`光荣公司`).
                    referents.extend(ep for ep in semantic.rels
                                     if ep.predicate == "compound_p"
                                     and ep.args.get("ARG1") == target and ep.lnk is not None
                                     and ep.lnk.type == lnk.Lnk.CHARSPAN)
                    for referent in referents:
                        start, end = referent.lnk.data
                        absolute = (unit_start + start, unit_start + end)
                        if not (0 <= absolute[0] < absolute[1] <= len(sentence)):
                            continue
                        text = sentence[absolute[0]:absolute[1]]
                        if text and text not in question:
                            answers.append(CJKSpanCandidate(
                                text, sentence_index, absolute, 0, 0, 1.0))
    values = {answer.text for answer in answers}
    if not values:
        return CJKReadoutResult("abstain", "", None, "zhong_role_unbound")
    if len(values) != 1:
        return CJKReadoutResult("contested", "", None, "zhong_role_contested")
    answer = min(answers, key=lambda item: (item.sentence_index, item.source_span))
    return CJKReadoutResult("resolved", answer.text, answer, "zhong_mrs_role_binding")


def _strip_span(sentence: str, start: int, end: int) -> tuple[int, int, str]:
    while start < end and sentence[start] in _PUNCT:
        start += 1
    while end > start and sentence[end - 1] in _PUNCT:
        end -= 1
    return start, end, sentence[start:end].strip()


def lightweight_fenci_role_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    """Millisecond role transport over exact fenci tokens; Zhong is its offline oracle."""
    hole = compile_cjk_hole(question)
    if hole is None or hole.kind not in ("person", "place", "entity"):
        return CJKReadoutResult("unsupported", "", None, "no_lightweight_role")
    qtokens = _zhong_tokens(question)
    hole_tokens = [index for index, (_surface, start, end, _tags) in enumerate(qtokens)
                   if start < hole.end and end > hole.start]
    if not hole_tokens:
        return CJKReadoutResult("abstain", "", None, "hole_not_tokenized")
    hole_index = hole_tokens[0]
    verbs = [(index, token) for index, token in enumerate(qtokens)
             if "VV" in token[3] or token[0] in ("是", "有", "在", "为", "叫", "称")]
    if not verbs:
        return CJKReadoutResult("abstain", "", None, "predicate_unobserved")
    verb_index, predicate = min(verbs, key=lambda row: (abs(row[0] - hole_index), row[0]))
    predicate_surface = predicate[0]
    prefix = question[:hole.start]
    marker = next((value for value in ("由", "被") if prefix.rfind(value) >= 0), None)
    locative = hole.kind == "place" and prefix.rstrip().endswith("在")
    proposals = []
    for sentence_index, sentence in enumerate(sentences):
        for match in re.finditer(re.escape(predicate_surface), sentence):
            pred_start, pred_end = match.span()
            if marker is not None:
                marker_start = sentence.rfind(marker, 0, pred_start)
                if marker_start < 0:
                    continue
                start, end = marker_start + len(marker), pred_start
            elif hole_index < verb_index:
                start = max(sentence.rfind(mark, 0, pred_start) for mark in _CLAUSE_END) + 1
                end = pred_start
                if hole.kind == "person":
                    spans = _proper_spans(sentence[start:end], frozenset(("nr",)))
                    if spans:
                        a, b, _value, _tag = spans[-1]
                        old_start = start
                        start, end = old_start + a, old_start + b
            else:
                start = pred_end
                if locative:
                    at = sentence.find("在", pred_end)
                    if at >= 0:
                        start = at + 1
                end = _clause_bound(sentence, start, 1)
            start, end, value = _strip_span(sentence, start, end)
            if not value or len(value) > 48 or value in question:
                continue
            if hole.kind == "place":
                spans = _proper_spans(value, frozenset(("ns",)))
                if spans:
                    a, b, typed_value, _tag = spans[-1]
                    old_start = start
                    start, end, value = old_start + a, old_start + b, typed_value
            proposals.append(CJKSpanCandidate(
                value, sentence_index, (start, end), 0, 0, 1.0 - 0.05 * sentence_index))
    values = {item.text for item in proposals}
    if not values:
        return CJKReadoutResult("abstain", "", None, "lightweight_role_unbound")
    if len(values) > 1:
        return CJKReadoutResult("contested", "", None, "lightweight_role_contested")
    candidate = min(proposals, key=lambda item: (item.sentence_index, item.source_span))
    return CJKReadoutResult("resolved", candidate.text, candidate, "lightweight_fenci_role")


def _proper_spans(sentence: str, allowed: frozenset[str]) -> tuple[tuple[int, int, str, str], ...]:
    lexicon = _zh_proper_lexicon()
    candidates = []
    for start in range(len(sentence)):
        for end in range(min(len(sentence), start + 12), start + 1, -1):
            value = sentence[start:end]
            tag = lexicon.get(value)
            if tag in allowed:
                candidates.append((start, end, value, tag))
                break  # longest typed token at this start
    for match in re.finditer(r"[A-Za-zΑ-ω][A-Za-zΑ-ω0-9+._-]{1,31}", sentence):
        candidates.append((match.start(), match.end(), match.group(), "nz"))
    # Longer overlapping typed spans dominate shorter substrings.
    ordered = sorted(candidates, key=lambda row: (row[0], -(row[1] - row[0]), row[2]))
    kept = []
    for row in ordered:
        if any(row[0] >= old[0] and row[1] <= old[1] for old in kept):
            continue
        kept.append(row)
    return tuple(sorted(kept))


def typed_cjk_entity_fiber_readout(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    hole = compile_cjk_hole(question)
    if hole is None or hole.kind not in ("person", "place", "entity"):
        return CJKReadoutResult("unsupported", "", None, "no_typed_entity_fiber")
    allowed = {"person": frozenset(("nr",)), "place": frozenset(("ns",)),
               "entity": frozenset(("nr", "ns", "nt", "nz"))}[hole.kind]
    qnorm, _ = _normalized(question)
    candidates = []
    require_pair = hole.surface in ("哪两个",)
    for sentence_index, sentence in enumerate(sentences):
        spans = [row for row in _proper_spans(sentence, allowed)
                 if _normalized(row[2])[0] not in qnorm]
        expanded = list(spans)
        if require_pair:
            expanded = []
            for left, right in zip(spans, spans[1:]):
                bridge = sentence[left[1]:right[0]]
                if len(bridge) <= 3 and any(mark in bridge for mark in ("和", "与", "、", "及")):
                    expanded.append((left[0], right[1], sentence[left[0]:right[1]], "pair"))
        for start, end, value, _tag in expanded:
            masked = sentence[:start] + hole.surface + sentence[end:]
            mnorm, _ = _normalized(masked)
            similarity = SequenceMatcher(None, qnorm, mnorm, autojunk=False).ratio()
            score = 4.0 * similarity - 0.08 * sentence_index - 0.004 * len(value)
            candidates.append(CJKSpanCandidate(value, sentence_index, (start, end), 0, 0, score))
    if not candidates:
        return CJKReadoutResult("abstain", "", None, "empty_typed_entity_fiber")
    ranked = sorted(candidates, key=lambda item: (-item.score, len(item.text), item.sentence_index,
                                                   item.source_span, item.text))
    best = ranked[0]
    tied = {item.text for item in ranked if abs(item.score - best.score) < 1e-12}
    if len(tied) > 1:
        return CJKReadoutResult("contested", "", None, "typed_entity_fiber_contested")
    return CJKReadoutResult("resolved", best.text, best, f"typed_{hole.kind}_fiber")


def cjk_direct_answer_program(question: str, sentences: tuple[str, ...]) -> CJKReadoutResult:
    """Living-program v6: only transferred time fiber; rejected types cannot preempt fallback."""
    causal = causal_cjk_readout(question, sentences)
    if causal.state == "resolved":
        return causal
    typed = typed_cjk_fiber_readout(question, sentences)
    if typed.state == "resolved" and typed.reason == "typed_time_fiber":
        return typed
    lightweight = lightweight_fenci_role_readout(question, sentences)
    if lightweight.state == "resolved":
        return lightweight
    local = readout_cjk_span(question, sentences)
    if local.state == "resolved":
        return CJKReadoutResult(
            "resolved", local.answer, local.candidate, "local_proof_dominance")
    counterfactual = counterfactual_cjk_readout(question, sentences)
    if counterfactual.state == "resolved":
        return counterfactual
    return local if local.state in ("abstain", "contested") else counterfactual


def cjk_evidence_rank_dominance_program(question: str, sentences: tuple[str, ...]) \
        -> CJKReadoutResult:
    """A later retrieval rank may fill an open obligation, never overwrite a closed local proof."""
    for rank, sentence in enumerate(sentences):
        result = cjk_direct_answer_program(question, (sentence,))
        if result.state == "resolved":
            candidate = (replace(result.candidate, sentence_index=rank)
                         if result.candidate is not None else None)
            return replace(result, candidate=candidate)
    return cjk_direct_answer_program(question, sentences)


__all__ = ["CJKHybridReadoutResult", "CJKQueryHole", "CJKReadoutResult", "CJKSpanCandidate",
           "HDEMAnswerProof", "HDEMConstraint", "HDEMProblem", "HDEMResult", "HDEMValue",
           "HDEMVariable", "HDEMWorld", "solve_hdem_enumerative", "solve_hdem_packed",
           "HDCAResult", "solve_hdca",
           "HDCAFrame", "HDCAFrameRole", "compile_hdca_frames", "hdca_frame_readout",
           "PinyinAtom", "PinyinProjection", "PinyinTable", "project_pinyin",
           "HDEMFragment", "pinyin_hdem_fragment",
           "PinyinBridge", "RomanizedPinyinAtom", "pinyin_bridges",
           "project_romanized_pinyin",
           "PackedSegmentationLattice", "SegmentationPaths", "SegmentationSummary",
           "SurfaceToken", "build_segmentation_lattice", "enumerate_segmentation_paths",
           "summarize_segmentation_lattice",
           "HDCAChart", "HDCAChartCell", "parse_hdca_chart",
           "HDCAChartView", "parse_hdca_clause_charts", "parse_hdca_query_local_chart",
           "FenciSpan", "fenci_span_candidates", "HashedSpanPointer", "HASH_DIM",
           "hashed_pointer_indices", "LinearSpanPointer", "POINTER_FEATURES",
           "pointer_features", "predict_hashed_span", "predict_linear_span",
           "FenciSpan", "fenci_span_candidates",
           "align_hole_to_sentence", "certified_cjk_readout", "compile_cjk_hole",
           "cjk_direct_answer_program", "counterfactual_cjk_readout",
           "cjk_evidence_rank_dominance_program",
           "causal_cjk_readout",
           "has_strong_transport_certificate", "readout_cjk_span", "typed_cjk_fiber_readout",
           "typed_cjk_entity_fiber_readout", "parse_zhong_mrs", "parse_zhong_semantic_units",
           "SafeZhongParserSession", "shared_zhong_nominal_anchors", "ZhongParserSession",
           "lightweight_fenci_role_readout", "zhong_mrs_role_readout", "zhong_yy_lattice"]
