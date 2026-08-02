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

The `runOn` field restricts a test to run only on the listed operating systems. If omitted, the test runs on all platforms. Use this when a test requires a platform-specific command (e.g., `cmd` or `powershell` on Windows, `bash` on POSIX).

### Additional Assertion Keys

These optional keys pin down *why* a fixture fails and *how* a run is ordered, beyond the single accept/reject bit.

The `expectedError` key (on `.invalid.test.yaml` job fixtures) pins the reason a fixture was rejected, so a fixture that is rejected for an unrelated reason (a typo, an unrelated validation rule) fails instead of silently passing. A plain string is shorthand for `contains`. `contains` may be a string or a list of strings that must ALL appear in the run output; `anyOf` is a list of which at least ONE must appear — `anyOf` exists because implementations phrase the same rule differently. Matching is case-sensitive substring containment, and when `contains` and `anyOf` are both given, both must hold.

```yaml
template:
  # ... a template that must be rejected at runtime ...
expectedError:
  anyOf:
    - "must be greater than or equal to 1"
    - "must be at least 1"
```

Raw template fixtures (`job_templates/`, `env_templates/`) are passed to `openjd check` verbatim, so a foreign YAML key would itself change validation. For those, the directive instead lives in the contiguous `#` comment block at the top of the file. The directive must appear in that leading block: a directive in a comment anywhere after it is a hard error (it would otherwise be silently ignored), and positive (non-`.invalid`) templates may not carry one.

```yaml
# expectedError:
#   contains: "uses reserved scope 'worker'"
specificationVersion: jobtemplate-2023-09
# ...
```

The `expected.outputSequence` key asserts an ordered subsequence over the run output: entries must appear in order but need not be adjacent. A flat list of strings is one chain; a list of lists is independent chains (e.g. diamond dependencies whose two branches are mutually unordered). The `outputSequence_posix` / `outputSequence_windows` variants are each checked as their own independent chain set, never concatenated with the base list. A chain needs at least 2 entries (a 1-entry chain is a membership check — use `expected.output`). `outputSequence` is not allowed in `.invalid` fixtures; write failure-ordering cases as `.test.yaml` with `expected.taskFailure` instead.

```yaml
expected:
  outputSequence:
    - ["ROOT", "BRANCH-A", "LEAF"]
    - ["ROOT", "BRANCH-B", "LEAF"]
```

The `expected.taskCount` key asserts the exact number of task runs in the output, counted via the task banner conforming CLIs emit once per task run.

```yaml
expected:
  taskCount: 4
```

Fixtures skipped via `runOn` are reported per directory and in the final `Total:` line, and the runner's `--fail-on-skip-ratio X` flag (X in [0, 1]) exits non-zero when skipped/(passed+failed+skipped) exceeds X, so platform-restricted fixtures cannot silently become the majority of a run.

Note that an `expected:` block in an `.invalid` fixture is NOT enforced (a historical quirk; the runner prints a note when it discards one).

The runner's own self-tests can be run with `uv run --with pytest,pyyaml python -m pytest test_run_openjd_cli_tests.py`.

## Writing Your Own Test Runner

To validate your OpenJD library against these tests:

1. Parse the test files and naming conventions
2. For `*.yaml` files (without `.invalid`): verify your library accepts them
3. For `*.invalid.yaml` files: verify your library rejects them
4. For job execution tests (`.test.yaml`): extract template/parameters/environments, run the job, verify outputs match `expected` assertions

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
