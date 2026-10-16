"""Learning from what the operator stars and removes.

The brief says what the operator wants in their own words; their stars and
removals say it again, and more honestly. Every `every` new ones (50 by
default), the model reads them next to the scores it gave and writes a few
guidelines that sharpen the brief where the two disagreed. A handful of the
postings it got most wrong are kept as worked examples. Both go into the
review prompt, between the brief and the fixed instructions.

The brief stays the operator's: nothing here edits it. A calibration has a
version of its own, recorded on each review, and a new one does not make the
earlier reviews stale — it changes every fifty clicks, and rescoring seven
thousand postings each time would cost more than it adds. The panel counts
the reviews made before the latest calibration, and rescores them on request.

Everything lives in data/ranking-calibration.json; each calibration replaced
is appended to data/ranking-calibration-history.jsonl.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import db, paths

logger = logging.getLogger(__name__)

#: New stars and removals between two calibrations, unless set otherwise.
DEFAULT_EVERY = 50

#: How many of each the model reads: the most recent ones. Stars are rare
#: (36 against 1,003 removals when this was written), so they get their own
#: share rather than being crowded out.
MAX_STARRED = 120
MAX_REMOVED = 240

#: Worked examples kept in the review prompt, of each kind.
EXAMPLES_PER_KIND = 6

#: Guidelines the model may write. A long list is a second brief, and the
#: cheap reviewer follows a short one better.
MAX_GUIDELINES = 12

_GUIDELINES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "guidelines": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["guidelines", "notes"],
    "additionalProperties": False,
}

CALIBRATION_INSTRUCTIONS = f"""
You calibrate the prompt of a job-ranking model. Above is the candidate's
brief: the model reads it with ONE job posting at a time and scores it from
0 to 100. Below are the postings the candidate starred (★: wants to apply)
and removed (✕: not interested), each with the score the model gave and the
reason it wrote, followed by the guidelines of the previous calibration.

Write guidelines that bring the scores in line with these choices.
- Look for patterns that recur in several postings (three or more) where the
  score and the choice disagree: starred postings scored low, removed postings
  scored high. Ignore one-offs: a posting can be removed because it was
  already applied to, has expired, or is a duplicate.
- Each guideline is one sentence, concrete and checkable from an advert (kind
  of role, tasks, employer, level, place, language), and says which way to
  move the score and roughly by how much, in terms of the brief's criteria.
- Refine the brief, never contradict it: its blockers, caps and eligibility
  rules stand. Do not repeat what the brief already says, unless the scores
  show the model ignoring it.
- Keep the previous guidelines the feedback still supports; fix or drop those
  it contradicts.
- At most {MAX_GUIDELINES} guidelines; fewer when the evidence is thin, none
  if the scores already agree. Write them in the language of the brief.
- notes: one or two sentences for the candidate on what changed and why.

Reply only with the JSON object requested.
"""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _history_path():
    return paths.RANKING_CALIBRATION_PATH.with_name("ranking-calibration-history.jsonl")


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(paths.RANKING_CALIBRATION_PATH.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    settings = state.get("settings") or {}
    return {
        "settings": {
            "auto": bool(settings.get("auto", True)),
            "every": max(5, int(settings.get("every") or DEFAULT_EVERY)),
        },
        "current": state.get("current"),
        # The newest feedback event the latest calibration (or reset) took in.
        "through_event": int(state.get("through_event") or 0),
    }


def _save_state(state: dict[str, Any]) -> None:
    path = paths.RANKING_CALIBRATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _archive(current: dict[str, Any] | None) -> None:
    if not current:
        return
    with _history_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(current, ensure_ascii=False) + "\n")


def save_settings(*, auto: bool | None = None, every: int | None = None) -> dict[str, Any]:
    with _lock:
        state = load_state()
        if auto is not None:
            state["settings"]["auto"] = bool(auto)
        if every is not None:
            state["settings"]["every"] = max(5, int(every))
        _save_state(state)
        return state["settings"]


def reset() -> None:
    """Forget the guidelines; the feedback so far no longer counts as new."""
    with _lock:
        state = load_state()
        _archive(state["current"])
        state["current"] = None
        state["through_event"] = _last_event_id()
        _save_state(state)


def version() -> int:
    """The calibration reviews are made with now; 0 when there is none."""
    current = load_state()["current"]
    return int(current["version"]) if current else 0


def _last_event_id() -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(id) AS id FROM feedback_events").fetchone()
    return int(row["id"] or 0)


def pending(through_event: int | None = None) -> int:
    """Stars and removals made since the latest calibration."""
    if through_event is None:
        through_event = load_state()["through_event"]
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM feedback_events WHERE value = 1 AND id > ?",
            (through_event,),
        ).fetchone()
    return int(row["n"] or 0)


# ---------------------------------------------------------------------------
# The prompt section
# ---------------------------------------------------------------------------
def _example_line(example: dict[str, Any]) -> str:
    mark = "★ starred" if example["action"] == "starred" else "✕ removed"
    place = f" ({example['location']})" if example.get("location") else ""
    posting = f"{example['company']} — {example['title']}{place}"
    return f"- {mark}, you gave {example['score']}: {posting}"


def prompt_section(current: dict[str, Any] | None = None) -> str:
    """What the review prompt carries of the latest calibration, or ""."""
    if current is None:
        current = load_state()["current"]
    if not current or not (current.get("guidelines") or current.get("examples")):
        return ""
    lines = [
        "## Calibration from my feedback",
        f"Learned from {current.get('feedback_total', 0)} postings I starred or removed. "
        "These refine the brief above; its blockers, caps and eligibility rules still apply.",
    ]
    lines += [f"- {guideline}" for guideline in current.get("guidelines") or []]
    if current.get("examples"):
        lines += ["", "Postings where your score and my choice disagreed:"]
        lines += [_example_line(example) for example in current["examples"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
def feedback() -> list[dict[str, Any]]:
    """The starred and removed postings the model reads, newest first."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT j.id, j.title, j.company, j.location, j.saved, j.dismissed,
                   j.priority_llm_score, j.priority_score, j.priority_llm, j.priority_excluded,
                   (SELECT MAX(e.id) FROM feedback_events e WHERE e.job_id = j.id) AS last_event
            FROM jobs j
            WHERE j.saved = 1 OR j.dismissed = 1
            ORDER BY last_event IS NULL, last_event DESC, j.updated_at DESC
            """
        ).fetchall()
    starred: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        review = json.loads(row["priority_llm"]) if row["priority_llm"] else {}
        item = {
            # A posting both starred and removed is starred: removing one is
            # the more casual act (it also clears the grid of what is done).
            "action": "starred" if row["saved"] else "removed",
            "score": row["priority_llm_score"] if review else row["priority_score"],
            "reviewed": bool(review),
            "company": row["company"],
            "title": row["title"],
            "location": row["location"] or "",
            "excluded": row["priority_excluded"] or "",
            "reason": str(review.get("justification") or review.get("motivo") or "")[:220],
        }
        bucket, limit = (starred, MAX_STARRED) if row["saved"] else (removed, MAX_REMOVED)
        if len(bucket) < limit:
            bucket.append(item)
    return starred + removed


def _feedback_text(items: list[dict[str, Any]]) -> str:
    lines = []
    for number, item in enumerate(items, start=1):
        mark = "★" if item["action"] == "starred" else "✕"
        score = item["score"] if item["score"] is not None else "?"
        source = "" if item["reviewed"] else " (keyword rules, not reviewed)"
        excluded = f" · excluded by rules: {item['excluded']}" if item["excluded"] else ""
        lines.append(
            f"{number}. {mark} score {score}{source}{excluded} · {item['company']} — "
            f"{item['title']} · {item['location'] or 'no location'}"
        )
        if item["reason"]:
            lines.append(f"   model's reason: {item['reason']}")
    return "\n".join(lines)


def pick_examples(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reviewed postings the model got most wrong: stars it scored lowest,
    removals it scored highest. Rules scores are left out: they are on
    another scale, and the model never wrote them."""
    reviewed = [item for item in items if item["reviewed"] and item["score"] is not None]
    starred = sorted((i for i in reviewed if i["action"] == "starred"), key=lambda i: i["score"])
    removed = sorted((i for i in reviewed if i["action"] == "removed"), key=lambda i: -i["score"])
    # A removal the model already scored low taught it nothing.
    removed = [item for item in removed if item["score"] >= 50]
    keep = ("action", "score", "company", "title", "location")
    return [
        {key: item[key] for key in keep}
        for item in starred[:EXAMPLES_PER_KIND] + removed[:EXAMPLES_PER_KIND]
    ]


# ---------------------------------------------------------------------------
# Calibrating
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_run_lock = threading.Lock()
#: What the panel shows of the calibration in progress, or of the last one.
run_state: dict[str, Any] = {"running": False, "error": "", "finished_at": "", "failed_at": 0.0}

#: Seconds before a failed automatic calibration is tried again.
RETRY_AFTER = 600


class NothingToLearn(RuntimeError):
    """No starred or removed posting to learn from."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def calibrate(*, client: Any = None, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Learn from the feedback so far, and make it the current calibration."""
    from . import config as config_module
    from . import ranking
    from .llm import OpenRouterClient, parse_response

    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("a calibration is already running")
    run_state.update(running=True, error="")
    try:
        brief = ranking.load_brief()
        through = _last_event_id()
        items = feedback()
        if not items:
            raise NothingToLearn("star or remove some postings first")
        state = load_state()
        previous = state["current"] or {}
        settings = config_module.load()
        user = (
            f"## Feedback ({sum(i['action'] == 'starred' for i in items)} starred, "
            f"{sum(i['action'] == 'removed' for i in items)} removed, newest first)\n"
            f"{_feedback_text(items)}\n\n## Previous guidelines\n"
            + ("\n".join(f"- {g}" for g in previous.get("guidelines") or []) or "(none yet)")
        )
        model = settings.openrouter.model
        say(f"calibrating on {len(items)} starred and removed postings with {model}")
        owned = client is None
        if client is None:
            client = OpenRouterClient(settings)
        try:
            payload = client.complete_raw(
                [
                    {"role": "system", "content": f"{brief}\n{CALIBRATION_INSTRUCTIONS}"},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "calibration",
                        "strict": True,
                        "schema": _GUIDELINES_SCHEMA,
                    },
                },
            )
        finally:
            if owned:
                client.close()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        answer = parse_response(content or "")
        guidelines = [
            " ".join(str(g).split()) for g in answer.get("guidelines") or [] if str(g).strip()
        ][:MAX_GUIDELINES]
        usage = payload.get("usage") or {}
        current = {
            "version": int(previous.get("version") or 0) + 1,
            "created_at": _now(),
            "model": settings.openrouter.model,
            "cost": float(usage.get("cost") or 0.0),
            "feedback_total": len(items),
            "starred": sum(i["action"] == "starred" for i in items),
            "removed": sum(i["action"] == "removed" for i in items),
            "guidelines": guidelines,
            "notes": str(answer.get("notes") or "").strip(),
            "examples": pick_examples(items),
        }
        with _lock:
            state = load_state()
            _archive(state["current"])
            state["current"] = current
            state["through_event"] = through
            _save_state(state)
        say(
            f"calibration {current['version']}: {len(guidelines)} guidelines, "
            f"{len(current['examples'])} examples, ${current['cost']:.4f}"
        )
        return current
    except Exception as exc:
        run_state.update(error=str(exc), failed_at=time.monotonic())
        raise
    finally:
        run_state.update(running=False, finished_at=_now())
        _run_lock.release()


def start(say: Callable[[str], None] | None = None) -> bool:
    """Calibrate in the background; False when one is already running."""
    if run_state["running"]:
        return False

    def work() -> None:
        try:
            calibrate(say=say or (lambda line: logger.info("calibration: %s", line)))
        except Exception as exc:  # noqa: BLE001 — shown in the panel
            logger.warning("calibration failed: %s", exc)

    threading.Thread(target=work, name="calibration", daemon=True).start()
    return True


def maybe_start(say: Callable[[str], None] | None = None) -> bool:
    """Calibrate in the background if enough new feedback came in.

    Called after every star and removal. Quiet when there is no key or no
    brief: the feedback keeps counting, and is learned from once there is.
    """
    from . import config as config_module
    from . import ranking

    state = load_state()
    if not state["settings"]["auto"] or run_state["running"]:
        return False
    if run_state["error"] and time.monotonic() - run_state["failed_at"] < RETRY_AFTER:
        return False  # not a paid call per click while the gateway is down
    if pending(state["through_event"]) < state["settings"]["every"]:
        return False
    if not config_module.load().openrouter.api_key or not ranking.current_digest():
        return False
    return start(say)


def status() -> dict[str, Any]:
    state = load_state()
    return {
        "settings": state["settings"],
        "current": state["current"],
        "pending": pending(state["through_event"]),
        "running": run_state["running"],
        "error": run_state["error"],
        "finished_at": run_state["finished_at"],
        "section": prompt_section(state["current"]),
    }
