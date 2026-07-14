from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .address_domain import CanonicalAddress, canonicalize_address
from .confidence_engine import evaluate_confidence
from .normalize import sha256_parts, stable_json
from .structure import CURRENT_STRUCTURE

AGENT_TASK_TYPES = {
    "mapping_conflict",
    "missing_context",
    "import_issue",
    "directive_challenge",
}


@dataclass(frozen=True)
class MappingSpec:
    address_key: str
    res_name: str
    branch_name: str
    status: str
    confidence: float
    explanation: dict[str, Any]
    observations: list[dict[str, Any]]


@dataclass(frozen=True)
class ConditionalRuleSpec:
    rule_key: str
    ambiguity_key: str
    condition: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class KnowledgePlan:
    rows: list[dict[str, Any]]
    valid_rows: list[dict[str, Any]]
    groups: dict[str, list[dict[str, Any]]]
    mappings: list[MappingSpec]
    conditional_rules: list[ConditionalRuleSpec]
    tasks: list[dict[str, Any]]
    keep_keys: set[str]
    directive_keys: set[str]


def _address(row: dict[str, Any]) -> CanonicalAddress:
    return canonicalize_address(row)


def _canonical_key(row: dict[str, Any]) -> str:
    return str(row.get("canonical_address_key") or row.get("canonical_key") or _address(row).canonical_key)


def _ambiguity_key(row: dict[str, Any]) -> str:
    return str(row.get("ambiguity_key") or _address(row).ambiguity_key)


def _context_key(row: dict[str, Any]) -> str:
    return str(row.get("context_key") or _address(row).context_key)


def _address_payload(row: dict[str, Any], address_key: str | None = None) -> dict[str, Any]:
    payload = _address(row).payload()
    payload["address_key"] = address_key or _canonical_key(row)
    payload["district"] = payload["municipal_district"]
    payload["settlement"] = payload["territory"]
    return payload


def _selected_res(directive: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in (directive.get("selection") or {}).get("selected_res", [])
        if value
    }


def _decision_type(directive: dict[str, Any] | None) -> str:
    if not directive:
        return ""
    return str((directive.get("selection") or {}).get("decision_type", "confirmed"))


def _evidence_count(items: list[dict[str, Any]]) -> int:
    return sum(int(item.get("independent_evidence_count", 0) or 0) for item in items)


def _challenge(
    base: dict[str, Any],
    directive: dict[str, Any],
    directives: dict[str, dict[str, Any]],
    current_version: int,
    relevant: set[str],
) -> dict[str, Any] | None:
    base_key = str(base["task_key"])
    relevant.add(base_key)
    observed = {
        str(option.get("res", ""))
        for option in (base.get("payload") or {}).get("options", [])
        if option.get("res")
    }
    selected = _selected_res(directive)
    decision = _decision_type(directive)
    if decision in {"source_error", "insufficient_data", "skip"}:
        selected = observed
    if current_version <= int(directive.get("source_version", 0)) or observed.issubset(selected):
        return None

    signature = sha256_parts(
        [
            *sorted(observed | selected),
            stable_json((base.get("payload") or {}).get("options", [])),
        ]
    )
    challenge_key = sha256_parts(["directive_challenge", base_key, signature])
    previous = directives.get(challenge_key)
    if previous:
        relevant.add(challenge_key)
        previous_selected = _selected_res(previous)
        if current_version <= int(previous.get("source_version", 0)) or observed.issubset(
            previous_selected
        ):
            return None

    return {
        "task_key": challenge_key,
        "task_type": "directive_challenge",
        "subject_type": base["subject_type"],
        "subject_key": base["subject_key"],
        "title": "Новое независимое доказательство противоречит решению",
        "payload": {
            **(base.get("payload") or {}),
            "previous_selection": sorted(selected),
            "new_evidence": True,
            "allow_address_edit": True,
        },
        "priority": 115,
    }


def _resolve_task(
    base: dict[str, Any],
    directives: dict[str, dict[str, Any]],
    current_version: int,
    relevant: set[str],
) -> dict[str, Any] | None:
    directive = directives.get(str(base["task_key"]))
    if not directive:
        return base
    return _challenge(base, directive, directives, current_version, relevant)


def _condition(address: CanonicalAddress) -> dict[str, Any]:
    return {
        "municipal_district": address.municipal_district,
        "urban_district": address.urban_district,
        "intracity_district": address.intracity_district,
        "locality": address.locality,
        "locality_type": address.locality_type,
        "territory": address.territory,
        "territory_type": address.territory_type,
        "street": address.street,
        "house": address.house,
        "coordinate_cell": address.coordinate_cell,
    }


def _rules_from_directives(
    directives: dict[str, dict[str, Any]],
) -> list[ConditionalRuleSpec]:
    result: list[ConditionalRuleSpec] = []
    for directive in directives.values():
        selection = directive.get("selection") or {}
        if str(selection.get("decision_type", "")) not in {
            "both_by_district",
            "both_by_condition",
            "conditional",
        }:
            continue
        for item in selection.get("conditions", []):
            res_name = str(item.get("res", ""))
            if res_name not in CURRENT_STRUCTURE:
                continue
            condition = dict(item.get("condition") or {})
            ambiguity_key = str(item.get("ambiguity_key") or directive.get("subject_key", ""))
            rule_key = sha256_parts(
                ["operator_rule", ambiguity_key, stable_json(condition), res_name]
            )
            result.append(
                ConditionalRuleSpec(
                    rule_key=rule_key,
                    ambiguity_key=ambiguity_key,
                    condition=condition,
                    result={"res": res_name, "branch": CURRENT_STRUCTURE[res_name]},
                )
            )
    return result


def build_knowledge_plan(
    rows: list[dict[str, Any]],
    directives: dict[str, dict[str, Any]],
    current_version: int,
) -> KnowledgePlan:
    valid = [
        row
        for row in rows
        if (_address(row).locality or _address(row).territory)
        and str(row.get("res_name", "")) in CURRENT_STRUCTURE
        and int(row.get("accepted_evidence_count", 1) or 0) > 0
    ]
    valid_ids = {int(row["id"]) for row in valid}
    invalid = [row for row in rows if int(row["id"]) not in valid_ids]

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ambiguity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        groups[_canonical_key(row)].append(row)
        ambiguity_groups[_ambiguity_key(row)].append(row)

    mappings: list[MappingSpec] = []
    tasks: list[dict[str, Any]] = []
    keep_keys: set[str] = set()
    directive_keys: set[str] = set()
    conditional_rules: list[ConditionalRuleSpec] = _rules_from_directives(directives)

    def add_task(base: dict[str, Any]) -> None:
        candidate = _resolve_task(base, directives, current_version, directive_keys)
        if candidate:
            keep_keys.add(str(candidate["task_key"]))
            tasks.append(candidate)

    ambiguous_names: dict[str, dict[str, Any]] = {}
    for ambiguity_key, ambiguity_rows in ambiguity_groups.items():
        contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in ambiguity_rows:
            contexts[_context_key(row)].append(row)
        contextual = {
            key: values
            for key, values in contexts.items()
            if _address(values[0]).has_region_context or _address(values[0]).coordinate_cell
        }
        all_res = {
            str(row["res_name"])
            for values in contextual.values()
            for row in values
            if str(row.get("res_name", "")) in CURRENT_STRUCTURE
        }
        if len(contextual) > 1 and len(all_res) > 1:
            options: list[dict[str, Any]] = []
            for context_key, context_rows in contextual.items():
                address = _address(context_rows[0])
                for res_name in sorted({str(item["res_name"]) for item in context_rows}):
                    condition = _condition(address)
                    rule_key = sha256_parts(
                        ["source_rule", ambiguity_key, context_key, res_name]
                    )
                    conditional_rules.append(
                        ConditionalRuleSpec(
                            rule_key=rule_key,
                            ambiguity_key=ambiguity_key,
                            condition=condition,
                            result={"res": res_name, "branch": CURRENT_STRUCTURE[res_name]},
                        )
                    )
                    options.append(
                        {
                            **condition,
                            "res": res_name,
                            "branch": CURRENT_STRUCTURE[res_name],
                            "independent_evidence": _evidence_count(context_rows),
                        }
                    )
            ambiguous_names[ambiguity_key] = {"options": options, "res": all_res}

    for address_key, group in groups.items():
        by_res: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in group:
            by_res[str(row["res_name"])].append(row)
        conflict = len(by_res) > 1
        address = _address(group[0])
        ambiguity = ambiguous_names.get(_ambiguity_key(group[0]))
        lacks_context = bool(ambiguity) and not (
            address.has_region_context or address.coordinate_cell
        )
        conflict_task_key = sha256_parts(["mapping_conflict", address_key])
        conflict_directive = directives.get(conflict_task_key)
        decision = _decision_type(conflict_directive)

        if conflict and decision not in {
            "both_by_district",
            "both_by_condition",
            "conditional",
            "source_error",
            "insufficient_data",
            "skip",
        }:
            add_task(
                {
                    "task_key": conflict_task_key,
                    "task_type": "mapping_conflict",
                    "subject_type": "address",
                    "subject_key": address_key,
                    "title": "Один канонический адрес связан с разными РЭС",
                    "payload": {
                        "address": _address_payload(group[0], address_key),
                        "options": [
                            {
                                "branch": CURRENT_STRUCTURE[res],
                                "res": res,
                                "independent_evidence": _evidence_count(items),
                                "technical_duplicates": sum(
                                    int(item.get("technical_duplicate_count", 0) or 0)
                                    for item in items
                                ),
                            }
                            for res, items in sorted(by_res.items())
                        ],
                        "allow_multiple": True,
                        "allow_address_edit": True,
                        "decision_types": [
                            "confirmed",
                            "selected_other",
                            "both_by_district",
                            "both_by_condition",
                            "insufficient_data",
                            "source_error",
                            "skip",
                        ],
                    },
                    "priority": 105,
                }
            )

        if lacks_context:
            missing_key = sha256_parts(["missing_context", _ambiguity_key(group[0])])
            add_task(
                {
                    "task_key": missing_key,
                    "task_type": "missing_context",
                    "subject_type": "observation",
                    "subject_key": str(group[0]["id"]),
                    "title": "Недостаточно контекста для одноименного объекта",
                    "payload": {
                        "observation_id": group[0]["id"],
                        "address": _address_payload(group[0], address_key),
                        "current": {
                            "branch": group[0].get("branch_name", ""),
                            "res": group[0].get("res_name", ""),
                        },
                        "options": ambiguity["options"],
                        "allow_multiple": False,
                        "allow_address_edit": True,
                        "do_not_guess": True,
                    },
                    "priority": 100,
                }
            )

        for res_name, observations in by_res.items():
            mapping_directive = conflict_directive
            operator_decision = _decision_type(mapping_directive)
            confidence = evaluate_confidence(
                address,
                observations,
                conflict_count=1 if conflict else 0,
                operator_decision=operator_decision,
                geodata_match=bool(group[0].get("geodata_match", False)),
            )
            if conflict:
                status = "conflict"
            elif lacks_context:
                status = "ambiguous"
            elif confidence.score < 45:
                status = "source_only"
            else:
                status = "consistent"
            if operator_decision == "source_error":
                status = "rejected"
            mappings.append(
                MappingSpec(
                    address_key=address_key,
                    res_name=res_name,
                    branch_name=CURRENT_STRUCTURE[res_name],
                    status=status,
                    confidence=confidence.score,
                    explanation=confidence.payload(),
                    observations=observations,
                )
            )

    for row in invalid:
        if int(row.get("technical_duplicate_count", 0) or 0) > 0 and int(
            row.get("accepted_evidence_count", 0) or 0
        ) == 0:
            continue
        task_key = sha256_parts(["import_issue", str(row.get("observation_key", row["id"]))])
        if task_key in directives:
            directive_keys.add(task_key)
            continue
        keep_keys.add(task_key)
        tasks.append(
            {
                "task_key": task_key,
                "task_type": "import_issue",
                "subject_type": "observation",
                "subject_key": str(row["id"]),
                "title": "Не удалось определить полный адрес или РЭС",
                "payload": {
                    "observation_id": row["id"],
                    "address": _address_payload(row),
                    "raw_text": row.get("raw_text", ""),
                    "options": [],
                    "allow_multiple": False,
                    "allow_address_edit": True,
                },
                "priority": 95,
            }
        )

    unique_rules = {rule.rule_key: rule for rule in conditional_rules}
    return KnowledgePlan(
        rows=rows,
        valid_rows=valid,
        groups=dict(groups),
        mappings=mappings,
        conditional_rules=list(unique_rules.values()),
        tasks=tasks,
        keep_keys=keep_keys,
        directive_keys=directive_keys,
    )
