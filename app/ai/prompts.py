"""Prompt templates and the output schema for AI analyses (PLAN §26).

Bump PROMPT_VERSION on any change here: it is part of the cache key, so old results invalidate.
The model only interprets numbers the backend already computed; it never does the math.
"""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

PROMPT_VERSION = 2

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Insight(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["observation", "interpretation", "hypothesis"]
    text: Text
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class Analysis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: Text
    insights: Annotated[list[Insight], Field(min_length=1, max_length=6)]
    caveats: Annotated[list[Text], Field(max_length=4)] = []
    data_sufficiency: Literal["sufficient", "limited", "insufficient"]


SYSTEM = """You analyse one runner's training data for that runner. Rules:
- Use ONLY the JSON data provided. Never invent values, dates, events or causes.
- All numbers are already computed. Quote them; do not recompute or extrapolate.
- Tag every insight: "observation" = directly in the data; "interpretation" = a reasonable \
reading of several observations; "hypothesis" = a possible explanation that the data cannot confirm.
- "evidence" must cite the specific values the insight relies on.
- `runner` describes the athlete: age, body, weekly schedule (runs + gym strength sessions), \
how long they have trained seriously, and their recent volume. Judge every number against it: \
a novice-to-intermediate male aged ~24 running 3x/week with 3 gym sessions, so fatigue from \
strength work and a young training age matter. Gym sessions are not in the data: never assume \
what they contained; at most mention them as a possible factor (a "hypothesis").
- Fields that are missing or null are unknown: say so instead of guessing.
- No forecasts, no race-time predictions.
- This is training-data analysis, not medicine: never diagnose or name medical conditions. If \
something looks concerning (e.g. unusual heart rate), say it is unusual in the data and suggest \
consulting a qualified professional.
- Be concise and specific. Second person ("you"). English.
- Reply with ONE JSON object and nothing else, exactly this shape:
{"summary": str (<= 3 sentences), "insights": [{"kind": "observation"|"interpretation"|\
"hypothesis", "text": str, "evidence": str}] (1-6 items, most useful first), "caveats": [str] \
(0-4 items, data limitations), "data_sufficiency": "sufficient"|"limited"|"insufficient"}"""

TASKS: dict[str, str] = {
    "activity": (
        "Analyse this single run. Focus on pacing across splits, heart-rate response and "
        "drift, time in zones, and how it compares with the similar recent runs listed. "
        "Mention flagged data-quality issues."
    ),
    "period": (
        "Analyse this training period versus the previous period of equal length. Focus on "
        "volume and consistency, load changes flagged in `flags`, intensity distribution, "
        "and the steady-run pace/efficiency trends (only when `trend` is present). Mention "
        "personal records set in the period."
    ),
}


def build_messages(kind: str, context: dict[str, Any]) -> list[dict[str, str]]:
    data = json.dumps(context, separators=(",", ":"), ensure_ascii=False)
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{TASKS[kind]}\n\nDATA:\n{data}"},
    ]


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_analysis(content: str) -> Analysis:
    """Lenient extraction (reasoning tags, code fences, prose around the object), strict schema.

    Raises ValueError (pydantic's ValidationError is one) when no valid object can be recovered.
    """
    text = _THINK.sub("", content)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return Analysis.model_validate(json.loads(text[start : end + 1]))
