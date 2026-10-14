"""Numeric snapshots of one locally retained Codex session tree.

Adapted from Team Mode's usage_by_model.py: last-token snapshots, cumulative
duplicate evidence, and response windows with observed tool time removed.
No message text, tool arguments, local paths, pricing, or raw events are returned.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re


SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens")
LOG_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _timestamp(value):
    if type(value) in (int, float) and math.isfinite(value):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _events(path):
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                yield None
                continue
            yield event if isinstance(event, dict) and isinstance(event.get("payload"), dict) else None


def _metadata(path):
    # Metadata is at the beginning of rollout files. Do not read unrelated chats
    # in full just to discover whether they belong to this tree.
    try:
        for index, event in enumerate(_events(path)):
            if index >= 64:
                break
            if event and event.get("type") == "session_meta":
                payload = event["payload"]
                if not isinstance(payload.get("id"), str) or not SESSION_ID.fullmatch(payload["id"]):
                    return None
                source = payload.get("source")
                subagent = source.get("subagent") if isinstance(source, dict) else None
                spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                spawn = spawn if isinstance(spawn, dict) else {}
                parent = payload.get("parent_thread_id") or spawn.get("parent_thread_id")
                parent = parent if isinstance(parent, str) else None
                hint = payload.get("session_id")
                role = payload.get("agent_role") or spawn.get("agent_role")
                return {"id": payload["id"], "parent": parent,
                        "hint": hint if isinstance(hint, str) else None,
                        "role": role if isinstance(role, str) else ("subagent/unknown" if parent else "main"),
                        "path": path}
    except (OSError, UnicodeError):
        pass
    return None


def _latest_rank(trace):
    latest = datetime.min.replace(tzinfo=timezone.utc)
    count = 0
    complete = False
    try:
        for event in _events(trace["path"]):
            if not event:
                continue
            count += 1
            stamp = _timestamp(event.get("timestamp") or event["payload"].get("timestamp"))
            if stamp:
                latest = max(latest, stamp)
            if event.get("type") == "event_msg":
                kind = event["payload"].get("type")
                if kind == "task_started":
                    complete = False
                elif kind == "task_complete":
                    complete = True
        return latest, complete, count
    except (OSError, UnicodeError):
        return latest, False, -1


def _discover(roots, session_id):
    by_id = defaultdict(list)
    notes = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.jsonl"):
                if path.is_symlink():
                    continue
                meta = _metadata(path)
                if meta:
                    by_id[meta["id"]].append(meta)
        except OSError:
            notes.add("discovery_incomplete")
    selected = {sid for sid, items in by_id.items()
                if sid == session_id or any(item["hint"] == session_id for item in items)}
    while True:
        descendants = {sid for sid, items in by_id.items()
                       if any(item["parent"] in selected or item["parent"] == session_id for item in items)}
        expanded = selected | descendants
        if expanded == selected:
            break
        selected = expanded
    if session_id not in by_id:
        notes.add("root_missing")
    traces = []
    duplicates = 0
    for sid in sorted(selected):
        candidates = by_id[sid]
        duplicates += len(candidates) - 1
        traces.append(max(candidates, key=_latest_rank) if len(candidates) > 1 else candidates[0])
    return traces, duplicates, notes


def _blank():
    return dict.fromkeys((*FIELDS, "tokenEvents"), 0)


def _totals(usage):
    return {**usage, "uncachedInputTokens": usage["inputTokens"] - usage["cachedInputTokens"],
            "totalTokens": usage["inputTokens"] + usage["outputTokens"]}


def _add(target, source):
    for key in target:
        target[key] += source[key]


def _token_usage(value):
    if not isinstance(value, dict):
        return None
    if not all(key in value for key in ("input_tokens", "output_tokens")):
        return None
    values = [value.get(key, 0) for key in LOG_FIELDS]
    if any(type(number) is not int or number < 0 for number in values):
        return None
    if values[1] > values[0] or values[3] > values[2]:
        return None
    return dict(zip(FIELDS, values), tokenEvents=1)


def _tool_seconds(start, end, spans):
    # Parallel tools overlap. Subtract the union, never the sum of durations.
    clipped = sorted((max(start, a), min(end, b)) for a, b in spans if b > start and a < end)
    total = 0.0
    cursor = start
    for a, b in clipped:
        a = max(a, cursor)
        if b > a:
            total += (b - a).total_seconds()
            cursor = b
    return total


def _speed(samples):
    if not samples:
        return None
    ordered = sorted(samples)
    def percentile(fraction):
        index = fraction * (len(ordered) - 1)
        low = int(index)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (index - low)
    return {"samples": len(ordered), "medianTokensPerSecond": percentile(.5),
            "meanTokensPerSecond": sum(ordered) / len(ordered),
            "p10TokensPerSecond": percentile(.1), "p90TokensPerSecond": percentile(.9)}


def _scan(trace):
    models = defaultdict(_blank)
    samples = defaultdict(list)
    model, effort = "unknown", None
    previous_snapshot = None
    last_response = None
    pending_tools, spans = {}, []
    malformed = duplicates = invalid_usage = 0
    readable = True
    latest = None
    try:
        for event in _events(trace["path"]):
            if event is None:
                malformed += 1
                continue
            payload = event["payload"]
            stamp = _timestamp(event.get("timestamp") or payload.get("timestamp"))
            if stamp:
                latest = max(latest, stamp) if latest else stamp
            kind = event.get("type")
            if kind == "turn_context":
                next_model = payload.get("model")
                next_model = next_model if isinstance(next_model, str) and next_model else model
                next_effort = payload.get("effort")
                if (model, effort) != (next_model, next_effort):
                    previous_snapshot = last_response = None
                model, effort = next_model, next_effort
            elif kind == "event_msg" and payload.get("type") in ("task_started", "turn_aborted", "task_complete"):
                last_response = None
                pending_tools, spans = {}, []
            elif kind == "response_item" and stamp:
                call_id = payload.get("call_id")
                if not isinstance(call_id, str):
                    continue
                if payload.get("type") in ("function_call", "custom_tool_call"):
                    pending_tools[call_id] = stamp
                elif payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                    called_at = pending_tools.pop(call_id, None)
                    if called_at and stamp >= called_at:
                        spans.append((called_at, stamp))
            if kind != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if info is None:
                # Some token_count events only update rate limits.
                continue
            info = info if isinstance(info, dict) else {}
            raw_usage = info.get("last_token_usage")
            usage = _token_usage(raw_usage)
            if usage is None:
                invalid_usage += 1
                last_response = None
                continue
            cumulative = _token_usage(info.get("total_token_usage"))
            snapshot = (json.dumps(cumulative, sort_keys=True), json.dumps(usage, sort_keys=True)) if cumulative else None
            if snapshot and snapshot == previous_snapshot:
                duplicates += 1
                continue
            previous_snapshot = snapshot
            _add(models[model], usage)
            if stamp and last_response is not None:
                window = (stamp - last_response).total_seconds()
                if .2 < window <= 120 and usage["outputTokens"] > 0:
                    active = spans + [(a, stamp) for a in pending_tools.values() if a < stamp]
                    generating = window - _tool_seconds(last_response, stamp, active)
                    rate = usage["outputTokens"] / generating if generating > 0 else None
                    if rate is not None and math.isfinite(rate):
                        samples[model].append(rate)
            if stamp:
                last_response = stamp
                spans = [(a, b) for a, b in spans if b > stamp]
    except (OSError, UnicodeError):
        readable = False
    rows = [{"sessionId": trace["id"], "parentSessionId": trace["parent"],
             "agentRole": trace["role"], "model": name, **_totals(usage),
             "speed": _speed(samples[name])} for name, usage in sorted(models.items())]
    return rows, models, samples, malformed, duplicates, invalid_usage, readable, latest


def collect_usage(session_id, sessions_root=None, archived_root=None):
    """Collect once; the store caches this report. Roots are local/test-only args."""
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id)):
        raise ValueError("sessionId has an invalid format")
    captured_at = datetime.now(timezone.utc).isoformat()
    totals = _blank()
    report = {"version": 1, "status": "unavailable", "capturedAt": captured_at,
              "sessionId": session_id, "scope": "session_tree", "source": "local_codex_logs", "lastEventAt": None,
              "totals": None, "speed": None, "models": [], "sessions": [],
              "coverage": {"sessionsFound": 0, "sessionsWithUsage": 0, "malformedLines": 0,
                           "duplicateFiles": 0, "skippedDuplicateEvents": 0}, "notes": []}
    if session_id is None:
        report["notes"] = ["missing_session_id"]
        return report
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    roots = [Path(sessions_root) if sessions_root is not None else home / "sessions"]
    if archived_root is not None:
        roots.append(Path(archived_root))
    elif sessions_root is None:
        roots.append(home / "archived_sessions")
    traces, duplicates, notes = _discover(roots, session_id)
    coverage = report["coverage"]
    coverage.update(sessionsFound=len(traces), duplicateFiles=duplicates)
    models = defaultdict(_blank)
    speeds = defaultdict(list)
    all_samples = []
    latest = None
    for trace in traces:
        rows, values, samples, malformed, skipped, invalid, readable, at = _scan(trace)
        report["sessions"].extend(rows)
        coverage["malformedLines"] += malformed
        coverage["skippedDuplicateEvents"] += skipped
        if values:
            coverage["sessionsWithUsage"] += 1
        else:
            notes.add("sessions_without_usage")
        if not readable:
            notes.add("unreadable_trace")
        if malformed or invalid:
            notes.add("incomplete_events")
        if at:
            latest = max(latest, at) if latest else at
        for name, value in values.items():
            _add(models[name], value)
            _add(totals, value)
            speeds[name].extend(samples[name])
            all_samples.extend(samples[name])
    report["models"] = [{"model": name, **_totals(value), "speed": _speed(speeds[name])}
                        for name, value in sorted(models.items())]
    if coverage["sessionsWithUsage"]:
        report.update(status="partial" if notes else "available", totals=_totals(totals), speed=_speed(all_samples))
    else:
        notes.add("no_token_events")
    report["capturedAt"] = datetime.now(timezone.utc).isoformat()
    report["lastEventAt"] = latest.isoformat() if latest else None
    report["notes"] = sorted(notes)
    return report
