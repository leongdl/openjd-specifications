# Proposed fixtures (extended parameter types) — job_templates

These fixtures are believed spec-correct but fail against at least one
current reference implementation. Placement is kind-level
(`<component>/<kind>/proposed/`): the runner does not scan `proposed/`, and
promotion is a move up one directory unchanged. This README is named per
fixture family (`README-param-types.md`) because other expected-failures
PRs park fixtures in this same directory with their own READMEs.

Observations are per-implementation (`rs` = openjd-rs, `py` = the Python
openjd CLI); dual results from the 2026-08-12 sweep, rs re-verified against
the current upstream/main build.

## 2.13--list-int-item-int64-overflow.invalid.yaml
## 2.16--list-list-int-inner-item-int64-overflow.invalid.yaml

Both must be rejected: a `LIST[INT]` element / `LIST[LIST[INT]]` inner element
of `9223372036854775808` (2^63) exceeds the int64 range that governs `int`
values (Expression Language §1.2.1 "64-bit signed integer"; the EXPR literal
fixtures `expr2.1.1--int64-overflow-*` pin the same bound for literals).
Observed — accepted by **BOTH rs and py**:

```
$ openjd check 2.13--list-int-item-int64-overflow.invalid.yaml
Template ... passes validation checks.
$ openjd check 2.16--list-list-int-inner-item-int64-overflow.invalid.yaml
Template ... passes validation checks.
```

Classification: implementation bug in both, the known int64-in-data bug
class (scalar INT parameter defaults have the same defect) one container
deeper. The implementations validate int64 bounds for expression literals
and arithmetic but not for values arriving as YAML data in list defaults.
Same caveat as the expr-lang family: the explicit overflow-is-error text
was dropped from the published spec (only the §1.2.1 type-table row
remains), so promotion is additionally gated on restoring that text.
Accept twins at 2^63-1: `2.13--list-int-item-int64-max.yaml` /
`2.16--list-list-int-inner-item-int64-max.yaml` in the param-types-gaps PR.

## 2.10--range-expr-endpoint-int64-max.yaml

Must be accepted: Template Schemas §3.4.1.1.1 defines `<Int>` as "Any integer
value (positive, negative, or zero)", and §2.10 requires only that a
RANGE_EXPR default be a valid `<IntRangeExpr>`. Observed:

```
$ openjd check 2.10--range-expr-endpoint-int64-max.yaml
ERROR: Model validation error: 1 validation error for JobTemplate
parameterDefinitions[0]:
	Parameter 'Frames': default '9223372036854775806-9223372036854775807' is not a valid range expression.
```

Probing shows the implementation accepts endpoints up to 2^62-1
(4611686018427387903) and rejects 2^62 (4611686018427387904) and above.
This is **rs-only** — py accepts the valid endpoints (the quoted error is
rs output; openjd-rs uses the same "Model validation error" format as
pydantic, re-verified against the current upstream/main build).
Classification: openjd-rs implementation bug (undocumented 2^62 endpoint
cap; the spec grammar admits any int64 endpoint). Note: the companion
negative `2.10--range-expr-endpoint-int64-overflow.invalid.yaml` (in the
parent directory, param-types-gaps PR) currently passes on rs because of
this same over-rejection, so its rejection reason is wrong until this
positive is green — promote as a pair.

## 3.4.1--task-param-type-case-insensitive.yaml

Must be accepted: Template Schemas §2 states that with EXPR enabled "job
parameter and task parameter type names become case-insensitive". Observed:

```
$ openjd check 3.4.1--task-param-type-case-insensitive.yaml
ERROR: Validation error: 'jobtemplate-2023-09' failed checks: unknown variant `int`, expected one of `INT`, `FLOAT`, `STRING`, `PATH`, `CHUNK[INT]`
```

Classification: implementation bug in **BOTH** implementations (2026-08-12
sweep; the quoted serde-style error is rs — py fails with its own message).
Task parameter type names are matched exactly; case-insensitivity is
implemented for job parameter types only. Missing reject twin worth adding
separately: task-param lowercase type WITHOUT EXPR (nothing pins that gate
anywhere in the suite).

## Dropped: 2--type-lowercase-string.invalid.yaml

An identical base type-lowercase pin also exists on the base
expected-failures PR (`base/job_templates/proposed/2--type-lowercase
.invalid.yaml`). This PR's copy was dropped to avoid promoting the same
pin twice; that branch's copy is the single source.
