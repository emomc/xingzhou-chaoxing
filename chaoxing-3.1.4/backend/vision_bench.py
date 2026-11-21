"""Accuracy bench: let the image API answer image questions that already have an accepted answer."""

import re
import time

from api.vision_pipeline import answer_identity
from api.webai_client import WebAIClient, WebAIError, parse_answers, validated_completion


CHOICE_TYPES = {"single", "multiple"}


def label(value):
    match = re.match(r"\s*([A-Za-z])(?:\b|\.)", value or "")
    return match.group(1).upper() if match else (value or "").strip()


def expected_answers(row):
    try:
        return parse_answers(row["answer"], row["question"])
    except Exception:  # noqa: BLE001 - a damaged reference must not abort the bench
        return []


def grade(question, expected, answers):
    """Return True/False, or None when the two answers are not comparable."""
    if not answers or not expected:
        return None
    if question.get("type") in CHOICE_TYPES:
        return sorted(label(item) for item in answers) == sorted(label(item) for item in expected)
    return answer_identity(answers, question) == answer_identity(expected, question)


def run(store, config, limit=6, mode="direct"):
    """mode=direct: one vision request answers; mode=pipeline: recognise, solve, cross-check."""
    rows = store.image_answers_with_reference(limit)
    result = {
        "mode": mode, "available": len(rows), "items": [],
        "graded": 0, "correct": 0, "accuracy": None, "model": "",
        "protocol": (config or {}).get("webai_protocol", ""),
    }
    if not rows or not config:
        return result
    client = WebAIClient({**config, "webai_recorder": None, "webai_stream": "false"})
    result["model"] = config.get("webai_vision_model") or config.get("webai_model", "")
    try:
        for row in rows:
            question = store.hydrate_answer_images(row["question"])
            expected = expected_answers({**row})
            record = {
                "id": row["id"], "type": question.get("type", ""),
                "title": (question.get("title") or "").replace("\n", " ")[:60],
                "images": len(question.get("images") or []),
                "expected": ", ".join(label(item) for item in expected),
                "got": "", "ok": None, "error": "", "seconds": 0,
            }
            started = time.monotonic()
            try:
                if mode == "pipeline":
                    answers = validated_completion(client, question)
                else:
                    answers = client.complete_direct(question)
                record["got"] = ", ".join(label(item) for item in answers)
                record["ok"] = grade(question, expected, answers)
            except WebAIError as exc:
                record["error"] = str(exc)[:150]
            except Exception as exc:  # noqa: BLE001 - upstream failure is a data point
                record["error"] = f"{type(exc).__name__}: {str(exc)[:130]}"
            record["seconds"] = round(time.monotonic() - started, 2)
            if record["ok"] is not None:
                result["graded"] += 1
                result["correct"] += 1 if record["ok"] else 0
            result["items"].append(record)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - closing must not mask results
            pass
    if result["graded"]:
        result["accuracy"] = round(result["correct"] / result["graded"], 3)
    return result
