# WRAP_ACTIONS Conformance Tests

Conformance tests for the `WRAP_ACTIONS` extension defined in
[RFC 0008 — Environment Wrap Actions](../../../rfcs/0008-environment-wrap-actions.md).

## What this extension does

`WRAP_ACTIONS` adds three new fields to `<EnvironmentActions>`:

```yaml
<EnvironmentActions> ::= the object:
  onEnter: <Action>          # optional
  onWrapEnvEnter: <Action>   # NEW — optional
  onWrapTaskRun: <Action>    # NEW — optional
  onWrapEnvExit: <Action>    # NEW — optional
  onExit: <Action>           # optional
```

When an active environment defines the wrap hooks, the runtime runs each
hook *instead of* the corresponding lifecycle action of every inner
environment and task. Each hook receives the wrapped action's fields via
the `WrappedAction.*` template variables:

| Variable                      | Type           | Description                                                         |
|-------------------------------|----------------|---------------------------------------------------------------------|
| `WrappedAction.Command`       | `string`       | The `command` from the wrapped action                               |
| `WrappedAction.Args`          | `list[string]` | The `args` from the wrapped action                                  |
| `WrappedAction.Environment`   | `list[string]` | `openjd_env`-defined variables as `["KEY=value", ...]`              |
| `WrappedAction.Timeout`       | `int`          | Timeout of the wrapped action in seconds, or `0` if none            |
| `WrappedEnv.Name`             | `string`       | Name of the inner environment (only in `onWrapEnvEnter`/`onWrapEnvExit`)  |
| `WrappedStep.Name`            | `string`       | Name of the step whose task is being wrapped (only in `onWrapTaskRun`) |

## RFC rules these tests verify

- **All-or-nothing**: defining any wrap hook requires defining all three
  (`onWrapEnvEnter`, `onWrapTaskRun`, `onWrapEnvExit`).
- **Single wrap layer**: at most one environment in the session stack may
  define wrap hooks.
- **Variable scope**: `WrappedAction.*` may be referenced only inside the
  three wrap hooks; `WrappedEnv.*` only inside `onWrapEnvEnter`/`onWrapEnvExit`;
  `WrappedStep.*` only inside `onWrapTaskRun`.
- **Extension gating**: wrap hooks are only valid when `WRAP_ACTIONS` is
  declared in `extensions:`.
- **Interception**: the wrap hooks run instead of the corresponding
  lifecycle actions of inner environments and tasks.
- **Safe forwarding**: with `repr_sh()` from the EXPR extension, arbitrary
  user input (quotes, metacharacters, globs, Unicode) reaches the wrapped
  process verbatim.

## How these tests are designed

The RFC motivates the wrap hooks with Docker/Apptainer use cases, but
those containers aren't required to *test* the mechanism. These tests use
trivial wrap scripts — typically `echo`, `sh -c`, or `printf` — that
observe the injected `WrappedAction.*` variables and write sentinel
markers to stdout.

A test passes if:
- Each wrap hook runs in place of its corresponding lifecycle action
  (verified by sentinels the original action would never emit).
- The injected `WrappedAction.*` variables carry the exact bytes of the
  wrapped action.
- The original action does **not** execute.

## Layout

```
WRAP_ACTIONS/
├── README.md                       (this file)
├── env_templates/                  # Env template validation tests
│   ├── 4.2--minimal-wrap-env-template.yaml
│   ├── 4.2--wrap-only-environment.yaml
│   ├── 4.2--wrap-with-timeout.yaml
│   ├── 4.2--empty-actions-no-wrap-no-enter-no-exit.invalid.yaml
│   ├── 4.3--wrap-only-onwrap-task-run.invalid.yaml
│   └── 4.3--wrap-missing-onwrap-exit.invalid.yaml
└── jobs/                           # End-to-end execution tests
    │  # Interception — each hook runs in place of its lifecycle action
    ├── wrap-intercepts-simple-echo.test.yaml
    ├── wrap-original-onrun-does-not-run.test.yaml
    ├── wrap-enter-and-exit-three-hooks.test.yaml
    ├── wrap-enter-intercepts-inner-on-enter.test.yaml
    ├── wrap-exit-intercepts-inner-on-exit.test.yaml
    ├── wrap-env-own-lifecycle-not-wrapped.test.yaml
    ├── wrap-three-hooks-python.test.yaml
    ├── wrap-three-hooks-windows.test.yaml
    │  # WrappedAction.* / WrappedEnv.* / WrappedStep.* injection
    ├── wrap-enter-receives-wrapped-action.test.yaml
    ├── wrap-exit-receives-wrapped-action.test.yaml
    ├── wrap-task-command-injected.test.yaml
    ├── wrap-task-args-preserved.test.yaml
    ├── wrap-task-environment-injected.test.yaml
    ├── wrap-task-action-timeout-injected.test.yaml
    ├── wrap-task-action-timeout-zero-when-unset.test.yaml
    ├── wrap-timeout-injected-python.test.yaml
    ├── wrap-no-args.test.yaml
    ├── wrap-empty-environment-list.test.yaml
    ├── wrap-openjd-env-from-wrapped-onenter-propagates.test.yaml
    │  # Exit-status propagation
    ├── wrap-exit-status-propagates.test.yaml
    ├── wrap-exit-status-python.test.yaml
    ├── wrap-exit-status-windows.test.yaml
    │  # Composition / coexistence
    ├── wrap-outer-env-without-wrap-ignored.test.yaml
    ├── wrap-runs-for-every-task.test.yaml
    ├── wrap-ignores-wrapped-action-vars.test.yaml
    ├── wrap-job-environment.test.yaml
    ├── wrap-step-environment.test.yaml
    │  # Safe forwarding of arbitrary user input (see Security matrix below)
    ├── wrap-preserves-shell-metacharacters.test.yaml
    ├── wrap-preserves-nested-quotes.test.yaml
    ├── wrap-preserves-unicode-cjk.test.yaml
    ├── wrap-preserves-empty-arg.test.yaml
    ├── wrap-glob-characters-literal.test.yaml
    ├── wrap-path-traversal-literal.test.yaml
    │  # Invalid templates / sessions the runner must reject
    ├── wrap-without-extension-fails.invalid.test.yaml
    ├── wrap-without-expr-extension.invalid.test.yaml
    ├── wrap-multiple-wrap-envs-rejected.invalid.test.yaml
    ├── wrap-job-and-step-env-rejected.invalid.test.yaml
    ├── wrap-partial-hooks-rejected.invalid.test.yaml
    └── wrap-wrappedaction-outside-wrap-hook-rejected.invalid.test.yaml
```

Most execution tests use POSIX shell commands (`sh`, `echo`, `printf`) and
are gated to `runOn: [posix]`. Cross-platform variants use Python
(`*-python`), and the `*-windows` variants use `cmd` and are gated to
`runOn: [windows]`.

## Security test matrix — RFC §"Security Considerations"

The RFC's [Security Considerations](../../../rfcs/0008-environment-wrap-actions.md#security-considerations)
section requires that arbitrary user-supplied command/argument bytes reach
the wrapped process verbatim, with shell metacharacters quoted (not
interpreted) by `repr_sh()`. This matrix maps those concerns to the job
bundles in this directory:

| # | Scenario                          | Test file                                       |
|---|-----------------------------------|-------------------------------------------------|
| 1 | Nested quoting                    | `wrap-preserves-nested-quotes.test.yaml`        |
| 2 | Shell metacharacters              | `wrap-preserves-shell-metacharacters.test.yaml` |
| 3 | Path traversal                    | `wrap-path-traversal-literal.test.yaml`         |
| 4 | Shell globbing                    | `wrap-glob-characters-literal.test.yaml`        |
| 5 | Unicode paths                     | `wrap-preserves-unicode-cjk.test.yaml`          |
| 6 | Empty / whitespace-only arguments | `wrap-preserves-empty-arg.test.yaml`            |
| 7 | Newlines in arguments             | Rejected by the 2023-09 `ArgString` regex —     |
|   |                                   | no dedicated conformance test needed.           |
| 8 | Near-limit command length         | Covered by unit tests in the implementation.    |
|   |                                   | Conformance runners vary in stdout buffer       |
|   |                                   | limits, so this is left to implementation tests |
|   |                                   | rather than conformance.                        |

## Additional semantics tested

- **Three-hook interception**: each hook runs in place of its
  corresponding lifecycle action
  (`wrap-enter-intercepts-inner-on-enter`,
  `wrap-exit-intercepts-inner-on-exit`,
  `wrap-enter-and-exit-three-hooks`, plus the cross-platform
  `wrap-three-hooks-python` and `wrap-three-hooks-windows` variants).
- **Wrapping env's own lifecycle is never wrapped**: a wrapping
  environment's own `onEnter`/`onExit` are not intercepted by its own wrap
  hooks (`wrap-env-own-lifecycle-not-wrapped`).
- **Variable injection**: `WrappedAction.Command/Args/Environment/Timeout`
  reach the wrap script with the right values
  (`wrap-task-command-injected`, `wrap-task-args-preserved`,
  `wrap-task-environment-injected`, `wrap-task-action-timeout-injected`,
  `wrap-no-args`, `wrap-empty-environment-list`), and `WrappedAction.*` is
  in scope in the env-lifecycle hooks too
  (`wrap-enter-receives-wrapped-action`,
  `wrap-exit-receives-wrapped-action`).
- **Timeout sentinel**: `WrappedAction.Timeout` is `0` when the wrapped
  action specifies no timeout (`wrap-task-action-timeout-zero-when-unset`,
  `wrap-timeout-injected-python`).
- **`openjd_env` propagation**: variables emitted by a wrapped `onEnter`
  surface in `WrappedAction.Environment` for subsequent wrapped actions
  (`wrap-openjd-env-from-wrapped-onenter-propagates`).
- **Exit-status propagation**: a non-zero wrapped process fails the wrap
  action and therefore the task (`wrap-exit-status-propagates`, plus the
  `-python` and `-windows` variants).
- **Environment placement**: wrap hooks work in `jobEnvironments`
  (`wrap-job-environment`) and `stepEnvironments` (`wrap-step-environment`).
- **Outer-non-wrap-env coexistence**: a non-wrapping environment can
  appear in the stack alongside the single wrapping environment
  (`wrap-outer-env-without-wrap-ignored`).
- **Per-task repeat**: variables re-inject cleanly per task
  (`wrap-runs-for-every-task`).
- **Wrap may ignore injected variables**: a wrap action that references no
  `WrappedAction.*` still runs (`wrap-ignores-wrapped-action-vars`).
- **Original action does not run**: the wrapped lifecycle action is
  replaced, not also executed (`wrap-original-onrun-does-not-run`).
- **Invalid templates / sessions**:
  - `wrap-without-extension-fails`: hooks require the `WRAP_ACTIONS` extension.
  - `wrap-without-expr-extension`: `WRAP_ACTIONS` requires `EXPR`.
  - `wrap-multiple-wrap-envs-rejected`: at most one wrap env per session.
  - `wrap-job-and-step-env-rejected`: single-wrap-layer rule across job and
    step environments.
  - `wrap-partial-hooks-rejected`: all-or-nothing rule (job side).
  - `wrap-wrappedaction-outside-wrap-hook-rejected`: variable scope rule (job side).
  - `4.3--wrap-only-onwrap-task-run`: all-or-nothing rule (env side).
  - `4.3--wrap-missing-onwrap-exit`: all-or-nothing rule (env side).
  - `4.2--empty-actions-no-wrap-no-enter-no-exit`: an `actions` block must
    define at least one action.

## Running the tests

From the repo root:

```bash
# Run just the WRAP_ACTIONS extension tests
uv run conformance-tests/run_openjd_cli_tests.py 2023-09/WRAP_ACTIONS

# Run only the job execution tests
uv run conformance-tests/run_openjd_cli_tests.py 2023-09/WRAP_ACTIONS/jobs

# Pattern-match a single scenario
uv run conformance-tests/run_openjd_cli_tests.py '*wrap*unicode*'
```

## Writing your own runner

See the top-level [conformance README](../../README.md) for the standard
runner contract. The behavior specific to `WRAP_ACTIONS`:

1. When the env template declares `extensions: [WRAP_ACTIONS]`, the
   runner must accept `onWrapEnvEnter`, `onWrapTaskRun`, and `onWrapEnvExit`
   under `environment.script.actions`, and must reject the template if
   any one of the three is defined without all three.
2. When an environment with wrap hooks is entered, every subsequent
   inner-environment lifecycle action and task `onRun` must execute the
   corresponding wrap hook in place of the original action, with
   `WrappedAction.*` variables populated from the wrapped action's fields.
3. When two or more environments in the session stack define any wrap
   hook, the runner must reject the session before entering any
   environment.
4. When wrap-hook fields are used without `WRAP_ACTIONS` in the
   `extensions` list, the runner must reject the template at validation
   time.
5. When a template lists `WRAP_ACTIONS` without also listing `EXPR`, the
   runner must reject the template at validation time.
