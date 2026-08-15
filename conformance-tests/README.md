# OpenJD Conformance Tests

A conformance test suite for validating any OpenJD implementation against the specification. These tests define expected behavior that all compliant libraries should exhibit.

## Purpose

The test cases in this suite are implementation-agnostic. They specify:
- Templates that should pass or fail validation
- Jobs that should succeed or fail execution
- Expected outputs and behaviors

Any OpenJD library can use these tests to verify spec compliance.

## Structure

```
conformance-tests/
└── {spec_version}/
    ├── base/                    # Base specification tests
    │   ├── job_templates/       # Job template validation tests
    │   ├── env_templates/       # Environment template validation tests
    │   └── jobs/                # Job execution tests
    └── {EXTENSION_NAME}/        # Extension-specific tests
        ├── job_templates/
        └── jobs/
```

Example:
```
conformance-tests/
└── 2023-09/
    ├── base/
    │   ├── job_templates/
    │   ├── env_templates/
    │   └── jobs/
    ├── TASK_CHUNKING/
    │   ├── job_templates/
    │   └── jobs/
    └── REDACTED_ENV_VARS/
        └── jobs/
```

### Naming Convention

Filenames encode the spec section they test:

```
<spec-section>--<description>[.invalid][.suffix].yaml
```

- `<spec-section>` - Reference to the [Template Schema](../wiki/2023-09-Template-Schemas.md) section (e.g., `1.1`, `3.3.2`, `7.3`)
- `.invalid` - Test should FAIL validation/execution
- `.invalid.test` - Job execution test that should FAIL at runtime (in `jobs/` directory). Use this when the template passes static validation but the error only fires during evaluation.
- `.test` - Job execution test (in `jobs/` directory)

For the `EXPR` extension, tests may reference either the Template Schema or the
[Expression Language](../wiki/2026-02-Expression-Language.md) specification. Tests referencing
the Expression Language use the `expr` prefix:

```
expr<section>--<description>[.invalid][.suffix].yaml
```

- `expr<section>` - Reference to the Expression Language section (e.g., `expr1.1`, `expr2.2.4`)

Examples:
- `1.1--minimal-job-template.yaml` - Section 1.1 (Job Template root)
- `3.3.2--allof.yaml` - Section 3.3.2 (AttributeRequirement)
- `5--cancelation-notify-then-terminate.yaml` - Section 5 (Action)
- `2.1--missing-name.invalid.yaml` - Invalid test for Section 2.1
- `contiguous-even.test.yaml` - TASK_CHUNKING extension execution test (in `TASK_CHUNKING/jobs/`)
- `2.9--bool-param-default-true.yaml` - EXPR: Template Schema §2.9 (JobBoolParameterDefinition)
- `3.6--let-step-level.yaml` - EXPR: Template Schema §3.6 (LetBindings)
- `expr1.1--arithmetic-expr.yaml` - EXPR: Expression Language §1.1 (Extended Format String Grammar)
- `expr2.2.4--upper.test.yaml` - EXPR: Expression Language §2.2.4 (String Functions)

### Extension Tests

Extension tests are organized in their own directories (e.g., `2023-09/TASK_CHUNKING/`). Extensions must be explicitly enabled in templates via the `extensions` field:

```yaml
specificationVersion: jobtemplate-2023-09
extensions:
  - REDACTED_ENV_VARS
name: MyJob
# ...
```

Some tests (like `redaction-without-extension.test.yaml` in `REDACTED_ENV_VARS/jobs/`) intentionally omit the `extensions` field to verify behavior when extension syntax is used without enabling the extension.

### Job Execution Test Format

Job execution tests use a unified single-file format (`.test.yaml`):

```yaml
# The job template to test (required)
template:
  specificationVersion: jobtemplate-2023-09
  name: MyJob
  steps:
    - name: Step1
      script:
        actions:
          onRun:
            command: echo
            args: ["{{Param.Value}}"]

# Optional: parameters to pass when running the job
parameters:
  MyParam: "override-value"

# Optional: environment templates (supports multiple)
environments:
  - specificationVersion: environment-2023-09
    environment:
      name: Env1
      variables:
        FOO: bar

# Optional: path mapping rules
pathMapping:
  - source_path_format: POSIX
    source_path: /mnt/shared
    destination_path: /local/shared

# Optional: restrict which operating systems this test runs on (default: all)
# Valid values: "posix", "windows"
runOn:
  - posix
  - windows

# Optional: expected output assertions
expected:
  output:
    - LINE1
    - LINE2
  forbidden:
    - SHOULD_NOT_APPEAR
  # Platform-specific assertions (optional)
  output_posix:
    - PATH:/unix/style
  output_windows:
    - PATH:D:\windows\style
  forbidden_posix:
    - /wrong/path
  forbidden_windows:
    - D:\wrong\path
  # Optional: assert the run ended in a task failure, and (optionally) that a
  # specific process exit code propagated to the task.
  taskFailure:
    exitCode: 42
```

Platform-specific assertions (`output_posix`, `output_windows`, `forbidden_posix`, `forbidden_windows`) are merged with the base `output` and `forbidden` lists at runtime based on the current platform. Use these when tests involve filesystem paths or other platform-dependent behavior.

The `taskFailure` assertion is for non-`.invalid.` job tests that succeed at the CLI level (the `openjd run` invocation itself does not error) but must still verify that a task *failed* — for example, that a wrapped process' non-zero exit status propagated through a wrap script. When present:

- The run MUST exit non-zero (the session reported a task failure). A run that succeeds fails the assertion.
- If `exitCode` is given, the first non-zero `exited with code: <N>` line in the run output MUST report that code. All failures are terminal for a session, so the first non-zero code is the failing action's; later lines belong to teardown actions (`onWrapEnvExit`, `onExit`) that run after the failure and may legitimately exit `0`. The `exited with code:` substring is emitted by conforming CLIs (`Process exited with code: 42`, `Process pid 1234 exited with code: 42 (unsigned) / ...`), so the assertion is portable across implementations.

Use `taskFailure` instead of the `.invalid.test.yaml` suffix when you also want to assert specific `output`/`forbidden` lines on a failing run, or when you need to pin the propagated exit code.

A valid job test with no `taskFailure` MUST exit `0`. Without that requirement the verdict
rests entirely on substring matching, and a case whose task failed still passes as long as
the expected lines turn up anywhere in the output — including inside the failure's own
diagnostics.

The `runOn` field restricts a test to run only on the listed operating systems. If omitted, the test runs on all platforms. Use this when a test requires a platform-specific command (e.g., `cmd` or `powershell` on Windows, `bash` on POSIX).

### Self-asserting job tests

Many single-task job tests assert their own output, in addition to declaring it in
`expected`. Their `onRun` takes one leading argument — an `OpenJDConformanceAssert`
embedded file — followed by the case's original command and args:

```yaml
      actions:
        onRun:
          command: python
          args:
          - '{{Task.File.OpenJDConformanceAssert}}'
          - python
          - -c
          - print(r'OUTPUT:{{Param.Version}}')
```

The wrapper runs `sys.argv[1:]`, echoes its output verbatim, compares that output
against literals baked into the wrapper, and exits non-zero on mismatch. The task's
exit status then carries the verdict.

This exists so that an implementation which can only observe **task status** — a
service, rather than a CLI whose stdout the runner can read — still verifies output
*content* rather than only that the task ran and exited `0`. `expected.output` and
`expected.forbidden` are unchanged, so a runner that scans logs is unaffected; both
mechanisms check the same thing by different means.

Four properties matter if you write or edit one of these:

- **The original argv is passed through, not embedded in the wrapper.** The
  implementation still expands the args list, so a case whose subject *is* that
  expansion stays under test — `expr1.3.2--list-flattens-in-args` asserts
  `ARG0:--width` … `COUNT:10`, which only holds if the list flattening still happens
  where it always did.
- **The literals are literals.** `expected.output` holds the already-substituted
  value (`OUTPUT:1.0`, not `OUTPUT:{{Param.Version}}`). Injecting them by parameter
  reference would make the comparison self-referential: a substitution bug would
  corrupt both sides identically and the case would pass.
- **Diagnostics report a line's position, never its text.**
  `OPENJD_CONFORMANCE_ASSERT_FAILED: expected output line 1 of 2 not found`. Printing
  the literal would place it in the very output a log-scanning runner searches, so a
  failing case would satisfy that runner with its own error message. Read the position
  against the case's `expected` block.
- **Braces in a literal are written `\u007b` / `\u007d`.** Embedded file data is itself
  a format string, and several cases forbid a literal `{{Param.` in their output.
  Written raw, that literal would be parsed as a substitution.

On success the wrapper prints `OPENJD_CONFORMANCE_ASSERT_OK: <n> expected, <m>
forbidden`. That line is how you confirm the assertion actually ran, rather than the
case having passed for some other reason — worth checking, since the wrapper is only
reached if the implementation actually executes the task's action.

Four kinds of case deliberately stay status-only, because a task cannot assert them:

- **Multi-task cases.** Each task sees only its own output, so a per-task exit code
  cannot assert that N lines appeared across N tasks. These need aggregation outside
  the job.
- **Cases asserting the implementation's log handling.** `4--openjd-redacted-env`
  expects `SECRET_IS:********`, which is the implementation's *redaction* of what the
  task printed. The task sees the real value, so nothing it can observe about its own
  output distinguishes redacted from leaked. Most of `REDACTED_ENV_VARS` is the same.
- **Cases whose expected output comes from elsewhere.** `7.3--env-file-reference`
  asserts a line printed by a job environment's `onEnter`; its task prints something
  else entirely. `WRAP_ACTIONS` is excluded wholesale for the same reason plus a
  stronger one: `onWrapTaskRun` *replaces* the task action, so a wrapper on the inner
  action is usually never executed.
- **Cases whose subject is the args list itself.** `wrap-no-args` asserts that
  `WrappedAction.Args` surfaces as an empty list when the wrapped action has no args.
  Adding an argument changes the subject.

## Writing Your Own Test Runner

To validate your OpenJD library against these tests:

1. Parse the test files and naming conventions
2. For `*.yaml` files (without `.invalid`): verify your library accepts them
3. For `*.invalid.yaml` files: verify your library rejects them
4. For job execution tests (`.test.yaml`): extract template/parameters/environments, run the job, verify outputs match `expected` assertions, and require a clean exit unless the test declares `expected.taskFailure`

### Example Test Runner for openjd CLI

The included `run_openjd_cli_tests.py` demonstrates how to run these tests using the `openjd` CLI. Implementers can adapt this approach or write their own runner targeting their library's API.

```bash
uv run run_openjd_cli_tests.py                          # Run all tests
uv run run_openjd_cli_tests.py 2023-09                  # Run all 2023-09 tests
uv run run_openjd_cli_tests.py 2023-09/base             # Run base spec tests only
uv run run_openjd_cli_tests.py 2023-09/TASK_CHUNKING    # Run TASK_CHUNKING extension tests
uv run run_openjd_cli_tests.py 2023-09/*/jobs           # Run all job execution tests
uv run run_openjd_cli_tests.py '*/*/jobs/*param*'       # Pattern match test names
```
