"""Validated output contract with a bounded re-ask (wq#2489).

Anywhere the fleet parses a contract line out of LLM prose (`RECOMMENDATION: ADOPT`),
a miss is a FORM failure, not a verdict. This module keeps the two apart: the caller
declares the contract as a pydantic model, and a text that does not satisfy it comes
back as status 'rerun' (or 'unavailable' when the re-ask itself could not run). It
never comes back as a decision. pydantic is used for validation only, no framework.

    class Rec(BaseModel):
        recommendation: Literal['ADOPT', 'WATCH', 'SKIP']

    res = parse_or_reask(note_text, Rec)                 # parse only
    res = parse_or_reask(note_text, Rec, reask=ask_llm)  # one bounded re-ask

Field `recommendation` is read from a line `RECOMMENDATION: <token>`. The label match
is line-anchored, case-insensitive, tolerates markdown decoration (`**RECOMMENDATION: X**`,
`## RECOMMENDATION: X`, `> - RECOMMENDATION: X`), and the LAST such line wins, because the
verdict is written at the end of a note and earlier lines are prose.

The re-ask callable gets the validation error text verbatim and nothing else. It returns
the new text, or None when the call could not be made (gateway down), which is
'unavailable' and never a verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError, field_validator

TAIL_CHARS = 300


@dataclass
class ContractResult:
    status: str                      # 'ok' | 'rerun' | 'unavailable'
    value: Optional[BaseModel] = None
    error: str = ""                  # pydantic's message, verbatim, when not ok
    reasks: int = 0
    tail: str = ""                   # text after the last matched contract line's token


class Recommendation(BaseModel):
    """The research/pilot verdict contract shared by the defer pipeline."""
    recommendation: str

    @field_validator("recommendation", mode="before")
    @classmethod
    def _upper_known(cls, v):
        v = str(v).strip().upper()
        if v not in ("ADOPT", "WATCH", "SKIP"):
            raise ValueError("recommendation must be one of ADOPT, WATCH, SKIP")
        return v


def _line_re(label: str) -> "re.Pattern[str]":
    return re.compile(
        r"(?im)^[\s>#*_\-]*" + re.escape(label) + r"\s*:\s*[*_`]*\s*([A-Za-z0-9_\-]+)")


def extract_fields(text: str, model: Type[BaseModel]) -> Tuple[dict, int]:
    """One `LABEL: token` per declared field, last match wins. Returns (data, end offset)."""
    data, end = {}, 0
    for name in model.model_fields:
        hits = list(_line_re(name).finditer(text))
        if hits:
            data[name] = hits[-1].group(1)
            end = max(end, hits[-1].end())
    return data, end


def parse_or_reask(text: str, model: Type[BaseModel], *,
                   reask: Optional[Callable[[str], Optional[str]]] = None,
                   max_reasks: int = 1) -> ContractResult:
    reasks = 0
    while True:
        data, end = extract_fields(text, model)
        try:
            return ContractResult("ok", model(**data), "", reasks, text[end:end + TAIL_CHARS])
        except ValidationError as e:
            err = str(e)
        if reask is None or reasks >= max_reasks:
            return ContractResult("rerun", None, err, reasks)
        new = reask(err)
        if new is None:
            return ContractResult("unavailable", None, err, reasks)
        reasks += 1
        text = new
