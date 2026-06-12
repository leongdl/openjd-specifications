---

* Feature Name: Environment Wrap Actions
* Author(s): David Leong <[leongdl](https://github.com/leongdl)>
* RFC Tracking Issue: https://github.com/OpenJobDescription/openjd-specifications/issues/132
* Start Date: 2026-04-16
* Specification Version: 2023-09 extension WRAP_ACTIONS
* Accepted On: (pending)
* Depends On:
  * RFC 0002 — Model Extensions (https://github.com/OpenJobDescription/openjd-specifications/issues/57)
  * RFC 0005 — Expression Language (https://github.com/OpenJobDescription/openjd-specifications/pull/93)
  * RFC 0006 — Expression Function Library (https://github.com/OpenJobDescription/openjd-specifications/pull/104)

## Summary

This RFC proposes extending `<Environment>` with three new session actions —
`onWrapEnter`, `onWrapTaskRun`, and `onWrapExit` — that let an environment template
intercept and wrap the lifecycle actions of *inner* environments and tasks. The runtime
supplies each wrap action with the wrapped action's command, args, timeout, cancelation
method, and environment variables as template variables. A companion opt-out,
`runOnHost: true` on `<Action>`, lets individual actions bypass wrapping when they
must run on the host (credential fetching, mount setup, cleanup that must always run).

The primary motivation is container support: a Docker or Apptainer environment template
can start a container in `onEnter`, route every inner environment's `onEnter`/`onExit`
and every task's `onRun` into the container via the three wrap hooks, and stop the
container in `onExit`. Job templates and inner environments remain portable across
Conda, Rez, Docker, and Apptainer.

## Basic Examples

### Docker environment template

This environment template runs inner environments and tasks inside a Docker container.
The container starts once in `onEnter`, every inner action is forwarded into the
container via the three wrap hooks, and the container stops in `onExit`.

```yaml
specificationVersion: "environment-2023-09"
extensions:
- WRAP_ACTIONS
- EXPR
parameterDefinitions:
- name: ContainerImage
  type: STRING
  description: The container image to run jobs in.
  default: "ubuntu:latest"
  userInterface:
    control: LINE_EDIT
    label: Container Image

environment:
  name: Docker
  script:
    actions:
      onEnter:
        command: "bash"
        args: ["{{Env.File.Enter}}"]
      onWrapEnter:
        command: "bash"
        args: ["{{Env.File.WrapEnter}}"]
        timeout: "{{Env.Wrapped.Timeout}}"
      onWrapTaskRun:
        command: "bash"
        args: ["{{Env.File.WrapTaskRun}}"]
        timeout: "{{Task.Timeout}}"
      onWrapExit:
        command: "bash"
        args: ["{{Env.File.WrapExit}}"]
        timeout: "{{Env.Wrapped.Timeout}}"
      onExit:
        command: "bash"
        args: ["{{Env.File.Exit}}"]
    embeddedFiles:
    - name: Enter
      filename: docker-env-enter.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail

        # Authenticate with the container registry (e.g., ECR)
        aws ecr get-login-password --region us-west-2 \
            | docker login --username AWS --password-stdin \
              "$(echo '{{Param.ContainerImage}}' | cut -d/ -f1)"

        # Pull the requested image
        docker image pull '{{Param.ContainerImage}}'

        # Run the container in the background with the session directory mounted
        DOCKER_CONTAINER_ID=$(docker container run --rm \
            --pull never \
            --detach \
            --network host \
            --mount 'type=bind,src={{Session.WorkingDirectory}},dst={{Session.WorkingDirectory}}' \
            --mount 'type=bind,src=/sessions,dst=/sessions' \
            '{{Param.ContainerImage}}' \
            bash -c 'sleep infinity')

        # Export the container ID for subsequent actions
        echo "openjd_env: DOCKER_CONTAINER_ID=$DOCKER_CONTAINER_ID"

    # Wrap an inner environment's onEnter action: forward its command into the
    # container so that installs, activations, and warm-up affect the container
    # image, not the host.
    - name: WrapEnter
      filename: docker-wrap-enter.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        echo "[Docker] Running onEnter for env '{{Env.Wrapped.Name}}' inside container"
        docker container exec \
            $DOCKER_CONTAINER_ID \
            {{ repr_sh(flatten([['-e', e] for e in Env.Wrapped.Environment])) }} \
            {{ repr_sh(Env.Wrapped.Command) }} \
            {{ repr_sh(Env.Wrapped.Args) }}

    # Wrap the task's onRun action. Escaping is critical here: Task.Command and
    # Task.Args may contain spaces, quotes, dollar signs, backticks, glob
    # characters, or other shell metacharacters.
    #
    # repr_sh() applies shlex.quote to each element individually:
    #   repr_sh("hello world")       => 'hello world'
    #   repr_sh('say "hi"')          => 'say "hi"'
    #   repr_sh("it's")              => "it's"
    #   repr_sh("$HOME")             => '$HOME'
    #   repr_sh(["a", "b c", "d"])   => a 'b c' d
    #
    # The flatten + repr_sh pattern for environment variables produces:
    #   -e 'KEY1=value1' -e 'KEY2=has spaces' -e 'KEY3=has"quotes'
    - name: WrapTaskRun
      filename: docker-wrap-task-run.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        docker container exec \
            $DOCKER_CONTAINER_ID \
            {{ repr_sh(flatten([['-e', e] for e in Task.Environment])) }} \
            {{ repr_sh(Task.Command) }} \
            {{ repr_sh(Task.Args) }}

    # Wrap an inner environment's onExit action: forward teardown into the
    # container so cleanup targets the container state, not the host.
    - name: WrapExit
      filename: docker-wrap-exit.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        echo "[Docker] Running onExit for env '{{Env.Wrapped.Name}}' inside container"
        docker container exec \
            $DOCKER_CONTAINER_ID \
            {{ repr_sh(flatten([['-e', e] for e in Env.Wrapped.Environment])) }} \
            {{ repr_sh(Env.Wrapped.Command) }} \
            {{ repr_sh(Env.Wrapped.Args) }}

    - name: Exit
      filename: docker-env-exit.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail

        # Stop the container, allowing up to 30 seconds for a graceful shutdown
        # before SIGKILL.
        docker container stop \
            $DOCKER_CONTAINER_ID \
            --timeout 30
```

### Apptainer environment template

The same pattern applies to Apptainer (daemonless, each wrap hook invokes
`apptainer exec` directly rather than exec'ing into a running container). See
[Appendix A: Apptainer environment template](#appendix-a-apptainer-environment-template)
for the full example.

### Job template that works with any environment

This job template renders frames with Blender. It works identically whether the queue uses
a Conda, Rez, Docker, or Apptainer environment template. The job template does not know
or care about the execution context.

It also shows the `runOnHost: true` opt-out on an inner step environment that must mount
an NFS share on the *host* — mounting inside a container would defeat the purpose. The
`BlenderSetup` step environment, by contrast, does *not* set `runOnHost`, so its
`pip install` runs inside the container (modifying the container's Python environment)
when the queue has a wrapping container environment active.

```yaml
specificationVersion: 'jobtemplate-2023-09'
name: Blender Render
parameterDefinitions:
  - name: SceneFile
    type: PATH
    objectType: FILE
    dataFlow: IN
  - name: Frames
    type: STRING
    default: "1-100"
steps:
  - name: Render
    stepEnvironments:
      # Runs ON THE HOST even when a wrapping container env is active.
      # Mounting the share must happen on the host so the container can
      # bind-mount it.
      - name: NFSMount
        script:
          actions:
            onEnter:
              command: mount
              args: ["-t", "nfs", "fileserver:/renders", "/mnt/renders"]
              runOnHost: true
            onExit:
              command: umount
              args: ["/mnt/renders"]
              runOnHost: true
      # Runs INSIDE the wrapping container (if any). The Blender plugin
      # install needs to modify the container's Python environment.
      - name: BlenderSetup
        script:
          actions:
            onEnter:
              command: pip
              args: ["install", "--quiet", "blender-batch==2.1"]
            onExit:
              command: pip
              args: ["uninstall", "--yes", "blender-batch"]
    parameterSpace:
      taskParameterDefinitions:
        - name: Frame
          type: INT
          range: "{{Param.Frames}}"
    script:
      actions:
        # Runs INSIDE the wrapping container (if any) via onWrapTaskRun.
        onRun:
          command: blender
          args:
            - "--background"
            - "{{Param.SceneFile}}"
            - "--frame-set"
            - "{{Task.Param.Frame}}"
```

### Execution order with a wrapping Docker environment

When the job above runs on a queue with the Docker environment as a queue environment,
the execution order demonstrates how the three wrap hooks and `runOnHost` compose:

```
Docker.onEnter                            → HOST      (starts container)
  NFSMount.onEnter                        → HOST      (runOnHost: true)
    BlenderSetup.onEnter                  → CONTAINER (via Docker.onWrapEnter)
      [task 1] blender ...                → CONTAINER (via Docker.onWrapTaskRun)
      [task 2] blender ...                → CONTAINER (via Docker.onWrapTaskRun)
      ...
    BlenderSetup.onExit                   → CONTAINER (via Docker.onWrapExit)
  NFSMount.onExit                         → HOST      (runOnHost: true)
Docker.onExit                             → HOST      (stops container)
```

On a Conda queue with no wrapping environment, the Docker layer simply does not exist
and every action runs on the host. The job template and the inner step environments are
unchanged.

## Motivation

Environment templates can prepare the context in which a job runs — installing software,
setting environment variables, starting background daemons. But they cannot change *how*
the actions within the session are executed. The existing
[bash-in-docker sample](https://github.com/OpenJobDescription/openjd-specifications/tree/mainline/samples/job_templates/bash-in-docker)
demonstrates a workaround: the step environment starts a Docker container and the task
execs a script inside it. But this approach requires the job template to be written
specifically for Docker, and inner step environments cannot install software inside the
container. Swap the environment from Docker to Conda, and the job template breaks.

The [Design Tenets](https://github.com/OpenJobDescription/openjd-specifications/wiki/Design-Tenets)
call for portability:

> Job templates should be portable in a way to run them, unmodified, with either a Conda,
> Rez, Docker, or Apptainer environment template that provides the software environment
> to run in.

The three wrap hooks close this gap. The outer environment template controls the
execution context for the entire session — including inner environments' lifecycle
actions — and the job template specifies the work. Neither needs to know about the
other. The `runOnHost` opt-out gives specific actions a clean way to decline wrapping
when they fundamentally cannot work inside the wrapped context.

This separation is a proven pattern across workflow ecosystems. Nextflow, CWL, Snakemake,
and WDL all solve the same problem — abstracting the container runtime from the job logic —
using language-specific mechanisms (see Prior Art). OpenJD's approach is unique in using
composable environment templates rather than a global config toggle, which enables per-queue
and per-step runtime selection in multi-step pipelines.

### Use cases

1. **Containers with inner-env setup.** Run a studio-wide Docker or Apptainer container
   at the queue level, and let per-step environments install plugins, activate Conda
   environments, or stage dependencies *inside* the container. The job template and
   the step environments are unchanged when the wrapping environment is swapped or
   removed.

2. **Host-level actions alongside containers.** Actions that must run on the host —
   mounting an NFS share the container will bind-mount, setting up a VPN tunnel for a
   license server, fetching short-lived credentials — use `runOnHost: true` to bypass
   the wrapping container. This replaces a common class of workarounds where authors
   either bake host-specific steps into the outer environment or abandon portability.

3. **Remote execution.** An environment template could SSH into a remote host, or submit
   actions to a cloud API, using the same three wrap hooks. Inner environment setup runs
   on the remote host, not locally.

4. **Instrumentation over the full session.** Wrap every action with profiling, tracing,
   or resource-accounting tools without modifying the job template or the inner
   environments. Profiling only tasks (current RFC) misses the setup phases that are
   often where performance problems hide.

5. **Privilege isolation.** Run inner actions as a different user or with reduced
   capabilities by wrapping the command with `sudo -u`, `unshare`, or a jailed shell.

### Out of scope

The following are explicitly *not* addressed by this RFC and are deferred to follow-up
work if and when concrete use cases emerge:

1. **Nested wrap composition.** Only one environment in the session stack may define
   wrap hooks. See [Rejected Ideas › Nested wrap composition](#nested-wrap-composition).
2. **Periodic health-checking of wrapped processes** (e.g., detecting stalled
   containers). Deferred to a separate RFC for Environment monitoring.
3. **Wrap-aware cancelation.** The wrap hook's own `<Cancelation>` is used for any
   wrapped action; the wrapped action's `<Cancelation>` is not surfaced to the wrap
   script. See [Cancelation behavior](#cancelation-behavior).
4. **Cross-OS wrapping.** The same-path bind-mount requirement assumes the host and
   the wrapped execution context share path-separator conventions (Linux host with
   Linux container, or Windows with Windows). Cross-OS wrapping (e.g., a Windows host
   launching a Linux container) is not supported by this RFC.
5. **Multiple co-equal wrap layers** (e.g., a profiling wrapper *and* a container
   wrapper in the same session). See [Rejected Ideas › Nested wrap composition](#nested-wrap-composition).

### Backward compatibility

This RFC is additive and gated by the `WRAP_ACTIONS` extension name declared under
RFC 0002. Specifically:

- Schedulers that do not implement `WRAP_ACTIONS` MUST reject templates that list it
  in `extensions:`, per RFC 0002's extension-handling rules.
- Schedulers that do implement `WRAP_ACTIONS` MUST ignore the three wrap hooks and
  `runOnHost` on templates that do not list `WRAP_ACTIONS` in `extensions:`, so that
  existing templates without the extension continue to behave exactly as before.
- The new `<Action>.runOnHost` field defaults to `false`, which is the pre-RFC behavior
  for every existing action.
- No existing field changes meaning; `onEnter` and `onExit` continue to behave as they
  do today when no wrap hook is active.

### Performance: amortizing container startup

Starting a container per action is expensive. The `onEnter`/`onWrap*`/`onExit` pattern
amortizes container startup across the whole session: the container starts once, every
enter, every task, and every exit runs inside it, and the container stops when the
session ends. State persists across actions within the session (loaded plugins, cached
data, pip-installed packages), which matches how DCC applications work.

This is the same amortization pattern that task stickiness provides for background
daemons, and that task chunking (RFC 0001) provides for frame ranges.

### Security: container isolation boundaries

Each container instance should be scoped to a single security boundary. In the
`onEnter`/`onExit` model, the container lifecycle is tied to the session — one container
per session. This is the correct isolation granularity because a session already
represents a single security context: it runs under one set of credentials, accesses
one set of job attachments, and writes to one session working directory.

In Deadline Cloud, the session maps to a queue, which is the IAM trust boundary. Each
queue has its own IAM role, and actions from different queues never share a session.
This means the container inherits the queue's credential scope — it can only access
the resources that the queue role permits. A container started in one queue's session
cannot be reused by a different queue's session, even on the same worker host.

Schedulers implementing the wrap hooks should ensure that:

1. The container is stopped and removed in `onExit`, even on failure, so no state leaks
   between sessions.
2. The container does not run with elevated privileges (`--privileged`) unless explicitly
   configured by the environment template author.
3. Bind mounts are scoped to the session working directory and explicitly declared paths,
   not the entire host filesystem.

## Specification

> Changes to [the template schema](https://github.com/OpenJobDescription/openjd-specifications/wiki/2023-09-Template-Schemas).

> A modification to [`<EnvironmentActions>`](https://github.com/OpenJobDescription/openjd-specifications/wiki/2023-09-Template-Schemas#42-environmentactions)

```diff
  <EnvironmentActions> ::= the object:
    onEnter: <Action>
+   onWrapEnter: <Action>    # @optional
+   onWrapTaskRun: <Action>  # @optional
+   onWrapExit: <Action>     # @optional
    onExit: <Action> # @optional
```

1. *onEnter* — The action to run when entering the environment.
2. *onWrapEnter* — If provided, this action is run instead of the `onEnter` action of
   every *inner* environment that enters the session while this environment is active.
   The action receives the wrapped `onEnter`'s context via `Env.Wrapped.*` template
   variables (see below).
3. *onWrapTaskRun* — If provided, this action is run instead of the task's `onRun`
   action for every task that runs while this environment is active. The action
   receives the task's context via `Task.*` template variables (see below).
4. *onWrapExit* — If provided, this action is run instead of the `onExit` action of
   every *inner* environment that exits while this environment is active. The action
   receives the wrapped `onExit`'s context via `Env.Wrapped.*` template variables
   (see below).
5. *onExit* — The action to run when exiting the environment.

> A modification to [`<Action>`](https://github.com/OpenJobDescription/openjd-specifications/wiki/2023-09-Template-Schemas#43-action)

```diff
  <Action> ::= the object:
    command: string
    args: list[string]
    timeout: int
    cancelation: <Cancelation>
+   runOnHost: bool # @optional, default false
```

* *runOnHost* — If `true`, this action always runs directly on the host and is not
  intercepted by any active wrap hook. The default is `false`. This field is only
  meaningful on `onEnter`, `onRun`, and `onExit` actions within an environment or step
  that may be wrapped by an outer environment. Setting `runOnHost: true` on an outer
  environment's wrap hook itself has no effect (the wrap hook is, by definition, what
  other actions are being intercepted *to*).

### Wrap ordering with multiple environments

**This RFC restricts wrap hooks to a single active layer per session.** If more than
one environment in the session stack defines *any* wrap hook, the session is invalid
and the scheduler must reject it before entering any environment. This simplifies the
mental model, the implementation, and error reporting. The design does not preclude
adding nested wrap composition as a future extension if real use cases emerge.

For a session with environment A (queue env with wrap hooks) and environment B (step
env without wrap hooks), execution proceeds:

```
A.onEnter                                 → HOST   (outer env's own onEnter is never wrapped)
  B.onEnter                               → WRAPPED via A.onWrapEnter (unless runOnHost: true)
    Task 1: onRun                         → WRAPPED via A.onWrapTaskRun (unless runOnHost: true)
    Task 2: onRun                         → WRAPPED via A.onWrapTaskRun (unless runOnHost: true)
  B.onExit                                → WRAPPED via A.onWrapExit (unless runOnHost: true)
A.onExit                                  → HOST
```

An environment's own `onEnter` and `onExit` are never wrapped by its own wrap hooks.
The wrap hooks only intercept actions from environments and tasks that are *inner* to
the wrapping environment in the session stack. If a wrapping environment defines only
some of the wrap hooks, inner actions corresponding to the undefined hooks run on the
host as if no wrapping were present.

### New template variables

The following template variables are available in the wrap hooks and their embedded
files. The namespaces are scoped to prevent accidental cross-context references — for
example, `Task.Command` is only meaningful inside `onWrapTaskRun`, and
`Env.Wrapped.Name` is only meaningful inside `onWrapEnter`/`onWrapExit`.

Available in `onWrapEnter` and `onWrapExit`:

| Variable                  | Type           | Description                                                                                                       |
|---------------------------|----------------|-------------------------------------------------------------------------------------------------------------------|
| `Env.Wrapped.Name`        | `string`       | The `name` of the inner environment whose action is being wrapped.                                                |
| `Env.Wrapped.Command`     | `string`       | The `command` from the wrapped environment's action.                                                              |
| `Env.Wrapped.Args`        | `list[string]` | The `args` from the wrapped environment's action.                                                                 |
| `Env.Wrapped.Environment` | `list[string]` | Environment variables defined by `openjd_env` earlier in the session, as `["KEY=value", ...]`.                    |
| `Env.Wrapped.Timeout`     | `int`          | The timeout value specified for the wrapped action, in seconds. If no timeout is specified, this is the default. |

Available in `onWrapTaskRun`:

| Variable             | Type           | Description                                                                                          |
|----------------------|----------------|------------------------------------------------------------------------------------------------------|
| `Task.Command`     | `string`       | The `command` from the task's `onRun` action.                                                        |
| `Task.Args`        | `list[string]` | The `args` from the task's `onRun` action.                                                           |
| `Task.Environment` | `list[string]` | Environment variables defined by `openjd_env` in the current session, as `["KEY=value", ...]`.       |
| `Task.Timeout`     | `int`          | The timeout value in seconds from the wrapped task's `onRun`. Surfaces here so the wrap script can propagate it to the wrapped execution context — e.g. `docker container stop --timeout {{Task.Timeout}}`. If the wrapped action specifies no timeout, this is the spec-defined default. |

Referencing `Task.*` outside `onWrapTaskRun`, or `Env.Wrapped.*` outside
`onWrapEnter`/`onWrapExit`, is an error.

> Modifications to [How Jobs Are Run](https://github.com/OpenJobDescription/openjd-specifications/wiki/How-Jobs-Are-Run)

```diff
  Once the Environments have been created and entered for a Session, a series of Tasks are
  run within that Session and the Environments. Tasks from any Step in a Job can run within
  the same Session provided that the set of Environments that are required to run those
  Tasks are identical.

+ If exactly one Environment in the session stack defines any of onWrapEnter,
+ onWrapTaskRun, or onWrapExit, then the lifecycle actions of inner Environments and
+ tasks are intercepted by the corresponding wrap hook:
+
+ - An inner Environment's onEnter is replaced by the wrapping Environment's onWrapEnter
+   if onWrapEnter is defined and the inner action does not set runOnHost: true. If
+   onWrapEnter is not defined, the inner onEnter runs on the host.
+ - A task's onRun is replaced by the wrapping Environment's onWrapTaskRun if
+   onWrapTaskRun is defined and the task's onRun does not set runOnHost: true. If
+   onWrapTaskRun is not defined, the task's onRun runs on the host.
+ - An inner Environment's onExit is replaced by the wrapping Environment's onWrapExit
+   if onWrapExit is defined and the inner action does not set runOnHost: true. If
+   onWrapExit is not defined, the inner onExit runs on the host.
+
+ The wrapping Environment's own onEnter and onExit are never wrapped; they always run
+ on the host. If more than one Environment in the session stack defines any wrap hook,
+ the session is invalid and the scheduler must reject it before entering any Environment.
```

### Stdout and `openjd_env` propagation

A wrap hook's subprocess is the direct child that the scheduler observes; any wrapped
process (e.g., a command running inside a container via `docker exec`) is a grand-child.
Schedulers scan *the wrap hook's* stdout for `openjd_env: KEY=value` lines, not the
grand-child's. This preserves the existing contract: the scheduler reads stdout from
the action defined in the template.

The wrap script therefore MUST forward the wrapped process's stdout and stderr to its
own stdout and stderr verbatim. For the examples in this RFC this is the default
behavior of `docker container exec` and `apptainer exec`, which forward both streams
by default. Authors of custom wrap scripts (remote execution, privilege shifts, etc.)
MUST preserve this forwarding; dropping or buffering either stream breaks `openjd_env`
propagation, which in turn breaks any inner environment that sets environment
variables for subsequent actions.

Implications for the `Environment` template variables:

- `Env.Wrapped.Environment` and `Task.Environment` contain `openjd_env`-defined
  variables accumulated from all earlier actions in the session — whether those
  actions ran on the host or through a wrap hook — as observed by the scheduler on
  the child's stdout.
- An inner `onEnter` that runs via `onWrapEnter` and emits `openjd_env: FOO=bar`
  will therefore have `FOO=bar` present in `Env.Wrapped.Environment` and
  `Task.Environment` for every subsequent wrapped action in the same session,
  provided the wrap script forwards the inner process's stdout.

Alternatives considered (scheduler scans grand-child stdout; wrap script explicitly
re-emits `openjd_env` lines; dual-scan both streams) are documented in
[Appendix B: Alternatives for `openjd_env` propagation through wrap hooks](#appendix-b-alternatives-for-openjd_env-propagation-through-wrap-hooks).

### Cancelation behavior

The wrap hook's own `<Cancelation>` governs cancelation of any wrapped action. When
the scheduler needs to cancel a wrapped `onEnter`, `onRun`, or `onExit`, it applies
the cancelation method defined on the wrap hook itself, not the cancelation method
defined on the inner action. The wrap script is responsible for propagating the
cancelation signal to the wrapped process. For the container examples in this RFC,
this means the wrap script should trap SIGTERM and signal the container-side process
to stop gracefully (e.g., `docker exec --signal SIGTERM` or forwarding via a PID file).

The wrapped action's own `<Cancelation>` is *not* surfaced to the wrap script via any
template variable, and is not honored while the action is being wrapped. Authors of
inner environments that rely on specific cancelation semantics should either:

1. Use `runOnHost: true` on the sensitive action so the inner `<Cancelation>` applies
   directly, or
2. Coordinate with the wrap environment's author so the wrap hook's cancelation method
   matches their expectations.

This is a deliberate simplification; surfacing the wrapped `<Cancelation>` to the wrap
script is a candidate future extension if a concrete use case emerges. See
[Out of scope](#out-of-scope).

### Timeout behavior

Each wrap hook has its own timeout, specified in the action definition — that
timeout governs the wrap subprocess itself. The timeout *template variable*
(`Env.Wrapped.Timeout` for enter/exit wrappers, `Task.Timeout` for the
task wrapper), however, carries the *wrapped* action's timeout — what the
wrap script needs to propagate to the wrapped execution context so a
`docker container stop --timeout {{Task.Timeout}}` inside the wrap
script stops the inner container with the same timeout the task itself
would have enforced.

If no timeout is specified on a wrap hook, the default timeout behavior from the
specification applies (no timeout, meaning the action can run indefinitely).

## Design Choice Rationale

### Three separate hooks (Option A) rather than a single unified hook (Option B)

An alternative design (Option B in the tracking discussion) uses a single `onWrapAction`
hook with a `WrappedAction.Type` discriminator variable (`TASK_RUN`, `ENV_ENTER`,
`ENV_EXIT`). It is more DRY and lets the wrapper express action-type-specific logic via
EXPR conditionals. However:

1. **Schema explicitness.** Three hooks make the environment's capabilities visible
   directly in the schema. A template reader can see at a glance that `onWrapEnter` is
   defined, which means inner-environment `onEnter` actions are intercepted. With a
   single `onWrapAction`, the same information is buried inside the wrap script, which
   may branch or not on action type.
2. **Focused variable namespaces.** `Task.Command` and `Env.Wrapped.Command` carry
   different contextual meanings and should not share a namespace. Keeping them
   separate prevents authors from accidentally referencing the wrong one.
3. **EXPR not required for the simple case.** Three hooks let authors write
   straightforward shell scripts per action type without the EXPR extension. A unified
   hook practically requires EXPR for any non-trivial use case.
4. **Debug story.** A stack trace or log line citing `onWrapTaskRun` is unambiguous.
   A stack trace citing `onWrapAction` requires the reader to know which branch the
   script took.

The unified hook is strictly more expressive — it can suppress or transform certain
action types — but this extra power comes with the cost of silent wrapping and a less
obvious debug model. A future extension could add `onWrapAction` as shorthand for
"use the same script for all three hooks" if demand emerges.

### `runOnHost: true` as the opt-out

Some actions cannot run inside the wrapped context and still work:

- Mounting an NFS or SMB share that the container will then bind-mount.
- Setting up a VPN tunnel or SSH forward for a license server.
- Fetching short-lived credentials (AWS STS, OAuth tokens) on the host before the
  container starts.
- `onExit` cleanup that must run even if the wrapping container crashed or was
  OOM-killed.

Without an opt-out, authors would either bake host-only steps into the outer environment
(defeating portability) or abandon the wrapping pattern entirely. A declarative
`runOnHost: true` field makes the intent explicit in the template and keeps the
execution model simple.

Alternatives considered:

- **Capability-based negotiation.** Inner environments declare what they need
  (`requiresHost: ["network", "privileged"]`) and the runtime decides whether to
  wrap. This is more general but much more complex and requires authors to reason
  about capabilities rather than execution context.
- **Tag-based filtering.** Inner environments add tags that the wrap hooks filter on.
  This pushes the decision into the wrap script (which must then use EXPR to branch)
  and is less discoverable in the schema.
- **Wrap-hook-side bypass.** The wrap hook inspects the wrapped command and decides
  to forward or not. This makes the outer environment responsible for knowing about
  every inner environment's needs, which is exactly the coupling this RFC is trying
  to eliminate.

`runOnHost: true` on `<Action>` is the simplest model that puts the decision where the
knowledge lives (with the action author) and keeps it visible in the template.

### Extension name: `WRAP_ACTIONS` rather than `WRAP_TASK_RUN`

The earlier iteration of this RFC used `WRAP_TASK_RUN` because only the task's `onRun`
was wrapped. With three hooks covering enter, task run, and exit, the scope has
broadened and `WRAP_TASK_RUN` would be misleading. `WRAP_ACTIONS` reflects that the
extension enables wrapping of any lifecycle action within a session.

### Three hooks on Environment rather than a new template type

Adding the three wrap hooks to the existing `<EnvironmentActions>` keeps the environment
template model intact. Environments already have `onEnter` and `onExit` for setup and
teardown of their own state. The wrap hooks are a natural extension: setup, wrap each
inner lifecycle phase, teardown. A new template type would fragment the model and
require new plumbing in every implementation.

### Template variables rather than environment variables

Each wrap hook receives its context as template variables (`Task.Command`, `Task.Args`,
`Env.Wrapped.Command`, `Env.Wrapped.Args`, etc.) rather than environment variables.
Template variables are type-safe (with the EXPR extension, `Task.Args` and
`Env.Wrapped.Args` are `list[string]` that can be shell-quoted with `repr_sh()`),
available at template expansion time, and consistent with how other context is
provided in OpenJD. Environment variables would require parsing and escaping in every
wrap script.

### Scoped `Env.Wrapped.*` namespace

`onWrapEnter` and `onWrapExit` share the `Env.Wrapped.*` namespace rather than introducing
separate `Env.WrappedEnter.*` / `Env.WrappedExit.*` namespaces. The reason: both hooks
are fundamentally forwarding an inner environment's action, the data shape is identical,
and sharing the namespace lets authors factor common helper scripts. The distinction
between enter and exit is already made by the name of the hook containing the reference.

### Bind mount at the same path

The specification requires that `{{Session.WorkingDirectory}}` is mounted at the identical
path inside the container. This is a deliberate constraint: file references in embedded
files, job attachments, and task parameters use absolute paths resolved against the
session working directory. If the mount path differs, every file reference breaks.
Mounting at the same path means the job template and inner environments work identically
inside and outside the container.

Path mapping rules are applied identically whether or not a wrap hook is active. The
runtime resolves `Param.<name>` for PATH-type parameters using the session's path mapping
rules *before* injecting `Task.Command`, `Task.Args`, and `Env.Wrapped.*` into the wrap
action's symbol table. This means the wrap script receives already-mapped paths — the
same concrete paths that the wrapped action would have received if no wrapping were
present. The container environment template does not need to be aware of path mapping;
it simply bind-mounts the session directory and forwards the command. This preserves
the portability guarantee: swapping between a Conda environment (no wrapping, paths
resolved normally) and a Docker environment (wrapping via the wrap hooks, same paths
resolved the same way) produces identical file references.

### `Task.Environment` and `Env.Wrapped.Environment` as lists of `KEY=value` strings

Environment variables are provided as a flat list of `"KEY=value"` strings rather than a
dictionary. This matches the format expected by `docker exec -e` and `apptainer exec --env`,
and is straightforward to iterate over in a list comprehension:

```yaml
{{ repr_sh(flatten([['-e', e] for e in Task.Environment])) }}
```

A dictionary type would require additional syntax for iteration and would not map as
directly to the CLI flags of container runtimes.

### Command escaping via `repr_sh` rather than raw interpolation

The wrap script must reconstruct the wrapped action's command line inside a shell script.
This is inherently dangerous: if `Task.Command`, `Task.Args`, `Env.Wrapped.Command`, or
`Env.Wrapped.Args` contain shell metacharacters (`"`, `'`, `` ` ``, `&`, `|`, `>`, `<`,
`*`, `?`, `(`, `)`, `\`, newlines), raw interpolation breaks the script or — worse —
executes unintended commands.

The RFC requires the EXPR extension and its `repr_sh()` function (from RFC 0006) for safe
command construction. `repr_sh(string)` applies `shlex.quote` semantics: it wraps the value
in single quotes and escapes embedded single quotes. `repr_sh(list[string])` applies this
to each element individually and joins them with spaces, so each element becomes exactly
one argv entry when the shell parses the line.

This is the same approach that Discussion #83 identified as necessary, but using the
standardized `repr_sh()` function name from RFC 0006 rather than the ad-hoc `shlex_join()`
and `shlex_quote()` names from the original discussion.

For Windows containers (CMD or PowerShell), the equivalent functions are `repr_cmd()` and
`repr_pwsh()` from RFC 0006, which handle the different escaping rules of those shells.

## Security and Execution Constraints

### Command injection and argument safety

When implementing any wrap hook, authors MUST NOT pass raw command strings directly to a
shell parser. Patterns like `bash -c "{{Task.Command}} {{Task.Args}}"` or
`bash -c "{{Env.Wrapped.Command}} {{Env.Wrapped.Args}}"` create a critical command
injection vector if the wrapped command contains nested quotes, backticks, semicolons,
or subshell expressions like `$(...)`.

Instead, wrap scripts MUST treat `Command` and `Args` as distinct tokens and use
`repr_sh()` (or `repr_cmd()`/`repr_pwsh()` on Windows) to produce safely-quoted strings.
In programmatic wrappers (not shell scripts), prefer argument arrays over shell strings:

- **Python**: Use `subprocess.Popen([command] + args)` with a list, never
  `subprocess.Popen(shell_string, shell=True)`.
  See [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations).
- **Rust**: Use `std::process::Command::new(command).args(args)` which passes arguments
  directly to the OS exec layer without shell interpretation.
  See [Rust Command documentation](https://doc.rust-lang.org/std/process/struct.Command.html).

The EXPR extension's `repr_sh()` function is the specification's answer to this problem for
shell-based wrap scripts. It applies `shlex.quote` semantics to each argument individually,
ensuring the OS treats them as literals rather than shell instructions.

### Credential and data leakage into the wrapped context

Wrap hooks change the security context of inner lifecycle actions. Authors of inner
environments may assume their `onEnter` runs on the host and may write secrets, tokens,
or host-only paths to `{{Session.WorkingDirectory}}` that could leak into a container
filesystem if the session acquires a wrapping container environment.

Mitigations:

1. Inner-environment authors who handle secrets should declare `runOnHost: true` on
   the secret-handling action to guarantee it runs on the host, regardless of the
   outer environment.
2. Outer wrap environments SHOULD use per-session container instances (matching the
   "Security: container isolation boundaries" guidance above) so leaked material does
   not cross session boundaries.
3. Documentation for a wrap environment SHOULD state clearly that inner-environment
   actions run inside the wrapped context, so inner-environment authors can reason
   about the destination of any state they write.

### Privilege and namespace shifts for inner actions

An inner environment's `onEnter` authored for the host will, when wrapped, run under
the outer environment's identity, mount namespace, network namespace, and cgroup.
This is almost always the intent — that is the point of the wrapper — but it means
subtle assumptions (which hostname resolves, which user is root, which mounts are
visible) change silently. Inner-environment authors who require host semantics for
a particular action should set `runOnHost: true` on that action.

### Maximum command length limits

Wrap hooks are subject to the host operating system's maximum command line length.
Because a wrap hook effectively "doubles" the command string — once in the wrapper's
invocation and once in the wrapped action's command — implementations must be mindful
of platform limits:

| Platform | Limit Type | Typical Constraint |
|----------|-----------|-------------------|
| Windows (CMD) | Character count | 8,191 characters |
| Windows (CreateProcess API) | Character count | 32,767 characters |
| Linux (modern kernels) | Byte size | ~2,097,152 bytes (typically 1/4 of stack size) |
| macOS | Byte size | ~262,144 bytes (`ARG_MAX`) |

This "double penalty" means an action whose command is close to the OS limit when run
directly may exceed it when wrapped. Container runtimes add their own overhead:
`docker exec` prepends the container ID, environment flags (`-e KEY=value` for each
variable), and the image name to the command line. A session with many `openjd_env`
variables can push the total well past the limit, and this applies to all three wrap
hooks, not just task runs.

**Unicode impact**: On Linux and macOS, `ARG_MAX` limits are enforced in bytes, not
characters. Multi-byte characters (CJK characters consume 3 bytes in UTF-8, emoji consume
4 bytes) mean a command that appears short in character count may still trigger an
`Argument list too long` (E2BIG) error. This is particularly relevant for studios with
CJK file paths or project names. A path like `/projects/映画/シーン01/レンダー.exr`
is 42 characters but 66 bytes in UTF-8.

Implementations SHOULD validate the total command line length before exec and produce a
clear error message referencing the OS limit, rather than letting the OS return a cryptic
E2BIG or "command line too long" error.

### Recommended test cases for implementation

To verify the robustness of a wrap environment, the following scenarios SHOULD be
validated against each of the three wrap hooks:

1. **Nested quoting**: Commands containing both single and double quotes
   (e.g., `echo "O'Reilly's Guide"`). Verify `repr_sh()` produces correct output.

2. **Shell metacharacters**: Arguments containing `"`, `'`, `` ` ``, `|`, `&&`, `;`,
   `>`, `<`, `*`, `?`, `(`, `)`. Verify they are treated as literals, not interpreted
   by the shell.

3. **Path traversal**: Arguments containing `../` do not allow the wrapped process to
   escape intended container or session directory boundaries.

4. **Shell globbing**: Verify that `ls *` is passed literally to the wrapped action,
   not expanded by the wrapper shell. `repr_sh()` prevents this by quoting, but test
   it explicitly.

5. **Unicode paths**: Execute actions where the wrapped `Command` or directory names
   contain CJK characters, emoji, or other multi-byte sequences. Verify encoding
   parity between the wrapper and the host.

6. **Empty and whitespace-only arguments**: Verify that `Args` containing `""` or
   `"  "` are preserved as distinct arguments, not collapsed or dropped.

7. **Newlines in arguments**: Arguments containing `\n` characters must be preserved
   literally, not interpreted as command separators.

8. **Near-limit command length**: Construct an action with arguments totaling close
   to the OS `ARG_MAX` limit and verify the wrap hook either succeeds or produces a
   clear error.

9. **`runOnHost: true` isolation**: Verify that an action with `runOnHost: true`
   inside an inner environment runs directly on the host and does not invoke any
   wrap hook, even when the outer environment defines all three wrap hooks.

10. **Wrap hook precedence**: Verify the scheduler rejects sessions where more than
    one environment defines any wrap hook.

### End-to-end conformance tests for `onWrapEnter`, `onWrapExit`, and `runOnHost`

These tests verify the routing semantics of the new hooks and the `runOnHost` opt-out
using only `echo`, `cat`, and simple file creation — no containers or external services
required. Each test observes behavior through a "marker file" pattern: each action
appends a tagged line to a file in `{{Session.WorkingDirectory}}`, and the final file
contents demonstrate which actions ran via which hook.

Implementations can run these against a trivial "wrapper" that simulates an isolated
execution context by prepending a marker. In the examples below, the outer env's wrap
hooks prepend `[WRAPPED]` to every forwarded action's output, so a line in the marker
file starting with `[WRAPPED]` proves the action went through the wrap hook, and a
line without it proves the action ran directly on the host.

#### Test 1: `onWrapEnter` intercepts inner `onEnter`

Outer env defines `onWrapEnter`. Inner step env's `onEnter` writes a marker. The
expected marker file contents prove interception.

```yaml
# Outer env (queue env)
environment:
  name: Wrapper
  script:
    actions:
      onEnter:
        command: "bash"
        args: ["-c", "echo 'outer-onEnter ran on host' >> '{{Session.WorkingDirectory}}/trace.log'"]
      onWrapEnter:
        command: "bash"
        args:
          - "-c"
          - >-
            echo "[WRAPPED] inner-onEnter via onWrapEnter for env={{Env.Wrapped.Name}}"
            >> '{{Session.WorkingDirectory}}/trace.log'
            && {{ repr_sh(Env.Wrapped.Command) }} {{ repr_sh(Env.Wrapped.Args) }}
      onExit:
        command: "bash"
        args: ["-c", "echo 'outer-onExit ran on host' >> '{{Session.WorkingDirectory}}/trace.log'"]
```

```yaml
# Inner step env
stepEnvironments:
  - name: InnerEnter
    script:
      actions:
        onEnter:
          command: "bash"
          args: ["-c", "echo 'inner-onEnter body' >> '{{Session.WorkingDirectory}}/trace.log'"]
```

**Expected** `trace.log` contents after session teardown:

```
outer-onEnter ran on host
[WRAPPED] inner-onEnter via onWrapEnter for env=InnerEnter
inner-onEnter body
outer-onExit ran on host
```

**Pass criteria**: The `[WRAPPED]` line appears before `inner-onEnter body`,
`Env.Wrapped.Name` resolves to `InnerEnter`, and the outer env's own `onEnter` is *not*
prefixed with `[WRAPPED]`.

#### Test 2: `onWrapExit` intercepts inner `onExit`

Same outer env but with `onWrapExit` added. Inner step env's `onExit` writes a marker.

```yaml
# Outer env — onWrapExit added
onWrapExit:
  command: "bash"
  args:
    - "-c"
    - >-
      echo "[WRAPPED] inner-onExit via onWrapExit for env={{Env.Wrapped.Name}}"
      >> '{{Session.WorkingDirectory}}/trace.log'
      && {{ repr_sh(Env.Wrapped.Command) }} {{ repr_sh(Env.Wrapped.Args) }}
```

```yaml
# Inner step env adds onExit
stepEnvironments:
  - name: InnerExit
    script:
      actions:
        onEnter:
          command: "bash"
          args: ["-c", "echo 'inner-onEnter body' >> '{{Session.WorkingDirectory}}/trace.log'"]
        onExit:
          command: "bash"
          args: ["-c", "echo 'inner-onExit body' >> '{{Session.WorkingDirectory}}/trace.log'"]
```

**Expected** trailing lines of `trace.log`:

```
[WRAPPED] inner-onExit via onWrapExit for env=InnerExit
inner-onExit body
outer-onExit ran on host
```

**Pass criteria**: Inner `onExit` is prefixed with `[WRAPPED]` and the outer env's own
`onExit` is not.

#### Test 3: `runOnHost: true` bypasses `onWrapEnter`

Outer env defines `onWrapEnter`. Two inner step envs run `onEnter`: one with
`runOnHost: true`, one without. The marker file shows which was wrapped.

```yaml
stepEnvironments:
  - name: HostEnter
    script:
      actions:
        onEnter:
          command: "bash"
          args: ["-c", "echo 'HostEnter ran' >> '{{Session.WorkingDirectory}}/trace.log'"]
          runOnHost: true         # must bypass onWrapEnter
  - name: WrappedEnter
    script:
      actions:
        onEnter:
          command: "bash"
          args: ["-c", "echo 'WrappedEnter ran' >> '{{Session.WorkingDirectory}}/trace.log'"]
                                  # no runOnHost → goes through onWrapEnter
```

**Expected** relevant lines of `trace.log`:

```
HostEnter ran
[WRAPPED] inner-onEnter via onWrapEnter for env=WrappedEnter
WrappedEnter ran
```

**Pass criteria**: The line for `HostEnter` is *not* preceded by a `[WRAPPED]` marker,
and the line for `WrappedEnter` *is*. This proves `runOnHost: true` skips the wrap hook.

#### Test 4: `runOnHost: true` bypasses `onWrapExit` on failure paths

Same shape as Test 3, but verifies that `onWrapExit` is also skipped. This is important
because `onExit` must run even when the wrapped context is broken or torn down.

```yaml
stepEnvironments:
  - name: CleanupOnHost
    script:
      actions:
        onExit:
          command: "bash"
          args:
            - "-c"
            - >-
              echo 'CleanupOnHost: $(date -u +%FT%TZ)'
              >> '{{Session.WorkingDirectory}}/cleanup.log'
          runOnHost: true
```

**Pass criteria**: `cleanup.log` exists and contains the `CleanupOnHost:` line even if
the outer env's `onWrapExit` would have failed (e.g., simulated by pointing its `bash -c`
at a nonexistent command). The host-side `onExit` must be independent of the wrap hook's
health.

#### Test 5: Visible ordering across all three hooks

Combine Tests 1, 2, and 3 in a single session. Include one inner env with `runOnHost: true`
and one without, and one task. Capture the full `trace.log` and verify the ordering below:

```
outer-onEnter ran on host
HostEnter ran
[WRAPPED] inner-onEnter via onWrapEnter for env=WrappedEnter
WrappedEnter ran
[WRAPPED] onRun via onWrapTaskRun
task-onRun body
[WRAPPED] inner-onExit via onWrapExit for env=WrappedEnter
WrappedExit ran
HostExit ran
outer-onExit ran on host
```

**Pass criteria**:
- Every line from a non-`runOnHost` inner action is preceded by a `[WRAPPED]` line.
- Every line from a `runOnHost: true` inner action stands alone with no `[WRAPPED]`
  prefix.
- Enter/exit order is strictly nested: outer-enter → inner-enters → task → inner-exits
  (reverse) → outer-exit.

#### Test 6: `Env.Wrapped.*` namespace in `onWrapEnter`/`onWrapExit`

Outer env's `onWrapEnter` captures each `Env.Wrapped.*` variable to a separate file so
its contents can be asserted. No task needed.

```yaml
onWrapEnter:
  command: "bash"
  args:
    - "-c"
    - >-
      cat > '{{Session.WorkingDirectory}}/wrapped-enter.log' <<EOF
      Name={{Env.Wrapped.Name}}
      Command={{Env.Wrapped.Command}}
      Args={{ repr_sh(Env.Wrapped.Args) }}
      Environment={{ repr_sh(Env.Wrapped.Environment) }}
      Timeout={{Env.Wrapped.Timeout}}
      EOF
      {{ repr_sh(Env.Wrapped.Command) }} {{ repr_sh(Env.Wrapped.Args) }}
```

Given an inner env:

```yaml
- name: VarProbe
  script:
    actions:
      onEnter:
        command: "echo"
        args: ["hello", "from", "inner"]
        timeout: 42
```

**Expected** `wrapped-enter.log` contents:

```
Name=VarProbe
Command=echo
Args=hello from inner
Environment=
Timeout=42
```

**Pass criteria**: Each `Env.Wrapped.*` variable resolves to the inner action's
corresponding field, not to the outer env or the wrap hook itself.

#### Test 7: Referencing `Task.*` inside `onWrapEnter` is an error

Authoring negative test. A template whose `onWrapEnter` references `Task.Command` must
be rejected at template-validation time (or at session-setup time if the template
validator defers expression resolution).

```yaml
onWrapEnter:
  command: "bash"
  args: ["-c", "echo '{{Task.Command}}' > /tmp/out"]   # Task.* not in scope here
```

**Pass criteria**: The implementation emits a clear error identifying the out-of-scope
variable reference and the hook in which it appears. The session does not start.

The symmetric test (referencing `Env.Wrapped.*` inside `onWrapTaskRun`) must also be
rejected.

## Prior Art

### Workflow languages with container abstraction

Several workflow DSLs solve the same portability problem — separating the job logic from
the execution runtime — using different mechanisms:

**[Nextflow](https://nextflow.io/docs/stable/container.html)** is the closest analogue.
Its `container` directive declares the image at the process level, and the execution engine
handles `docker run`, `apptainer exec`, or `podman run` transparently. Switching runtimes
requires changing a single line in `nextflow.config` (`docker.enabled = true` →
`apptainer.enabled = true`), not the workflow code. Nextflow also automatically handles
volume mounts (`-v` / `--bind`) so data files are accessible inside the container.

```groovy
// Nextflow — the process doesn't know which runtime executes it
process render_frame {
    container 'vfx-studio/maya:2025'

    input: val frame
    script: "maya -batch -render scene.ma -s $frame -e $frame"
}
```

```groovy
// nextflow.config — switch runtime without changing the process
docker.enabled = true       // use Docker
// apptainer.enabled = true // or flip to Apptainer — same process code
```

**[CWL (Common Workflow Language)](https://www.commonwl.org/user_guide/en/topics/using-containers.html)**
uses a `DockerRequirement` hint in tool definitions. Runners like `cwltool` support a
`--singularity` flag that transparently maps all Docker requirements to Apptainer calls.
CWL enforces strict input/output staging via a "Path Map" that controls exactly which host
paths are visible inside the container — a stricter version of the same-path bind mount
approach this RFC recommends. The
[CWL v1.2 specification](https://www.commonwl.org/v1.2/CommandLineTool.html#DockerRequirement)
defines the `DockerRequirement` formally.

```yaml
# CWL — container declared as a hint, runner picks the runtime
cwlVersion: v1.2
class: CommandLineTool
hints:
  DockerRequirement:
    dockerPull: vfx-studio/maya:2025
baseCommand: [maya, -batch, -render]
inputs:
  scene:
    type: File
    inputBinding: {position: 1}
outputs:
  rendered:
    type: File
    outputBinding: {glob: "*.exr"}
```

```bash
# Run with Docker (default)
cwltool render.cwl --scene scene.ma
# Run with Apptainer — same CWL file, just a flag
cwltool --singularity render.cwl --scene scene.ma
```

**[Snakemake](https://snakemake.readthedocs.io/en/stable/snakefiles/deployment.html#running-jobs-in-containers)**
supports a per-rule `container:` directive pointing to a Docker URI. When invoked with
`--use-singularity`, Snakemake pulls the Docker image and converts it to Apptainer on the
fly. This per-rule granularity is analogous to OpenJD's per-step environment templates.

```python
# Snakemake — per-rule container, auto-converted to Apptainer if needed
rule render_frame:
    input: "scene.ma"
    output: "frame_{frame}.exr"
    container: "docker://vfx-studio/maya:2025"
    shell: "maya -batch -render {input} -s {wildcards.frame} -e {wildcards.frame}"
```

```bash
# Run with Apptainer (pulls Docker image, converts to SIF automatically)
snakemake --use-singularity --cores 8
```

**[WDL (Workflow Description Language)](https://github.com/openwdl/wdl)** uses a `docker:`
attribute in the `runtime` block. Even though the keyword is `docker`, engines like Cromwell
and miniWDL can intercept and run via Apptainer on HPC clusters without modifying the WDL
file.

```wdl
# WDL — docker keyword, but engine can substitute Apptainer transparently
task render_frame {
  input { File scene; Int frame }
  command { maya -batch -render ~{scene} -s ~{frame} -e ~{frame} }
  runtime {
    docker: "vfx-studio/maya:2025"
    memory: "16 GB"
    cpu: 4
  }
  output { File rendered = glob("*.exr")[0] }
}
```

The key difference between OpenJD's approach and these systems is *where* the abstraction
lives. Nextflow, CWL, and Snakemake use a global config toggle or CLI flag to switch
runtimes. OpenJD uses composable environment templates, which enables per-queue or per-step
runtime selection in a multi-step pipeline — a queue can use Docker while a specific step
uses Apptainer, or one queue uses containers while another uses Conda. None of these prior
systems distinguish between wrapping a per-task command and wrapping inner setup/teardown;
they all wrap the task command only, which is the gap the three-hook model in this RFC
closes.

### Container execution tools in VFX and HPC

**[NVIDIA Enroot](https://github.com/NVIDIA/enroot)** converts Docker images into
unprivileged user namespaces, providing near-native GPU performance without requiring a
Docker daemon. It is used on render farms and HPC clusters where the Docker daemon is
forbidden for security reasons. The `enroot start <image> <command>` pattern is essentially
what the wrap hooks formalize in a template.

```bash
# Enroot — import once, run without a daemon
enroot import docker://vfx-studio/maya:2025
enroot create --name maya maya.sqsh
enroot start --mount /sessions:/sessions maya \
    maya -batch -render scene.ma -s 1 -e 1
```

**Apptainer's `exec` pattern** (`apptainer exec docker://image command args`) demonstrates
that wrapping a command inside a container can be expressed as a simple command prefix.
The wrap hooks generalize this into a template-driven approach with access to the full
lifecycle context (environment variables, timeout, cancelation, enter/exit phases).

```bash
# Apptainer — the command is just prefixed, no daemon needed
apptainer exec --nv --bind /sessions:/sessions \
    docker://vfx-studio/maya:2025 \
    maya -batch -render scene.ma -s 1 -e 1
```

### Pydantic WrapValidator

The naming and concept are inspired by
[Pydantic's WrapValidator](https://docs.pydantic.dev/latest/concepts/validators/#wrap-validators),
which wraps a validation step with before/after logic. The wrap hooks wrap the lifecycle
actions of inner environments and tasks with before/after logic provided by the outer
environment.

### Docker exec pattern in OpenJD

The existing
[bash-in-docker sample](https://github.com/OpenJobDescription/openjd-specifications/tree/mainline/samples/job_templates/bash-in-docker)
demonstrates the pattern of starting a container in a step environment and exec'ing tasks
into it. This RFC generalizes that pattern to work at any environment level (queue, job,
step), makes it transparent to the job template, and extends it to cover inner-environment
setup and teardown.

### Deadline 10 job wrappers

[Deadline 10](https://docs.thinkboxsoftware.com/products/deadline/latest/1_User%20Manual/manual/job-scripts.html)
supports pre/post task scripts that wrap the render command. These are configured at the
repository or job level and can modify the command line, set environment variables, or run
setup/teardown around each task. The wrap hooks provide similar functionality through
the environment template mechanism, plus coverage of inner-environment lifecycle.

### Conda and Rez queue environments

The existing [Conda](https://github.com/aws-deadline/deadline-cloud-samples/tree/mainline/queue_environments/conda)
and [Rez](https://github.com/aws-deadline/deadline-cloud-samples/tree/mainline/queue_environments/rez)
queue environments use `onEnter` to install software and `onExit` to clean up. They modify
the execution context (PATH, environment variables) but don't wrap the actions within a
session. With the wrap hooks, a container environment template follows the same pattern
but goes further: it wraps the entire set of in-session action executions inside a
container.

### The "Universal Wrapper" pattern

The [Task](https://taskfile.dev/usage/) community uses a `WRAPPER` variable pattern where
the same command runs locally or in a container by prepending a runtime-specific prefix:

```
task render FRAME=123                                              # local
task render FRAME=123 WRAPPER="docker run -v $(pwd):/data image"   # Docker
task render FRAME=123 WRAPPER="apptainer exec image.sif"           # Apptainer
```

This is the simplest expression of the same abstraction that the wrap hooks provide.
OpenJD's approach is more powerful because the wrap hooks have access to the full
lifecycle context (environment variables, timeout, cancelation, enter/task/exit
distinction) and compose across multiple environment layers, but the underlying insight
is identical: separate the command from the execution context.

## Rejected Ideas

### A single unified wrap hook (`onWrapAction`) using EXPR conditionals

An alternative design adds a *single* `onWrapAction` hook with a `WrappedAction.Type`
discriminator (`TASK_RUN`, `ENV_ENTER`, `ENV_EXIT`) and a shared `WrappedAction.*`
namespace. Authors use EXPR conditionals to branch on action type. Concretely, the
Docker example in "Basic Examples" would collapse from three embedded scripts to one:

```yaml
specificationVersion: "environment-2023-09"
extensions:
- WRAP_ACTIONS
- EXPR

environment:
  name: Docker
  script:
    actions:
      onEnter: { command: "bash", args: ["{{Env.File.Enter}}"] }
      # One hook intercepts inner onEnter, onRun, and onExit.
      onWrapAction:
        command: "bash"
        args: ["{{Env.File.WrapAction}}"]
        timeout: "{{WrappedAction.Timeout}}"
      onExit: { command: "bash", args: ["{{Env.File.Exit}}"] }

    embeddedFiles:
    - name: WrapAction
      filename: docker-wrap-action.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail

        # Log which phase we're in. EXPR's null-coalescing handles TASK_RUN
        # where WrappedAction.Name is null.
        echo {{ repr_sh(
          "[Docker] " + WrappedAction.Type
          + (" for env '" + WrappedAction.Name + "'" if WrappedAction.Name else "")
        ) }}

        # EXPR-driven per-type behavior: run env setup as root inside the
        # container, but tasks as the default user.
        docker container exec \
            {{ '--user root' if WrappedAction.Type == 'ENV_ENTER' else '' }} \
            $DOCKER_CONTAINER_ID \
            {{ repr_sh(flatten([['-e', e] for e in WrappedAction.Environment])) }} \
            {{ repr_sh(WrappedAction.Command) }} \
            {{ repr_sh(WrappedAction.Args) }}
```

This is strictly more expressive than the three-hook model:

- The wrapper can suppress specific action types (e.g., `true` when
  `WrappedAction.Type == "ENV_EXIT"`) if the outer env manages its own teardown.
- Per-type transformations (e.g., adding `--user root` only for `ENV_ENTER`) live in
  one place instead of being duplicated across three scripts.
- Three nearly-identical `docker exec` blocks collapse to one.

The tradeoffs that tip this RFC toward the three-hook model anyway:

1. **Schema hides intent.** `onWrapAction` tells a template reader that *something* is
   wrapped, but not which phases. With three hooks, the presence or absence of each is
   visible directly in the schema.
2. **EXPR becomes a hard dependency.** Anything beyond "forward unchanged" requires
   EXPR conditionals. The three-hook model lets a template author ship a pure-shell
   wrap script per phase without pulling in the EXPR extension.
3. **Shared `WrappedAction.*` namespace blurs context.** `Task.Command` and
   `Env.Wrapped.Command` mean different things; collapsing them into
   `WrappedAction.Command` forces authors to re-derive the context from
   `WrappedAction.Type` every time.
4. **Debug story is weaker.** A stack trace or log line citing `onWrapAction` forces
   the reader to replay the EXPR branch to know what the wrapper actually did. Three
   named hooks are self-documenting in traces.
5. **Silent scope creep.** A single `onWrapAction` silently intercepts every inner
   lifecycle action, including ones the author may not have considered (e.g., a future
   `onWrapHealthCheck`). Three named hooks limit the blast radius to exactly the
   phases the author opted into.

A future extension could add `onWrapAction` as an additive shorthand for "apply the
same script to all three hooks" without conflicting with the three-hook model, so
picking three hooks now does not close the door on the unified form later.

### Extending `onRun` with a `wrapper` field

An alternative design adds a `wrapper` field to the `<Action>` definition that specifies
a command to prepend to the task command. This is simpler but less flexible: it doesn't
provide access to the task's environment variables, doesn't support wrapping of inner
environments' `onEnter`/`onExit`, and doesn't allow the wrapper to transform the command
and args (only prepend to them).

### Container-specific session action

A `onRunInContainer` action that is specific to container execution was considered. This
would hard-code container semantics into the specification. The general wrap-hook
mechanism is more flexible and supports use cases beyond containers (remote execution,
instrumentation, privilege isolation).

### Implicit container support in the runtime

Making the runtime automatically detect and handle containers (e.g., if a `containerImage`
field is present in the environment template) was considered. This couples the specification
to specific container runtimes and removes the user's ability to customize the container
invocation (mount points, network mode, GPU flags, security options). The explicit
wrap-hook approach keeps the specification general and the user in control.

### Global config toggle for runtime selection

Nextflow's approach — a single `docker.enabled = true` / `apptainer.enabled = true` toggle
in a config file — is elegant for homogeneous pipelines. It was considered as an alternative
to per-environment wrap actions. However, OpenJD jobs often run multi-step pipelines where
different steps need different runtimes (e.g., a Maya render step in a Docker container and
a Nuke composite step in a different container, or one step containerized and another using
Conda). A global toggle cannot express this. Per-environment wrap hooks provide the same
single-toggle simplicity when attached as a queue environment (all jobs on the queue are
wrapped), while also supporting per-step and per-job granularity when needed.

### Nested wrap composition

An earlier iteration allowed multiple environments in the session stack to define wrap
hooks, composing as nested wrappers (outermost wraps innermost). This would enable, for
example, a queue environment providing a container and a step environment adding
profiling inside it. However, nested composition adds significant implementation
complexity (symbol table layering, cancelation propagation across multiple wrap layers)
and makes debugging substantially harder (which layer transformed the command?). This
RFC restricts the session stack to a single wrap layer. The design does not preclude
adding nested wrap composition as a future extension if real use cases emerge.

## Appendix A: Apptainer environment template

Referenced from [Basic Examples › Apptainer environment template](#apptainer-environment-template).

The same pattern as the Docker environment template works with Apptainer. Because
Apptainer is daemonless, each wrap hook invokes `apptainer exec` directly rather than
exec'ing into a running container.

```yaml
specificationVersion: "environment-2023-09"
extensions:
- WRAP_ACTIONS
- EXPR
parameterDefinitions:
- name: ContainerImage
  type: STRING
  description: The container image URI (e.g., docker://ubuntu:latest or a local SIF path).
  default: "docker://ubuntu:latest"

environment:
  name: Apptainer
  script:
    actions:
      onEnter:
        command: "bash"
        args: ["{{Env.File.Enter}}"]
      onWrapEnter:
        command: "bash"
        args: ["{{Env.File.WrapEnter}}"]
      onWrapTaskRun:
        command: "bash"
        args: ["{{Env.File.WrapTaskRun}}"]
      onWrapExit:
        command: "bash"
        args: ["{{Env.File.WrapExit}}"]
      onExit:
        command: "bash"
        args: ["{{Env.File.Exit}}"]
    embeddedFiles:
    - name: Enter
      filename: apptainer-env-enter.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        # Pre-pull and convert to SIF if needed
        apptainer pull --force /tmp/container.sif '{{Param.ContainerImage}}'

    # Each wrap hook uses the same apptainer exec invocation with the
    # appropriate namespace. repr_sh() handles quoting for every value
    # that could contain shell metacharacters.
    - name: WrapEnter
      filename: apptainer-wrap-enter.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        apptainer exec \
            --nv \
            --bind '{{Session.WorkingDirectory}}:{{Session.WorkingDirectory}}' \
            {{ repr_sh(flatten([['--env', e] for e in Env.Wrapped.Environment])) }} \
            /tmp/container.sif \
            {{ repr_sh(Env.Wrapped.Command) }} \
            {{ repr_sh(Env.Wrapped.Args) }}

    - name: WrapTaskRun
      filename: apptainer-wrap-task-run.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        apptainer exec \
            --nv \
            --bind '{{Session.WorkingDirectory}}:{{Session.WorkingDirectory}}' \
            {{ repr_sh(flatten([['--env', e] for e in Task.Environment])) }} \
            /tmp/container.sif \
            {{ repr_sh(Task.Command) }} \
            {{ repr_sh(Task.Args) }}

    - name: WrapExit
      filename: apptainer-wrap-exit.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        apptainer exec \
            --nv \
            --bind '{{Session.WorkingDirectory}}:{{Session.WorkingDirectory}}' \
            {{ repr_sh(flatten([['--env', e] for e in Env.Wrapped.Environment])) }} \
            /tmp/container.sif \
            {{ repr_sh(Env.Wrapped.Command) }} \
            {{ repr_sh(Env.Wrapped.Args) }}

    - name: Exit
      filename: apptainer-env-exit.sh
      type: TEXT
      data: |
        #!/bin/env bash
        set -euo pipefail
        rm -f /tmp/container.sif
```

## Appendix B: Alternatives for `openjd_env` propagation through wrap hooks

Referenced from [Specification › Stdout and `openjd_env` propagation](#stdout-and-openjd_env-propagation).

The chosen model is simple: the scheduler scans the wrap hook's stdout (as it does for
any action), and the wrap script is required to forward the wrapped process's stdout
verbatim. Below are the alternatives considered and the reasons they were not chosen.

### Alternative 1: Scheduler scans the wrapped process's stdout directly

In this model, the scheduler would reach past the wrap hook and scan the grand-child
process's stdout for `openjd_env:` lines. Advantages: authors of wrap scripts could not
accidentally break propagation by dropping or buffering stdout. Disadvantages:

- The scheduler would need to know how to locate the grand-child (different container
  runtimes expose it differently; remote-exec wrappers may not expose it at all).
- The existing contract — "the scheduler reads stdout from the action defined in the
  template" — breaks for wrapped actions only, creating a special case.
- It presumes exactly one grand-child, which is not true for wrap scripts that invoke
  multiple commands (e.g., `docker exec` followed by a cleanup step).

### Alternative 2: Wrap script explicitly re-emits `openjd_env` lines

In this model, the wrap script would be required to grep for `openjd_env:` lines in
the wrapped process's output and re-emit them on its own stdout. Advantages:
schedulers stay on the existing contract. Disadvantages:

- Every wrap script author must implement this correctly, which is easy to miss and
  hard to test. A wrap script that forgets to forward these lines silently breaks
  every inner environment that sets environment variables.
- It duplicates work the scheduler already does when scanning stdout.

### Alternative 3: Dual-scan both streams

In this model, the scheduler would scan both the wrap hook's stdout *and* the wrapped
process's stdout (where locatable). Advantages: defensive. Disadvantages:

- Duplicate lines become possible (the wrap script forwards, then the scheduler also
  scans the grand-child directly), requiring de-duplication logic.
- Inherits the scheduler-knows-the-grand-child problem from Alternative 1.
- Increases implementation complexity for every scheduler.

### Why the chosen model

The chosen model — scheduler scans the wrap hook's stdout; wrap script forwards
stdout verbatim — preserves the existing contract exactly, does not require the
scheduler to know anything about the wrapped process's shape, and is satisfied by
default for every container runtime in scope (`docker container exec`,
`apptainer exec`, `podman exec`, `enroot start`) without any additional code in the
wrap script. The small cost — wrap script authors in exotic scenarios (custom
remote-exec, privilege shifts) must preserve stdout forwarding — is localized to the
small population of authors writing non-container wrap environments, who are already
reasoning about process topology.

## Copyright

This document is placed in the public domain or under the CC0-1.0-Universal license, whichever is more permissive.
