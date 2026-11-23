"""Per-run accounting of reported model usage, including incomplete calls."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


def valid_usage(value):
    if not isinstance(value, dict):
        return None
    keys = ("input_tokens", "output_tokens", "total_tokens")
    vals = [value.get(k) for k in keys]
    if any(type(n) is not int or n < 0 for n in vals) or vals[2] != vals[0] + vals[1]:
        return None
    return dict(zip(keys, vals))


class SummaryText(str):
    def __new__(cls, text, usage):
        obj = super().__new__(cls, text)
        obj.usage = valid_usage(usage)
        return obj


class SearchResult(dict):
    def __init__(self, data, usage):
        super().__init__(data)
        self.usage = valid_usage(usage)


@dataclass
class UsageTracker:
    emit: object
    measurement: str
    parts: dict = field(default_factory=lambda: {
        kind: {"calls": 0, "reported": 0, "missing": 0, "pending": 0,
               "not_dispatched": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for kind in ("compression", "generation", "web_search")
    })

    async def publish(self):
        await self.emit("run_usage", json.dumps({"measurement": self.measurement, "parts": self.parts}))

    async def start(self, kind):
        part = self.parts[kind]
        part["calls"] += 1
        part["pending"] += 1
        await self.publish()

    async def finish(self, kind, usage=None, not_dispatched=False):
        part = self.parts[kind]
        part["pending"] -= 1
        usage = valid_usage(usage)
        if usage is not None:
            part["reported"] += 1
            for key, value in usage.items():
                part[key] += value
        elif not_dispatched:
            part["not_dispatched"] += 1
        else:
            part["missing"] += 1
        await self.publish()
