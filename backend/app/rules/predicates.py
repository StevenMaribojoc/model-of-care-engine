"""The predicate registry: how YAML criteria become callables.

A criteria node compiles to a function ``(PatientFacts) -> (matched, evidence)``.
Returning evidence alongside the boolean is the point of the design: a tier
assignment that cannot explain itself is not usable in a clinical setting, and
"why is this patient High Risk?" is the first question staff will ask.

Extension path, in increasing order of cost:
  * a new program built from existing predicates  -> YAML only
  * a new kind of criterion (e.g. "has had N ED visits in 12 months")
        -> one function plus one @register line
  * a new *shape* of rule (e.g. cross-patient cohort logic) -> real design work

Compilation happens once at startup. Anything unknown -- a misspelled predicate, a
code set that does not exist, a lab test not in the reference list -- raises here,
so a bad config fails loudly at boot rather than silently matching nobody in
production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from dateutil.relativedelta import relativedelta

from app.domain.models import PatientFacts

# (matched, evidence)
PredicateResult = tuple[bool, dict[str, Any]]
Predicate = Callable[[PatientFacts], PredicateResult]


class ConfigError(ValueError):
    """Raised for any malformed or unresolvable rule configuration."""


@dataclass(frozen=True)
class RuleContext:
    """Reference data a predicate needs in order to compile and validate."""

    code_set_prefixes: dict[str, tuple[str, ...]]
    known_lab_tests: frozenset[str]


class PredicateFactory(Protocol):
    def __call__(self, arg: Any, ctx: RuleContext) -> Predicate: ...


_REGISTRY: dict[str, PredicateFactory] = {}
_COMBINATORS = {"all", "any", "not"}


def register(name: str) -> Callable[[PredicateFactory], PredicateFactory]:
    def decorator(factory: PredicateFactory) -> PredicateFactory:
        _REGISTRY[name] = factory
        return factory

    return decorator


def registered_predicates() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# --------------------------------------------------------------------------- #
# Leaf predicates
# --------------------------------------------------------------------------- #


@register("age_gte")
def _age_gte(arg: Any, ctx: RuleContext) -> Predicate:
    threshold = _require_int(arg, "age_gte")

    def predicate(facts: PatientFacts) -> PredicateResult:
        # Unknown date of birth cannot satisfy an age floor. Failing closed keeps a
        # missing birthday from silently promoting someone into a high-risk tier.
        if facts.age is None:
            return False, {"age": None}
        return facts.age >= threshold, {"age": facts.age}

    return predicate


@register("age_lt")
def _age_lt(arg: Any, ctx: RuleContext) -> Predicate:
    threshold = _require_int(arg, "age_lt")

    def predicate(facts: PatientFacts) -> PredicateResult:
        if facts.age is None:
            return False, {"age": None}
        return facts.age < threshold, {"age": facts.age}

    return predicate


@register("has_diagnosis")
def _has_diagnosis(arg: Any, ctx: RuleContext) -> Predicate:
    if not isinstance(arg, str):
        raise ConfigError(f"has_diagnosis expects a code set name, got {arg!r}")
    if arg not in ctx.code_set_prefixes:
        raise ConfigError(
            f"has_diagnosis references unknown code set {arg!r}; "
            f"known: {sorted(ctx.code_set_prefixes)}"
        )
    prefixes = ctx.code_set_prefixes[arg]
    code_set = arg

    def predicate(facts: PatientFacts) -> PredicateResult:
        matched = tuple(
            code
            for code in facts.diagnosis_codes
            if any(_code_matches(code, p) for p in prefixes)
        )
        return bool(matched), {"code_set": code_set, "matched_codes": list(matched)}

    return predicate


@register("lab_latest")
def _lab_latest(arg: Any, ctx: RuleContext) -> Predicate:
    spec = _require_mapping(arg, "lab_latest")
    test = _require_lab_test(spec, ctx, "lab_latest")
    within_months = _require_int(spec.get("within_months"), "lab_latest.within_months")
    bounds = _parse_bounds(spec, "lab_latest")
    if not bounds:
        raise ConfigError("lab_latest needs at least one of gte/gt/lte/lt")

    def predicate(facts: PatientFacts) -> PredicateResult:
        lab = _lab_in_window(facts, test, within_months)
        if lab is None:
            return False, {"test": test, "value": None, "reason": "no result in window"}
        evidence = {
            "test": test,
            "value": lab.result_value,
            "result_date": lab.result_date.isoformat(),
        }
        return all(check(lab.result_value) for check in bounds), evidence

    return predicate


@register("lab_missing")
def _lab_missing(arg: Any, ctx: RuleContext) -> Predicate:
    spec = _require_mapping(arg, "lab_missing")
    test = _require_lab_test(spec, ctx, "lab_missing")
    within_months = _require_int(spec.get("within_months"), "lab_missing.within_months")

    def predicate(facts: PatientFacts) -> PredicateResult:
        lab = _lab_in_window(facts, test, within_months)
        if lab is None:
            stale = facts.latest_labs.get(test)
            return True, {
                "test": test,
                "value": None,
                "last_result_date": stale.result_date.isoformat() if stale else None,
                "window_months": within_months,
            }
        return False, {
            "test": test,
            "value": lab.result_value,
            "result_date": lab.result_date.isoformat(),
        }

    return predicate


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #


def compile_criteria(node: Any, ctx: RuleContext, *, path: str = "criteria") -> Predicate:
    """Turn a criteria mapping into a single predicate.

    Shapes accepted:
        {}                                  always matches (catch-all tier)
        {all: [...]} / {any: [...]}         combinators over child nodes
        {not: {...}}                        negation
        {age_gte: 65, has_diagnosis: X}     multiple leaf keys are an implicit AND
    """
    node = {} if node is None else node
    if not isinstance(node, dict):
        raise ConfigError(f"{path} must be a mapping, got {type(node).__name__}")

    if not node:
        return lambda facts: (True, {})

    combinators = _COMBINATORS & node.keys()
    if combinators:
        if len(node) > 1:
            raise ConfigError(
                f"{path}: {sorted(combinators)} must be the only key in its mapping; "
                "wrap the other conditions in their own node"
            )
        return _compile_combinator(next(iter(combinators)), node, ctx, path)

    children = [_compile_leaf(name, arg, ctx, path) for name, arg in node.items()]
    return children[0] if len(children) == 1 else _and(children)


def _compile_combinator(name: str, node: dict, ctx: RuleContext, path: str) -> Predicate:
    arg = node[name]
    if name == "not":
        inner = compile_criteria(arg, ctx, path=f"{path}.not")

        def negated(facts: PatientFacts) -> PredicateResult:
            matched, evidence = inner(facts)
            return (not matched), evidence

        return negated

    if not isinstance(arg, list) or not arg:
        raise ConfigError(f"{path}.{name} expects a non-empty list of criteria")
    children = [
        compile_criteria(child, ctx, path=f"{path}.{name}[{i}]")
        for i, child in enumerate(arg)
    ]
    return _and(children) if name == "all" else _any(children)


def _compile_leaf(name: str, arg: Any, ctx: RuleContext, path: str) -> Predicate:
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ConfigError(
            f"{path}: unknown predicate {name!r}; known: {registered_predicates()}"
        )
    return factory(arg, ctx)


def _and(children: list[Predicate]) -> Predicate:
    def predicate(facts: PatientFacts) -> PredicateResult:
        evidence: dict[str, Any] = {}
        matched = True
        for child in children:
            child_matched, child_evidence = child(facts)
            # Evaluate every branch even after a failure: the evidence from the
            # branches that did pass is what makes a near-miss explainable.
            evidence.update(child_evidence)
            matched = matched and child_matched
        return matched, evidence

    return predicate


def _any(children: list[Predicate]) -> Predicate:
    def predicate(facts: PatientFacts) -> PredicateResult:
        evidence: dict[str, Any] = {}
        for child in children:
            child_matched, child_evidence = child(facts)
            if child_matched:
                # Short-circuit and report only the branch that actually matched,
                # so evidence reads as "qualified because X" rather than a dump.
                return True, child_evidence
            evidence.update(child_evidence)
        return False, evidence

    return predicate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _code_matches(code: str, prefix: str) -> bool:
    """Prefix match that respects ICD-10 structure.

    An ICD-10 code is a category (the part before the decimal, e.g. ``G47``)
    followed by an optional subclassification (``.33``). Everything after the
    decimal narrows the same condition, so:

    * a prefix that already contains a decimal may extend freely --
      ``G47.3`` matches ``G47.33``, which the spec requires ("G47.3x -- Sleep
      Apnea" is in the chronic conditions group);
    * a bare category prefix must stop at a boundary -- ``I10`` matches ``I10``
      and ``I10.9`` but not a different category that merely starts with those
      characters.

    A naive ``startswith`` gets the first case right and the second wrong; a
    strict boundary check gets it exactly backwards. Both halves matter.
    """
    if not code.startswith(prefix):
        return False
    rest = code[len(prefix) :]
    if rest == "" or "." in prefix:
        return True
    return rest[0] == "."


def _lab_in_window(facts: PatientFacts, test: str, within_months: int):
    """Most recent result for ``test`` that falls inside the lookback window.

    ``facts.latest_labs`` already holds the most recent result on or before as_of,
    so checking that one against the window is equivalent to searching the window:
    if the newest result is too old, nothing newer exists inside it either.
    """
    lab = facts.latest_labs.get(test)
    if lab is None:
        return None
    window_start = facts.as_of - relativedelta(months=within_months)
    return lab if lab.result_date > window_start else None


def _parse_bounds(spec: dict, label: str) -> list[Callable[[float], bool]]:
    checks: list[Callable[[float], bool]] = []
    for key, op in (
        ("gte", lambda v, t: v >= t),
        ("gt", lambda v, t: v > t),
        ("lte", lambda v, t: v <= t),
        ("lt", lambda v, t: v < t),
    ):
        if key in spec:
            threshold = float(spec[key])
            checks.append(lambda v, t=threshold, op=op: op(v, t))
    return checks


def _require_mapping(arg: Any, label: str) -> dict:
    if not isinstance(arg, dict):
        raise ConfigError(f"{label} expects a mapping, got {arg!r}")
    return arg


def _require_int(arg: Any, label: str) -> int:
    if isinstance(arg, bool) or not isinstance(arg, int):
        raise ConfigError(f"{label} expects an integer, got {arg!r}")
    return arg


def _require_lab_test(spec: dict, ctx: RuleContext, label: str) -> str:
    test = spec.get("test")
    if not isinstance(test, str):
        raise ConfigError(f"{label} requires a 'test' name")
    if test not in ctx.known_lab_tests:
        raise ConfigError(
            f"{label} references unknown lab test {test!r}; "
            f"declare it in reference.yaml (known: {sorted(ctx.known_lab_tests)})"
        )
    return test
