# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Corpus-derived Chinese anchor stopwords for `supersession_collapse._cjk_anchors`.

Mirrors `lab/zh_anchor_stopwords.py` verbatim (`lab/` is gitignored, never shipped, so this is a
real copy, not an import) -- see that file's own docstring for the full derivation: document
frequency of every real `_cjk_anchors()` output token across 282 `(scenario, noise variant)`
pairs in `dataset_chat/domains_zh` + `dataset_chat/domains_lh_zh`, threshold >=5%. Not a
linguistic stopword list -- these are corpus-specific slang/discourse-filler intensifiers
("666", "cap"/"no", "直接", "卧槽", "家人们"...) confirmed to drive spurious false-positive group
detection the same way an unfiltered "Final" from "Final answer?" did once for English.
"""
from __future__ import annotations

ZH_ANCHOR_STOPWORDS: frozenset[str] = frozenset({
    "666", "cap", "no",
    "不是", "不用", "什么情", "今天", "今晚", "兄弟们", "到了", "卧槽", "去了", "可以", "周末",
    "哈哈哈", "啊啊啊", "大家", "好哦", "家人们", "小时", "必须", "怎么回", "成功", "我们",
    "我哭了", "打工人", "时间", "服了", "狠狠", "现在", "直接", "破防了", "离谱", "等等", "终于",
    "绝了", "老天爷", "老板", "蚌埠住", "行吧", "要命了", "这也太", "这波", "那我", "非常", "顶了",
})

__all__ = ["ZH_ANCHOR_STOPWORDS"]
