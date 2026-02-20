
* Feature Name: Conditional Steps
* RFC Tracking Issue: (to be created)
* Start Date: 2026-02-20
* Specification Version: 2023-09 extension EXPR
* Accepted On: (pending)

## Summary

This RFC adds an optional `condition` field to `<StepTemplate>` that allows template authors to
conditionally include or exclude steps from a job's execution graph based on job parameter values.
When a step's condition evaluates to `false`, the step is skipped entirely — no tasks are created
or run for it, and steps that depend on it treat the dependency as satisfied.

## Basic Examples

### Conditional rendering quality

A single template supports both draft and final rendering workflows. The appropriate step runs
based on the `Quality` parameter, and the `Encode` step runs regardless of which render path
was taken.

```yaml
specificationVersion: jobtemplate-2023-09
extensions:
  - EXPR
name: ConditionalRender
parameterDefinitions:
  - name: Quality
    type: STRING
    allowedValues: ["draft", "final"]
  - name: SceneFile
    type: PATH
    objectType: FILE
    dataFlow: IN
steps:
  - name: FinalRender
    condition: "{{ Param.Quality == 'final' }}"
    bash:
      script: |
        render --quality high --scene {{repr_sh(Param.SceneFile)}}
  - name: DraftRender
    condition: "{{ Param.Quality == 'draft' }}"
    bash:
      script: |
        render --quality low --scene {{repr_sh(Param.SceneFile)}}
  - name: Encode
    dependencies:
      - dependsOn: FinalRender
      - dependsOn: DraftRender
    bash:
      script: |
        encode --input renders/
```

When submitted with `Quality=final`, only `FinalRender` and `Encode` run. `DraftRender` is
skipped entirely.

### Optional post-processing

A step that only runs when the user opts in:

```yaml
specificationVersion: jobtemplate-2023-09
extensions:
  - EXPR
name: RenderWithOptionalDenoise
parameterDefinitions:
  - name: Frames
    type: STRING
    default: "1-100"
  - name: Denoise
    type: BOOL
    default: false
steps:
  - name: Render
    parameterSpace:
      taskParameterDefinitions:
        - name: Frame
          type: INT
          range: "{{Param.Frames}}"
    bash:
      script: |
        render --frame {{Task.Param.Frame}}
  - name: Denoise
    condition: "{{ Param.Denoise }}"
    dependencies:
      - dependsOn: Render
    parameterSpace:
      taskParameterDefinitions:
        - name: Frame
          type: INT
          range: "{{Param.Frames}}"
    bash:
      script: |
        denoise --frame {{Task.Param.Frame}}
```

### Condition without EXPR extension

Without the EXPR extension, the condition field accepts simple value references that must
resolve to the strings `"true"` or `"false"`:

```yaml
specificationVersion: jobtemplate-2023-09
name: SimpleConditional
parameterDefinitions:
  - name: RunCleanup
    type: STRING
    default: "true"
    allowedValues: ["true", "false"]
steps:
  - name: Process
    script:
      actions:
        onRun:
          command: process
  - name: Cleanup
    condition: "{{Param.RunCleanup}}"
    dependencies:
      - dependsOn: Process
    script:
      actions:
        onRun:
          command: cleanup
```

## Motivation

Open Job Description currently defines a static step DAG — every step in a template always runs.
This creates friction for common workflows:

1. **Branching workflows** — A template that supports both draft and final quality rendering must
   either maintain separate templates, generate templates in code, or run both steps with one
   doing nothing. None of these are satisfactory: separate templates duplicate content, code
   generation loses portability, and no-op steps waste scheduler resources and clutter job views.

2. **Optional post-processing** — Steps like denoising, compositing, or cleanup are often
   optional. Without conditional steps, template authors must either always run them (wasting
   resources) or use wrapper scripts that check a parameter and exit early (obscuring intent
   and still consuming a scheduler slot).

3. **Platform-specific steps** — A cross-platform template may need different steps for
   different operating systems. Currently this requires code generation or running all
   platform variants with most being no-ops.

These motivations are reflected in community discussions:
- [Include/exclude parts of a template #81](https://github.com/OpenJobDescription/openjd-specifications/discussions/81)
- [Extend types and the template substitution language #79](https://github.com/OpenJobDescription/openjd-specifications/discussions/79)

This proposal builds on the EXPR extension (RFC 0005) which provides the expression evaluation
infrastructure needed for boolean conditions, though the feature also works without EXPR using
simple string value references.

## Specification

### Extension Name

This feature requires the `EXPR` extension when using expression syntax in the condition.
When using only simple format string references that resolve to `"true"` or `"false"`, no
additional extension is required beyond what the condition value references need.

### Schema Changes

A modification to [`<StepTemplate>`](https://github.com/OpenJobDescription/openjd-specifications/wiki/2023-09-Template-Schemas#3-steptemplate):

```diff
  name: <StepName>
+ condition: <ConditionString> # @optional @fmtstring
  description: <Description> # @optional
  dependencies: [ <StepDependency>, ... ] # @optional
  stepEnvironments: [ <Environment>, ... ] # @optional
  hostRequirements: <HostRequirements> # @optional
  parameterSpace: <StepParameterSpaceDefinition> # @optional
  script: <StepScript>
```

Where:

1. *condition* — A format string that is evaluated at job creation time. The result determines
   whether the step is included in the job. If the condition evaluates to `false`, the step is
   excluded from the job entirely: no tasks are created, and the step does not appear in the
   job's execution graph.
   - If not provided, the step is always included (equivalent to `condition: "true"`).
   - See: [`<ConditionString>`](#conditionstring).

#### `<ConditionString>`

A format string subject to the following constraints:

1. Allowed characters: Same as `<FormatString>`.
2. Minimum length: 1 character.
3. Maximum length: 512 characters, before the format string has been resolved.

The format string is evaluated at job creation time using the template-scope symbol table
(`Param.*` and `RawParam.*` only). It must not reference task-scope symbols (`Task.Param.*`,
`Session.*`, `Task.File.*`, `Env.File.*`).

**With the EXPR extension:** The expression within `{{ }}` must evaluate to a `bool` type value.

**Without the EXPR extension:** The resolved string must be exactly `"true"` or `"false"`
(case-insensitive). Any other resolved value is a validation error.

### Evaluation Timing

The `condition` field is evaluated during job creation — the same phase that resolves
`<JobName>` and other template-scope format strings. This means:

1. The condition is evaluated once, when the job is created from the template.
2. Only `Param.*` and `RawParam.*` symbols are available (not `Task.Param.*` or `Session.*`).
3. The result is fixed for the lifetime of the job.

This is intentional: the scheduler must know the complete DAG structure at job creation time
to plan scheduling. A condition that depends on runtime state would make the DAG
non-deterministic from the scheduler's perspective.

### Effect on the Step Dependency Graph

When a step's condition evaluates to `false`:

1. **The step is excluded from the job.** No `Step` object is created for it. It does not
   appear in the step dependency graph. No tasks are generated.

2. **Dependencies on the skipped step are treated as satisfied.** If step B depends on
   skipped step A, the dependency edge is removed and B is free to run as if the dependency
   did not exist.

3. **Transitive dependencies are unaffected.** If C depends on B depends on A, and A is
   skipped, then B's dependency on A is satisfied but B still runs (assuming B's own condition
   is true), and C still depends on B.

4. **Validation of dependency names still applies at template parse time.** A `dependsOn`
   value that does not match any step name in the template is still a validation error. The
   condition only affects whether the step is included at job creation time, not whether the
   name is valid.

### Interaction with Other Features

**Parameter Space:** A skipped step's parameter space is never evaluated. No tasks are created.

**Step Environments:** A skipped step's `stepEnvironments` are never entered.

**Host Requirements:** A skipped step's `hostRequirements` are ignored.

**Job Environments:** Job-level environments are unaffected by step conditions. They apply to
all sessions regardless of which steps are active.

## Design Choice Rationale

### Evaluation at Job Creation Time

The condition is evaluated at job creation time rather than at step scheduling time. This
ensures the DAG is fully determined before any tasks run, which is critical for schedulers
that need to plan resource allocation and scheduling order upfront.

An alternative would be to evaluate conditions at step scheduling time, allowing conditions
to depend on the output of previous steps. This was rejected because:
- It would require the scheduler to support dynamic DAG modification.
- It would make job behavior harder to predict and debug.
- It would complicate the specification significantly.
- The identified use cases (branching on parameters, optional steps) are all satisfiable
  with job-creation-time evaluation.

### Skipped Dependencies Treated as Satisfied

When a step depends on a skipped step, the dependency is treated as satisfied rather than
causing the dependent step to also be skipped. This was chosen because:

1. It enables the primary use case — branching workflows where step C depends on both
   step A and step B, but only one of A or B runs.
2. It is the simpler mental model: "if the step doesn't exist, there's nothing to wait for."
3. Cascading skip behavior can be achieved by adding conditions to downstream steps.

An alternative "cascading skip" semantic was considered where skipping A would automatically
skip anything that depends on A. This was rejected because:
- It breaks the branching workflow pattern (the most common use case).
- It is harder to reason about in complex DAGs.
- It can be explicitly achieved by adding conditions to dependent steps.

### Boolean-Only Condition Type

The condition must evaluate to a boolean (or the strings "true"/"false" without EXPR). A
numeric or string condition with "truthiness" semantics was rejected because:
- It would introduce ambiguity (is `0` false? is `""` false?).
- It conflicts with the EXPR RFC's design choice to not have a "truthy" concept.
- Explicit boolean conditions are clearer and less error-prone.

### No `else` or `switch` Construct

A higher-level construct like "run step A else step B" was considered but rejected:
- It adds complexity to the schema for a pattern easily expressed with two conditions.
- It couples steps together in a way that reduces composability.
- The condition field on each step is more general and orthogonal.

## Prior Art

### GitHub Actions

GitHub Actions supports [`if` conditionals](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idif)
on both jobs and steps. The `if` field accepts an expression that determines whether the
job/step runs. Skipped jobs are treated as successful for dependency purposes, matching
the semantic proposed here.

### Argo Workflows

Argo Workflows supports [`when` conditionals](https://argo-workflows.readthedocs.io/en/latest/conditional-artifacts-parameters/)
on DAG tasks. When a task's `when` condition is false, the task is skipped and downstream
tasks that depend on it can still run.

### Apache Airflow

Airflow supports [branching](https://airflow.apache.org/docs/apache-airflow/stable/howto/operator/python.html#branchpythonoperator)
via `BranchPythonOperator` which selects which downstream tasks to run. Tasks not selected
are skipped. Airflow also has `trigger_rule` on tasks to control how skipped upstream tasks
affect downstream execution.

### AWS Step Functions

Step Functions supports [`Choice` states](https://docs.aws.amazon.com/step-functions/latest/dg/amazon-states-language-choice-state.html)
that evaluate conditions and branch to different states. This is a more powerful construct
than what we propose, but the core concept of conditional execution based on input values
is the same.

## Rejected Ideas

### Runtime-Evaluated Conditions

Allowing conditions to reference `Task.Param.*` or `Session.*` was rejected because it
would require dynamic DAG modification at runtime, significantly complicating scheduler
implementations and making job behavior harder to predict.

### Step Groups with Conditions

A construct that groups multiple steps under a single condition was considered:

```yaml
stepGroups:
  - condition: "{{ Param.Quality == 'final' }}"
    steps:
      - name: HighQualityRender
        ...
      - name: HighQualityComposite
        ...
```

This was rejected because:
- It adds a new structural concept to the schema.
- The same result is achieved by putting the same condition on each step.
- It complicates the dependency graph (can steps outside the group depend on steps inside?).

### Cascading Skip Behavior

Having a skipped step automatically skip all steps that depend on it was rejected as the
default behavior. See the rationale in [Design Choice Rationale](#skipped-dependencies-treated-as-satisfied).

### Configurable Dependency Behavior on Skip

Adding an `onSkipped: run | skip` field to `<StepDependency>` was considered:

```yaml
dependencies:
  - dependsOn: Render
    onSkipped: skip  # Also skip this step if Render is skipped
```

While this provides maximum flexibility, it was rejected for the initial proposal because:
- It adds complexity to the dependency model.
- The primary use cases don't require it.
- It can be added in a future RFC if needed.
- The same effect can be achieved by adding a condition to the dependent step.

## Open Questions

### Should skipped steps appear in job status?

When viewing a job's status, should skipped steps appear at all? Options:
1. **Not shown** — The job looks like the skipped steps were never part of the template.
2. **Shown as "skipped"** — The step appears with a "skipped" status, making it clear
   that the template included it but the condition excluded it.

Option 2 provides better observability and debugging, but requires schedulers to track
steps that were excluded. This is left to the implementation.

### Should conditions support `Env.Param.*` references?

If environment templates define parameters, should step conditions be able to reference
them? This would enable patterns like "skip this step if the queue environment indicates
a specific configuration." This is deferred to a future RFC.

## Copyright

This document is placed in the public domain or under the CC0-1.0-Universal license, whichever is more permissive.
