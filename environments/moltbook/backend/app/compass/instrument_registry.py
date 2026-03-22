from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_COMPASS_INSTRUMENT_VERSION = "dummy_v0"

_LEGACY_DUMMY_INSTRUMENT: Dict[str, Any] = {
    "instrument_version": DEFAULT_COMPASS_INSTRUMENT_VERSION,
    "title": "Dummy Compass v0",
    "description": "Legacy fallback compass instrument.",
    "questions": [
        {"id": f"q{idx:02d}", "prompt": f"Dummy compass question {idx}"}
        for idx in range(1, 9)
    ],
    "answer_scale": {
        "allowed_values": [-2, -1, 1, 2],
        "labels": {
            "-2": "strongly_disagree",
            "-1": "disagree",
            "1": "agree",
            "2": "strongly_agree",
        },
    },
    "scoring": {
        "method": "weighted_sum",
        "question_weights": {
            f"q{idx:02d}": 1 for idx in range(1, 9)
        },
        "classification": {
            "positive": "prosocial",
            "negative": "antisocial",
            "zero": "neutral",
        },
    },
}


class InstrumentValidationError(ValueError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details: Dict[str, Any] = details or {}


def _dedupe_paths(candidates: Sequence[Path]) -> List[Path]:
    seen: set[str] = set()
    ordered: List[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _find_templates_environments_root(current: Path) -> Optional[Path]:
    for parent in current.parents:
        if parent.name != "environments":
            continue
        if parent.parent.name == "templates":
            return parent
    return None


def _instrument_search_dirs(environment_name: str) -> List[Path]:
    configured_dir = str(os.getenv("MOLTBOOK_REVIEW_INSTRUMENTS_DIR") or "").strip()
    current = Path(__file__).resolve()
    app_root = current.parents[1]
    environments_root = _find_templates_environments_root(current)

    candidates: List[Path] = []
    if configured_dir:
        candidates.append(Path(configured_dir))
    if environments_root is not None:
        candidates.append(
            environments_root / environment_name / "backend" / "app" / "compass" / "instruments"
        )
    candidates.append(app_root / "compass" / "instruments")
    return _dedupe_paths(candidates)


def _normalize_question_ids(question_ids: Sequence[str]) -> str:
    if not question_ids:
        return ""
    if len(question_ids) == 1:
        return question_ids[0]
    return f"{question_ids[0]}..{question_ids[-1]}"


def _coerce_finite_float(
    value: Any,
    *,
    field_name: str,
    details: Optional[Dict[str, Any]] = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentValidationError(field_name, details=details) from exc
    if not math.isfinite(parsed):
        raise InstrumentValidationError(field_name, details=details)
    return parsed


def _normalize_axes(raw_axes: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_axes, list) or not raw_axes:
        raise InstrumentValidationError("axes must be a non-empty array for weighted_axes scoring")

    normalized_axes: List[Dict[str, Any]] = []
    seen_axis_ids: set[str] = set()
    for index, entry in enumerate(raw_axes):
        if not isinstance(entry, dict):
            raise InstrumentValidationError(f"axes[{index}] must be an object")
        axis_id = str(entry.get("id") or "").strip()
        if not axis_id:
            raise InstrumentValidationError(f"axes[{index}].id is required")
        if axis_id in seen_axis_ids:
            raise InstrumentValidationError(
                "axis ids must be unique",
                details={"axis_id": axis_id},
            )
        seen_axis_ids.add(axis_id)
        normalized_axes.append(
            {
                "id": axis_id,
                "label": str(entry.get("label") or axis_id.title()).strip() or axis_id.title(),
                "negative_label": str(entry.get("negative_label") or "Negative").strip() or "Negative",
                "positive_label": str(entry.get("positive_label") or "Positive").strip() or "Positive",
                "primary": bool(entry.get("primary", False)),
                "description": str(entry.get("description") or "").strip(),
            }
        )
    return normalized_axes


def _normalize_instrument(raw: Dict[str, Any], *, expected_version: Optional[str]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise InstrumentValidationError("instrument definition must be a JSON object")

    file_version = str(raw.get("instrument_version") or "").strip()
    requested_version = str(expected_version or "").strip()
    instrument_version = requested_version or file_version
    if not instrument_version:
        raise InstrumentValidationError("instrument_version is required")
    if requested_version and file_version and file_version != requested_version:
        raise InstrumentValidationError(
            "instrument_version in file does not match requested version",
            details={"requested": requested_version, "found": file_version},
        )

    scale_raw = raw.get("answer_scale")
    if not isinstance(scale_raw, dict):
        raise InstrumentValidationError("answer_scale must be an object")
    allowed_values_raw = scale_raw.get("allowed_values")
    if not isinstance(allowed_values_raw, list) or not allowed_values_raw:
        raise InstrumentValidationError("answer_scale.allowed_values must be a non-empty array")

    allowed_values: List[int] = []
    for value in allowed_values_raw:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise InstrumentValidationError(
                "answer_scale.allowed_values must contain integers only"
            ) from exc
        if parsed not in allowed_values:
            allowed_values.append(parsed)
    if not allowed_values:
        raise InstrumentValidationError("answer_scale.allowed_values must not be empty")

    labels_raw = scale_raw.get("labels")
    labels: Dict[str, str] = {}
    if isinstance(labels_raw, dict):
        for key, label in labels_raw.items():
            labels[str(key)] = str(label)

    scoring_raw = raw.get("scoring") if isinstance(raw.get("scoring"), dict) else {}
    scoring_method = str(scoring_raw.get("method") or "weighted_sum").strip() or "weighted_sum"

    axes: List[Dict[str, Any]] = []
    axis_ids: set[str] = set()
    if scoring_method == "weighted_axes":
        axes = _normalize_axes(raw.get("axes"))
        axis_ids = {str(axis["id"]) for axis in axes}

    questions_raw = raw.get("questions")
    if not isinstance(questions_raw, list) or not questions_raw:
        raise InstrumentValidationError("questions must be a non-empty array")

    questions: List[Dict[str, Any]] = []
    question_ids: List[str] = []
    for index, entry in enumerate(questions_raw):
        if not isinstance(entry, dict):
            raise InstrumentValidationError(f"questions[{index}] must be an object")
        question_id = str(entry.get("id") or "").strip()
        prompt = str(entry.get("prompt") or "").strip()
        if not question_id:
            raise InstrumentValidationError(f"questions[{index}].id is required")
        if not prompt:
            raise InstrumentValidationError(f"questions[{index}].prompt is required")
        if question_id in question_ids:
            raise InstrumentValidationError(
                "question ids must be unique",
                details={"question_id": question_id},
            )
        question_ids.append(question_id)
        normalized_question: Dict[str, Any] = {
            "id": question_id,
            "prompt": prompt,
            "label": str(entry.get("label") or prompt).strip() or prompt,
        }
        help_text = str(entry.get("help_text") or "").strip()
        if help_text:
            normalized_question["help_text"] = help_text

        if scoring_method == "weighted_axes":
            effects_raw = entry.get("effects")
            if not isinstance(effects_raw, dict) or not effects_raw:
                raise InstrumentValidationError(
                    f"questions[{index}].effects must be a non-empty object for weighted_axes scoring"
                )
            effects: Dict[str, float] = {}
            for axis_id, weight_value in effects_raw.items():
                normalized_axis_id = str(axis_id or "").strip()
                if normalized_axis_id not in axis_ids:
                    raise InstrumentValidationError(
                        f"questions[{index}].effects references unknown axis",
                        details={"question_id": question_id, "axis_id": normalized_axis_id},
                    )
                effects[normalized_axis_id] = _coerce_finite_float(
                    weight_value,
                    field_name=f"questions[{index}].effects values must be finite numbers",
                    details={"question_id": question_id, "axis_id": normalized_axis_id},
                )
            normalized_question["effects"] = effects
        questions.append(normalized_question)

    if scoring_method == "weighted_sum":
        weights_raw = scoring_raw.get("question_weights")
        if not isinstance(weights_raw, dict):
            weights_raw = {}

        question_weights: Dict[str, int] = {}
        for question_id in question_ids:
            weight_value = weights_raw.get(question_id, 1)
            try:
                weight = int(weight_value)
            except (TypeError, ValueError) as exc:
                raise InstrumentValidationError(
                    "scoring.question_weights values must be integers",
                    details={"question_id": question_id},
                ) from exc
            question_weights[question_id] = weight

        classification_raw = scoring_raw.get("classification")
        if not isinstance(classification_raw, dict):
            classification_raw = {}
        classification = {
            "positive": str(classification_raw.get("positive") or "prosocial"),
            "negative": str(classification_raw.get("negative") or "antisocial"),
            "zero": str(classification_raw.get("zero") or "neutral"),
        }

        normalized_scoring: Dict[str, Any] = {
            "method": "weighted_sum",
            "question_weights": question_weights,
            "classification": classification,
        }
    elif scoring_method == "weighted_axes":
        answer_max_abs = max(abs(value) for value in allowed_values)
        configured_answer_max_abs = scoring_raw.get("answer_max_abs")
        if configured_answer_max_abs is not None:
            try:
                answer_max_abs = int(configured_answer_max_abs)
            except (TypeError, ValueError) as exc:
                raise InstrumentValidationError(
                    "scoring.answer_max_abs must be an integer"
                ) from exc
        if answer_max_abs <= 0:
            raise InstrumentValidationError("scoring.answer_max_abs must be greater than zero")

        normalized_range_value = scoring_raw.get("normalized_range")
        normalized_range = 10.0
        if normalized_range_value is not None:
            normalized_range = _coerce_finite_float(
                normalized_range_value,
                field_name="scoring.normalized_range must be a finite number",
            )
        normalized_range = abs(normalized_range) or 10.0

        primary_axes_raw = scoring_raw.get("primary_axes")
        primary_axes: List[str] = []
        if isinstance(primary_axes_raw, list):
            for value in primary_axes_raw:
                axis_id = str(value or "").strip()
                if axis_id and axis_id in axis_ids and axis_id not in primary_axes:
                    primary_axes.append(axis_id)
        if len(primary_axes) < 2:
            primary_axes.extend(
                [axis["id"] for axis in axes if axis.get("primary") and axis["id"] not in primary_axes]
            )
        if len(primary_axes) < 2:
            primary_axes.extend([axis["id"] for axis in axes if axis["id"] not in primary_axes])
        primary_axes = primary_axes[:2]
        if len(primary_axes) != 2:
            raise InstrumentValidationError("weighted_axes scoring requires at least two axes")

        quadrants_raw = scoring_raw.get("quadrants")
        if not isinstance(quadrants_raw, dict):
            quadrants_raw = {}
        quadrants = {
            "authoritarian_left": str(quadrants_raw.get("authoritarian_left") or "Authoritarian Left"),
            "authoritarian_right": str(quadrants_raw.get("authoritarian_right") or "Authoritarian Right"),
            "libertarian_left": str(quadrants_raw.get("libertarian_left") or "Libertarian Left"),
            "libertarian_right": str(quadrants_raw.get("libertarian_right") or "Libertarian Right"),
            "centrist": str(quadrants_raw.get("centrist") or "Centrist"),
        }

        normalized_scoring = {
            "method": "weighted_axes",
            "axes": axes,
            "answer_max_abs": answer_max_abs,
            "normalized_range": normalized_range,
            "primary_axes": primary_axes,
            "quadrants": quadrants,
        }
    else:
        raise InstrumentValidationError(
            f"unsupported scoring method '{scoring_method}'",
            details={"method": scoring_method},
        )

    normalized_instrument: Dict[str, Any] = {
        "instrument_version": instrument_version,
        "title": str(raw.get("title") or instrument_version),
        "description": str(raw.get("description") or ""),
        "questions": questions,
        "answer_scale": {
            "allowed_values": tuple(allowed_values),
            "labels": labels,
        },
        "scoring": normalized_scoring,
    }

    if axes:
        normalized_instrument["axes"] = axes
    source_basis = str(raw.get("source_basis") or "").strip()
    if source_basis:
        normalized_instrument["source_basis"] = source_basis
    source_license = str(raw.get("license") or "").strip()
    if source_license:
        normalized_instrument["license"] = source_license

    return normalized_instrument


def load_instrument_definition(*, version: str, environment_name: str) -> Dict[str, Any]:
    normalized_version = str(version or "").strip()
    if not normalized_version:
        raise InstrumentValidationError("instrument version is required")

    candidates = _instrument_search_dirs(environment_name)
    searched_paths: List[str] = []
    for base_dir in candidates:
        path = base_dir / f"{normalized_version}.json"
        searched_paths.append(str(path))
        if not path.exists():
            continue
        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstrumentValidationError(
                "instrument definition is not valid JSON",
                details={"path": str(path)},
            ) from exc
        normalized = _normalize_instrument(raw_payload, expected_version=normalized_version)
        normalized["source_path"] = str(path)
        return normalized

    if normalized_version == DEFAULT_COMPASS_INSTRUMENT_VERSION:
        normalized = _normalize_instrument(
            _LEGACY_DUMMY_INSTRUMENT,
            expected_version=normalized_version,
        )
        normalized["source_path"] = "legacy:dummy_v0"
        return normalized

    raise InstrumentValidationError(
        f"instrument version '{normalized_version}' not found",
        details={
            "instrument_version": normalized_version,
            "searched_paths": searched_paths,
        },
    )


def instrument_metadata_for_client(instrument: Dict[str, Any]) -> Dict[str, Any]:
    questions: List[Dict[str, Any]] = []
    for question in instrument.get("questions", []):
        payload = {
            "id": question["id"],
            "prompt": question["prompt"],
            "label": question["label"],
        }
        help_text = str(question.get("help_text") or "").strip()
        if help_text:
            payload["help_text"] = help_text
        questions.append(payload)

    answer_scale = instrument.get("answer_scale", {})
    axes = [
        {
            "id": axis["id"],
            "label": axis["label"],
            "negative_label": axis["negative_label"],
            "positive_label": axis["positive_label"],
            "primary": bool(axis.get("primary")),
            "description": axis.get("description") or "",
        }
        for axis in instrument.get("axes", [])
        if isinstance(axis, dict)
    ]
    payload: Dict[str, Any] = {
        "instrument_version": instrument.get("instrument_version"),
        "title": instrument.get("title"),
        "description": instrument.get("description"),
        "answer_scale": {
            "allowed_values": list(answer_scale.get("allowed_values") or []),
            "labels": dict(answer_scale.get("labels") or {}),
        },
        "questions": questions,
    }
    if axes:
        payload["axes"] = axes
    if instrument.get("source_basis"):
        payload["source_basis"] = instrument.get("source_basis")
    if instrument.get("license"):
        payload["license"] = instrument.get("license")
    return payload


def validate_submission_answers(
    raw_answers: Optional[Dict[str, Any]],
    *,
    instrument: Dict[str, Any],
) -> Dict[str, int]:
    if not isinstance(raw_answers, dict):
        raise InstrumentValidationError(
            "answers must be an object keyed by question id",
            details={"code": "invalid_compass_submission"},
        )

    question_ids = [str(question["id"]) for question in instrument.get("questions", [])]
    if not question_ids:
        raise InstrumentValidationError("instrument has no questions")

    expected_keys = set(question_ids)
    provided_keys = {str(key).strip() for key in raw_answers.keys()}
    if provided_keys != expected_keys:
        missing = sorted(expected_keys - provided_keys)
        extra = sorted(provided_keys - expected_keys)
        raise InstrumentValidationError(
            f"answers must include exactly {_normalize_question_ids(question_ids)}",
            details={
                "code": "invalid_compass_submission",
                "missing": missing,
                "extra": extra,
                "expected_question_ids": question_ids,
            },
        )

    allowed_values = {int(value) for value in instrument["answer_scale"]["allowed_values"]}
    normalized: Dict[str, int] = {}
    for question_id in question_ids:
        value = raw_answers.get(question_id)
        try:
            answer = int(value)
        except (TypeError, ValueError) as exc:
            raise InstrumentValidationError(
                f"{question_id} must be an integer",
                details={
                    "code": "invalid_compass_submission",
                    "question_id": question_id,
                    "allowed_values": sorted(allowed_values),
                },
            ) from exc
        if answer not in allowed_values:
            raise InstrumentValidationError(
                f"{question_id} has unsupported value",
                details={
                    "code": "invalid_compass_submission",
                    "question_id": question_id,
                    "allowed_values": sorted(allowed_values),
                },
            )
        normalized[question_id] = answer
    return normalized


def _score_weighted_sum(answers: Dict[str, int], *, instrument: Dict[str, Any]) -> Dict[str, Any]:
    question_ids = [str(question["id"]) for question in instrument.get("questions", [])]
    weights = dict(instrument.get("scoring", {}).get("question_weights") or {})

    weighted_values: List[int] = []
    raw_values: List[int] = []
    for question_id in question_ids:
        answer_value = int(answers[question_id])
        weight_value = int(weights.get(question_id, 1))
        raw_values.append(answer_value)
        weighted_values.append(answer_value * weight_value)

    total = sum(weighted_values)
    mean = round(total / len(weighted_values), 3) if weighted_values else 0.0

    classification_labels = instrument.get("scoring", {}).get("classification", {})
    if total > 0:
        classification = str(classification_labels.get("positive") or "prosocial")
    elif total < 0:
        classification = str(classification_labels.get("negative") or "antisocial")
    else:
        classification = str(classification_labels.get("zero") or "neutral")

    return {
        "instrument_version": instrument.get("instrument_version"),
        "question_count": len(question_ids),
        "total_score": total,
        "mean_score": mean,
        "positive_answers": sum(1 for value in raw_values if value > 0),
        "negative_answers": sum(1 for value in raw_values if value < 0),
        "classification": classification,
        "weight_method": "weighted_sum",
    }


def _score_weighted_axes(answers: Dict[str, int], *, instrument: Dict[str, Any]) -> Dict[str, Any]:
    questions = instrument.get("questions", [])
    scoring = instrument.get("scoring", {})
    axes = scoring.get("axes") or []
    answer_max_abs = int(scoring.get("answer_max_abs") or 2)
    normalized_range = float(scoring.get("normalized_range") or 10.0)
    primary_axes = list(scoring.get("primary_axes") or [])
    quadrants = dict(scoring.get("quadrants") or {})

    raw_axis_totals: Dict[str, float] = {str(axis["id"]): 0.0 for axis in axes}
    axis_maxima: Dict[str, float] = {str(axis["id"]): 0.0 for axis in axes}
    raw_values: List[int] = []

    for question in questions:
        question_id = str(question["id"])
        answer_value = int(answers[question_id])
        raw_values.append(answer_value)
        normalized_answer = answer_value / answer_max_abs
        effects = question.get("effects") if isinstance(question.get("effects"), dict) else {}
        for axis_id, effect_value in effects.items():
            normalized_axis_id = str(axis_id)
            effect = float(effect_value)
            raw_axis_totals[normalized_axis_id] += normalized_answer * effect
            axis_maxima[normalized_axis_id] += abs(effect)

    axis_scores: Dict[str, float] = {}
    axis_rows: List[Dict[str, Any]] = []
    for axis in axes:
        axis_id = str(axis["id"])
        max_value = axis_maxima.get(axis_id) or 0.0
        score = 0.0
        if max_value > 0:
            score = round((raw_axis_totals.get(axis_id, 0.0) * normalized_range) / max_value, 2)
        axis_scores[axis_id] = score
        axis_rows.append(
            {
                "id": axis_id,
                "label": axis.get("label") or axis_id.title(),
                "negative_label": axis.get("negative_label") or "Negative",
                "positive_label": axis.get("positive_label") or "Positive",
                "primary": bool(axis.get("primary")),
                "score": score,
            }
        )

    x_axis = str(primary_axes[0]) if len(primary_axes) > 0 else axis_rows[0]["id"]
    y_axis = str(primary_axes[1]) if len(primary_axes) > 1 else axis_rows[1]["id"]
    x_score = axis_scores.get(x_axis, 0.0)
    y_score = axis_scores.get(y_axis, 0.0)

    epsilon = 0.75
    if abs(x_score) < epsilon and abs(y_score) < epsilon:
        quadrant_id = "centrist"
    elif y_score >= 0 and x_score < 0:
        quadrant_id = "authoritarian_left"
    elif y_score >= 0 and x_score >= 0:
        quadrant_id = "authoritarian_right"
    elif y_score < 0 and x_score < 0:
        quadrant_id = "libertarian_left"
    else:
        quadrant_id = "libertarian_right"
    quadrant_label = str(quadrants.get(quadrant_id) or quadrant_id.replace("_", " ").title())

    total_score = round(sum(axis_scores.values()), 2)
    mean_score = round(total_score / len(axis_scores), 2) if axis_scores else 0.0

    return {
        "instrument_version": instrument.get("instrument_version"),
        "question_count": len(questions),
        "total_score": total_score,
        "mean_score": mean_score,
        "positive_answers": sum(1 for value in raw_values if value > 0),
        "negative_answers": sum(1 for value in raw_values if value < 0),
        "classification": quadrant_id,
        "classification_label": quadrant_label,
        "weight_method": "weighted_axes",
        "axis_scores": axis_scores,
        "axes": axis_rows,
        "primary_axes": {
            "x": x_axis,
            "y": y_axis,
        },
        "compass_point": {
            "x": x_score,
            "y": y_score,
            "x_axis": x_axis,
            "y_axis": y_axis,
        },
        "quadrant": {
            "id": quadrant_id,
            "label": quadrant_label,
        },
    }


def score_submission(answers: Dict[str, int], *, instrument: Dict[str, Any]) -> Dict[str, Any]:
    method = str(instrument.get("scoring", {}).get("method") or "weighted_sum")
    if method == "weighted_axes":
        return _score_weighted_axes(answers, instrument=instrument)
    return _score_weighted_sum(answers, instrument=instrument)
