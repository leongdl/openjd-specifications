# WRAP_TASK_RUN Conformance Tests

Conformance tests for the `WRAP_TASK_RUN` extension defined in [RFC 0008 — Environment Wrap Task Run](../../../rfcs/0008-environment-wrap-task-run.md).

## What this extension does

`WRAP_TASK_RUN` adds a third action to `<EnvironmentActions>`:

```yaml
<EnvironmentActions> ::= the object:
  onEnter: <Action>       # optional
  onWrapTaskRun: <Action> # optional — NEW
  onExit: <Action>        # optional
```

When an active environment defines `onWrapTaskRun`, the runtime runs that action
*instead of* the task's `onRun`. The wrap action receives the original task's
command, args, environment, and timeout as template variables:

| Variable                | Type            | Description                                        |
|-------------------------|-----------------|----------------------------------------------------|
| `Task.Command`          | `string`        | The command from the task's `onRun`                |
| `Task.Args`             | `list[string]`  | The args from the task's `onRun`                   |
| `Task.Environment`      | `list[string]`  | `openjd_env` variables as `["KEY=value", ...]`     |
| `Env.Action.Timeout`    | `int`           | The timeout of the current action, in seconds      |

## How these tests are designed

The RFC motivates `onWrapTaskRun` with Docker/Apptainer use cases, but those
containers aren't required to *test* the mechanism. These tests use trivial
wrap scripts — typically `echo`, `sh -c`, or `printf` — that observe the
injected template variables and write sentinel markers to stdout.

A test passes if:
- The wrap action runs (verified by a sentinel the original step would never emit).
- The injected template variables carry the exact bytes of the original task.
- The original step's `onRun` does **not** execute.

This approach makes the tests portable, fast, and readable while still
exercising the full runtime path that a real container environment would use.

## Layout

```
WRAP_TASK_RUN/
├── README.md                       (this file)
├── env_templates/                  # Env template validation tests
│   ├── 4.2--minimal-wrap-env-template.yaml
│   ├── 4.2--wrap-only-environment.yaml
│   ├── 4.2--wrap-with-timeout.yaml
│   └── 4.2--empty-actions-no-wrap-no-enter-no-exit.invalid.yaml
└── jobs/                           # End-to-end execution tests
    ├── wrap-intercepts-simple-echo.test.yaml
    ├── wrap-original-onrun-does-not-run.test.yaml
    ├── wrap-task-command-injected.test.yaml
    ├── wrap-task-args-preserved.test.yaml
    ├── wrap-task-environment-injected.test.yaml
    ├── wrap-task-env-action-timeout-injected.test.yaml
    ├── wrap-preserves-shell-metacharacters.test.yaml
    ├── wrap-preserves-nested-quotes.test.yaml
    ├── wrap-preserves-unicode-cjk.test.yaml
    ├── wrap-preserves-empty-arg.test.yaml
    ├── wrap-glob-characters-literal.test.yaml
    ├── wrap-path-traversal-literal.test.yaml
    ├── wrap-innermost-environment-wins.test.yaml
    ├── wrap-outer-env-without-wrap-ignored.test.yaml
    ├── wrap-runs-for-every-task.test.yaml
    ├── wrap-without-extension-fails.invalid.test.yaml
    └── wrap-missing-task-command-reference.test.yaml
```

All execution tests use POSIX shell commands (`sh`, `echo`, `printf`, `cat`),
so they are gated to `runOn: [posix]`. A future pass could add Windows
equivalents using `cmd` or `powershell`.

## Test matrix — RFC §"Recommended test cases for implementation"

The RFC lists 8 scenarios that any implementation should validate. This matrix
maps them to the job bundles in this directory:

| # | RFC scenario                      | Test file                                       |
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
|   |                                   | limits, so this is left to implementation       |
|   |                                   | tests rather than conformance.                  |

## Additional semantics tested

Beyond the 8 security scenarios, this suite also validates:

- **Interception**: the wrap action runs instead of `onRun`
  (`wrap-intercepts-simple-echo.test.yaml`,
  `wrap-original-onrun-does-not-run.test.yaml`).
- **Symbol injection**: `Task.Command`, `Task.Args`, `Task.Environment`,
  and `Env.Action.Timeout` reach the wrap script with the right values.
- **Stack ordering**: the innermost environment's wrap action is used
  (`wrap-innermost-environment-wins.test.yaml`).
- **Outer-without-wrap fallthrough**: an outer environment without a
  wrap action doesn't swallow tasks
  (`wrap-outer-env-without-wrap-ignored.test.yaml`).
- **Repeatability**: symbols re-inject cleanly per task
  (`wrap-runs-for-every-task.test.yaml`).
- **Extension gating**: the field requires the `WRAP_TASK_RUN` extension
  (`wrap-without-extension-fails.invalid.test.yaml`).

## Running the tests

From the repo root:

```bash
# Run just the WRAP_TASK_RUN extension tests
uv run conformance-tests/run_openjd_cli_tests.py 2023-09/WRAP_TASK_RUN

# Run only the job execution tests
uv run conformance-tests/run_openjd_cli_tests.py 2023-09/WRAP_TASK_RUN/jobs

# Pattern-match a single scenario
uv run conformance-tests/run_openjd_cli_tests.py '*wrap*unicode*'
```

## Writing your own runner

See the top-level [conformance README](../../README.md) for the standard runner
contract. The key behaviour specific to `WRAP_TASK_RUN`:

1. When the job template declares `extensions: [WRAP_TASK_RUN]`, the runner
   must accept `onWrapTaskRun` under `environment.script.actions`.
2. When an environment with `onWrapTaskRun` is entered via `--env`, every
   subsequent `openjd run ...` invocation must execute the wrap action in
   place of the step's `onRun`, with the listed template variables
   populated.
3. When a template uses `onWrapTaskRun` *without* `WRAP_TASK_RUN` in the
   `extensions` list, the runner must reject the template at validation
   time (this is covered by the `.invalid.test.yaml` fixture).
