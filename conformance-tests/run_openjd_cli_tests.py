#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml"]
# ///
"""
OpenJD Conformance Test Runner - Example for openjd CLI

Run with: uv run run_openjd_cli_tests.py

Examples:
  uv run run_openjd_cli_tests.py                          # run all tests
  uv run run_openjd_cli_tests.py 2023-09                  # run all 2023-09 tests
  uv run run_openjd_cli_tests.py 2023-09/base             # run base spec tests only
  uv run run_openjd_cli_tests.py 2023-09/TASK_CHUNKING    # run TASK_CHUNKING extension tests
  uv run run_openjd_cli_tests.py 2023-09/*/jobs           # run all job tests
  uv run run_openjd_cli_tests.py '*/*/jobs/*param*'       # pattern match test names
  uv run run_openjd_cli_tests.py 2023-09/base/jobs/1.1--basic-job-creation.test.yaml  # run single test
"""

import argparse
import fnmatch
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def _utf8_stream(stream):
    """Rewrap a stream as UTF-8 (needed for the Windows console).

    Only rewrap streams that expose a raw .buffer and are not already UTF-8,
    so importing this module under pytest (whose capture streams are already
    UTF-8, or have no buffer at all) leaves output capture intact.
    """
    if getattr(stream, "buffer", None) is None:
        return stream
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if encoding == "utf8":
        return stream
    return io.TextIOWrapper(stream.buffer, encoding="utf-8")


# Ensure UTF-8 output on Windows
sys.stdout = _utf8_stream(sys.stdout)
sys.stderr = _utf8_stream(sys.stderr)

OPERATING_SYSTEM = "windows" if sys.platform == "win32" else "posix"

CONFORMANCE_DIR = Path(__file__).parent

# The shared task banner: both the Rust and the Python CLI emit exactly this
# substring once per task run (env enter/exit actions do not emit it).
TASK_BANNER = "--------- Running Task"

# Deliberate non-goals for the assertion features below (expectedError,
# outputSequence, taskCount):
#   - No normative error-code taxonomy (`errorClass:`) — that is a spec
#     change; substring/anyOf is the pragmatic contract until the spec grows
#     error codes.
#   - No regex matching (contains + anyOf cover the enumerated cases; anyOf
#     exists because the Rust and Python CLIs phrase the same rule
#     differently).
#   - No adjacency assertions; ordered subsequence only.
#   - No ordering of validation-error text (multi-error reporting order is
#     non-normative).


def load_yaml_or_json(path: Path):
    with open(path) as f:
        content = f.read()
    if path.suffix == ".json":
        return json.loads(content)
    return yaml.safe_load(content)


def run_check(template_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["openjd", "check", str(template_path)],
        capture_output=True, text=True,
    )
    # Combined (not stderr-or-stdout) so expectedError matching sees
    # everything; the CLIs split diagnostics across the streams differently.
    return result.returncode == 0, result.stderr + result.stdout


# Matches the per-process exit-code line emitted by conforming CLIs. Both the
# Python (`Process pid 1234 exited with code: 42 (unsigned) / 0x2a (hex)`) and
# Rust (`Process exited with code: 42`) CLIs share the `exited with code: <N>`
# substring, so a single regex extracts process exit codes from either.
_EXIT_CODE_RE = re.compile(r"exited with code:\s*(-?\d+)")


def extract_failure_exit_code(output: str) -> int | None:
    """Return the exit code of the failing action in the run output, or None.

    All failures are terminal for a session, so the first non-zero exit code
    is the failing action's code. Later `exited with code:` lines belong to
    teardown actions (onWrapEnvExit, onExit) that run after the failure and
    may exit 0, so matching the last line would mis-attribute the failure.
    """
    for match in _EXIT_CODE_RE.findall(output):
        code = int(match)
        if code != 0:
            return code
    return None


def should_skip_test(test: dict) -> bool:
    """Check if a job test should be skipped on the current platform."""
    run_on = test.get("runOn")
    if run_on is None:
        return False
    return OPERATING_SYSTEM not in run_on


def check_expected_error(spec, output: str) -> str | None:
    """Check an expectedError spec against the combined output of a failed run.

    A plain string is shorthand for {contains: <string>}; `contains` may be a
    string or a list of strings that must ALL appear; `anyOf` is a list of
    strings of which at least ONE must appear. Matching is plain
    case-sensitive substring containment. Returns None when satisfied, else a
    failure message. Malformed specs are failures too, never silent ignores.
    """
    if isinstance(spec, str):
        spec = {"contains": spec}
    if not isinstance(spec, dict):
        return f"Malformed expectedError: must be a string or a mapping, got {type(spec).__name__}"
    unknown = sorted(set(spec) - {"contains", "anyOf"})
    if unknown:
        return f"Malformed expectedError: unknown key(s) {unknown} (allowed: contains, anyOf)"
    if not spec:
        return "Malformed expectedError: need at least one of contains / anyOf"
    if "contains" in spec:
        contains = spec["contains"]
        if isinstance(contains, str):
            contains = [contains]
        if not (isinstance(contains, list) and contains and all(isinstance(s, str) for s in contains)):
            return "Malformed expectedError.contains: must be a string or a non-empty list of strings"
        missing = [s for s in contains if s not in output]
        if missing:
            return (
                "Rejected, but for the wrong reason. Missing expected error "
                f"substring(s): {missing}\n--- Actual output ---\n{output}"
            )
    if "anyOf" in spec:
        any_of = spec["anyOf"]
        if not (isinstance(any_of, list) and any_of and all(isinstance(s, str) for s in any_of)):
            return "Malformed expectedError.anyOf: must be a non-empty list of strings"
        if not any(s in output for s in any_of):
            return (
                "Rejected, but for the wrong reason. None of the expected "
                f"error alternatives matched: {any_of}\n--- Actual output ---\n{output}"
            )
    return None


def extract_comment_directive(template_path: Path):
    """Extract an `expectedError` directive from a template's leading comments.

    Template fixtures are passed verbatim to `openjd check`, so a foreign
    top-level key would itself change validation; the directive instead lives
    in the contiguous `#` comment block at the top of the file, keeping the
    checked file byte-identical for every consumer.

    Returns (spec, None) when a directive is present, (None, None) when
    absent, or (None, error_message) when a directive is malformed.
    """
    # errors="replace": a non-UTF-8 template must never crash the runner (the
    # bytes are the CLI's to judge); we only scan for an ASCII directive.
    with open(template_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    block = []
    for line in lines:
        if not line.startswith("#"):
            break
        # De-comment: strip the leading '#' and one following space if present.
        line = line[1:]
        if line.startswith(" "):
            line = line[1:]
        block.append(line)

    # A directive in a comment PAST the leading block would otherwise be
    # silently ignored, quietly reverting the fixture to single-bit — the
    # exact failure mode this feature exists to kill. Hard error instead.
    # (A non-comment YAML key `expectedError:` in the template body is the
    # CLI's problem, not ours.)
    for line in lines[len(block):]:
        if not line.startswith("#"):
            continue
        content = line[1:]
        if content.startswith(" "):
            content = content[1:]
        if content.startswith("expectedError:"):
            return None, (
                "expectedError directive found outside the leading comment "
                "block; move it into the contiguous '#' block at the top of "
                "the file"
            )

    start = next((i for i, line in enumerate(block) if line.startswith("expectedError:")), None)
    if start is None:
        return None, None

    try:
        parsed = yaml.safe_load("\n".join(block[start:]))
    except yaml.YAMLError as exc:
        return None, f"Malformed expectedError comment directive: YAML parse error: {exc}"
    if not isinstance(parsed, dict) or set(parsed) != {"expectedError"}:
        return None, (
            "Malformed expectedError comment directive: must parse to a "
            "mapping with the single key 'expectedError'"
        )
    return parsed["expectedError"], None


def check_output_sequences(chains_value, output: str) -> str | None:
    """Check ordered-subsequence chains against the combined output.

    A flat list[str] is one chain; a list[list[str]] is independent chains
    (e.g. diamond dependencies where two branches are mutually unordered).
    Entries need not be adjacent; each entry matches at its first occurrence
    after the END of the previous entry's match (log markers do not overlap;
    greedy earliest-match is complete for ordered substring chains).
    """
    if not isinstance(chains_value, list) or not chains_value:
        return "Malformed outputSequence: must be a non-empty list"
    if all(isinstance(entry, list) for entry in chains_value):
        chains = chains_value
    elif all(isinstance(entry, str) for entry in chains_value):
        chains = [chains_value]
    else:
        return (
            "Malformed outputSequence: entries must be all strings (one chain) "
            "or all lists (independent chains), not a mix"
        )
    for chain in chains:
        if not all(isinstance(entry, str) for entry in chain):
            return f"Malformed outputSequence: chain entries must be strings: {chain}"
        if len(chain) < 2:
            return (
                "Malformed outputSequence: a chain needs at least 2 entries (a "
                f"1-entry chain is a membership check — use expected.output): {chain}"
            )
    for chain in chains:
        pos = 0
        prev = None
        for entry in chain:
            found = output.find(entry, pos)
            if found == -1:
                where = f"after {prev!r}" if prev is not None else "in the output"
                return (
                    f"outputSequence violated: {entry!r} not found {where}.\n"
                    f"--- Actual output ---\n{output}"
                )
            pos = found + len(entry)
            prev = entry
    return None


def count_tasks(output: str) -> int:
    """Count task runs via the shared task banner."""
    return output.count(TASK_BANNER)


def check_task_count(expected_count, output: str) -> str | None:
    """Require exactly expected_count task banners in the combined output."""
    # bool is an int subclass; `taskCount: true` must not pass as 1.
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        return f"Malformed taskCount: must be a non-negative integer, got {expected_count!r}"
    actual = count_tasks(output)
    if actual != expected_count:
        return (
            f"Expected taskCount {expected_count} but counted {actual} "
            f"'{TASK_BANNER}' banner(s).\n--- Actual output ---\n{output}"
        )
    return None


def check_task_failure(task_failure, returncode: int, output: str) -> str | None:
    """taskFailure: assert the run surfaced a task failure, and (optionally)
    that a specific process exit code propagated. This is how exit-status
    propagation is verified for a non-.invalid. test that must still make
    assertions about its output.
    """
    if task_failure is None:
        return None
    if returncode == 0:
        return (
            "Expected task failure (non-zero run exit) but the run "
            f"succeeded (exit 0).\n--- Actual output ---\n{output}"
        )
    expected_code = task_failure.get("exitCode")
    if expected_code is None:
        return None
    actual_code = extract_failure_exit_code(output)
    if actual_code is None:
        return (
            "Expected task exitCode "
            f"{expected_code} but no non-zero 'exited with code' "
            f"line was found in the output.\n"
            f"--- Actual output ---\n{output}"
        )
    if actual_code != expected_code:
        return (
            f"Expected task exitCode {expected_code} but the "
            f"failing action exited with {actual_code}.\n"
            f"--- Actual output ---\n{output}"
        )
    return None


def check_output_membership(expected: dict, output: str) -> str | None:
    """The `output` / `forbidden` membership assertions (with anyOf support)."""
    expected_output = expected.get("output", []) + expected.get(f"output_{OPERATING_SYSTEM}", [])
    forbidden = expected.get("forbidden", []) + expected.get(f"forbidden_{OPERATING_SYSTEM}", [])
    for line in expected_output:
        # Support anyOf: {"anyOf": ["value1", "value2"]} — at least one must appear
        if isinstance(line, dict) and "anyOf" in line:
            if not any(alt in output for alt in line["anyOf"]):
                return f"Missing expected output (none of anyOf matched): {line['anyOf']}\n--- Actual output ---\n{output}"
        elif line not in output:
            return f"Missing expected output: {line}\n--- Actual output ---\n{output}"
    for line in forbidden:
        if line in output:
            return f"Found forbidden output: {line}\n--- Actual output ---\n{output}"
    return None


def check_job_expectations(expected: dict, returncode: int, output: str) -> str | None:
    """All assertions for a non-.invalid job fixture; None when they hold."""
    error = check_task_failure(expected.get("taskFailure"), returncode, output)
    if error is not None:
        return error
    error = check_output_membership(expected, output)
    if error is not None:
        return error
    # Ordered chains are checked in addition to (after) output / forbidden /
    # taskFailure. Platform variants are each checked as their OWN chain set,
    # never concatenated with the base list: concatenation would invent an
    # ordering between the two blocks that the author never asserted.
    for key in ("outputSequence", f"outputSequence_{OPERATING_SYSTEM}"):
        if key in expected:
            error = check_output_sequences(expected[key], output)
            if error is not None:
                return error
    if "taskCount" in expected:
        return check_task_count(expected["taskCount"], output)
    return None


def lint_job_fixture(test: dict, fixture_name: str, expect_failure: bool) -> str | None:
    """Fixture-form checks that do not need the CLI to run."""
    if not expect_failure and "expectedError" in test:
        return "expectedError is only valid in .invalid fixtures"
    if expect_failure and "expected" in test:
        expected = test["expected"] or {}
        if any(key == "outputSequence" or key.startswith("outputSequence_") for key in expected):
            return (
                "outputSequence is not allowed in .invalid fixtures; write "
                "failure-ordering cases as .test.yaml with expected.taskFailure"
            )
        # Historical quirk (three mainline fixtures documenting a descending-
        # range spec defect): an `expected:` block in an .invalid fixture has
        # never been enforced. Keep discarding it, but say so out loud so the
        # block can never again be mistaken for an assertion.
        print(f"  note: 'expected:' in an .invalid fixture is not enforced ({fixture_name})", flush=True)
    return None


def run_job_cli(test: dict) -> tuple[int, str]:
    """Materialize the fixture's inputs and run `openjd run` on them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Write template
        template_path = tmpdir / "template.yaml"
        with open(template_path, "w") as f:
            yaml.dump(test["template"], f)

        cmd = ["openjd", "run", str(template_path)]

        # Parameters
        if "parameters" in test:
            params_path = tmpdir / "parameters.yaml"
            with open(params_path, "w") as f:
                yaml.dump(test["parameters"], f)
            cmd.extend(["-p", f"file://{params_path}"])

        # Environment templates
        for i, env in enumerate(test.get("environments", [])):
            env_path = tmpdir / f"env{i}.yaml"
            with open(env_path, "w") as f:
                yaml.dump(env, f)
            cmd.extend(["--env", str(env_path)])

        # Path mapping
        if "pathMapping" in test:
            pm_path = tmpdir / "pathmapping.json"
            with open(pm_path, "w") as f:
                json.dump({"version": "pathmapping-1.0", "path_mapping_rules": test["pathMapping"]}, f)
            cmd.extend(["--path-mapping-rules", f"file://{pm_path}"])

        result = subprocess.run(cmd, capture_output=True, text=True)

    return result.returncode, result.stdout + result.stderr


def run_job(test_path: Path) -> tuple[bool, str] | None:
    """Run one job fixture.

    Returns None when skipped on this platform, else (ok, detail) where ok is
    the final test verdict and detail is the raw output on pass or an
    explanatory message on failure.
    """
    test = load_yaml_or_json(test_path)

    if should_skip_test(test):
        return None

    expect_failure = ".invalid." in test_path.name

    error = lint_job_fixture(test, test_path.name, expect_failure)
    if error is not None:
        return False, error

    returncode, output = run_job_cli(test)

    if expect_failure:
        # Any failure (non-zero exit code) is acceptable; expectedError
        # additionally pins WHY the fixture was rejected.
        if returncode == 0:
            return False, f"Expected failure, got success\n--- Actual output ---\n{output}"
        if "expectedError" in test:
            error = check_expected_error(test["expectedError"], output)
            if error is not None:
                return False, error
        return True, output

    # `or {}`: an explicit `expected:` null block must not crash the runner.
    error = check_job_expectations(test.get("expected") or {}, returncode, output)
    if error is not None:
        return False, error
    return True, output


def run_template_test(template_path: Path) -> tuple[bool, str]:
    """Run one template fixture through `openjd check`.

    Returns (ok, detail): the raw output on pass, an explanatory message on
    failure. Applies any expectedError comment directive (see
    extract_comment_directive).
    """
    expect_failure = ".invalid." in template_path.name

    directive, error = extract_comment_directive(template_path)
    if error is not None:
        return False, error
    if directive is not None and not expect_failure:
        return False, "expectedError comment directive is only valid in .invalid templates"

    success, output = run_check(template_path)
    if success == expect_failure:
        return False, (
            f"Expected {'failure' if expect_failure else 'success'}, "
            f"got {'success' if success else 'failure'}\n{output}"
        )
    if directive is not None:
        error = check_expected_error(directive, output)
        if error is not None:
            return False, error
    return True, output


def run_template_tests(directory: Path, pattern: str = None) -> tuple[int, int, int, list[str]]:
    passed = failed = 0
    failed_tests = []

    templates = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.json")))
    for template in templates:
        if pattern and not fnmatch.fnmatch(template.name, pattern):
            continue
        ok, detail = run_template_test(template)

        if ok:
            passed += 1
            print(f"  ✓ {template.name}", flush=True)
        else:
            failed += 1
            failed_tests.append(str(template.relative_to(CONFORMANCE_DIR)))
            print(f"  ✗ {template.name}", flush=True)
            print(f"    {detail[:300]}")

    # Template fixtures cannot be skipped (no runOn key), so skipped is 0.
    return passed, failed, 0, failed_tests


def run_job_tests(directory: Path, pattern: str = None) -> tuple[int, int, int, list[str]]:
    passed = failed = skipped = 0
    failed_tests = []

    for test_path in sorted(directory.glob("*.test.yaml")):
        name = test_path.name.replace(".invalid.test.yaml", "").replace(".test.yaml", "")
        if pattern and not fnmatch.fnmatch(test_path.name, pattern):
            continue
        result = run_job(test_path)
        if result is None:
            skipped += 1
            print(f"  ⊘ {name} (skipped, not for {OPERATING_SYSTEM})", flush=True)
            continue
        ok, detail = result

        if ok:
            passed += 1
            print(f"  ✓ {name}", flush=True)
        else:
            failed += 1
            failed_tests.append(str(test_path.relative_to(CONFORMANCE_DIR)))
            print(f"  ✗ {name}", flush=True)
            print(f"    {detail[:300]}")

    return passed, failed, skipped, failed_tests


def run_single_file(file_path: Path) -> tuple[int, int, int, list[str]]:
    """Run a single test file directly."""
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 0, 1, 0, [str(file_path)]

    name = file_path.name
    if ".test.yaml" in name:
        result = run_job(file_path)
        if result is None:
            print(f"  ⊘ {name} (skipped, not for {OPERATING_SYSTEM})")
            return 0, 0, 1, []
        ok, detail = result
        display_name = name.replace(".invalid.test.yaml", "").replace(".test.yaml", "")
    else:
        ok, detail = run_template_test(file_path)
        display_name = name

    if ok:
        print(f"  ✓ {display_name}")
    else:
        print(f"  ✗ {display_name}")

    print(f"\n--- Output ---\n{detail}")
    return (1, 0, 0, []) if ok else (0, 1, 0, [str(file_path)])


def discover_test_dirs(base: Path) -> list[tuple[Path, str]]:
    """Discover all test directories: (path, type) where type is job_templates|env_templates|jobs"""
    results = []
    for spec_version in sorted(base.iterdir()):
        if not spec_version.is_dir() or not spec_version.name[0].isdigit():
            continue
        for component in sorted(spec_version.iterdir()):
            if not component.is_dir():
                continue
            for test_type in ["job_templates", "env_templates", "jobs"]:
                test_dir = component / test_type
                if test_dir.is_dir():
                    results.append((test_dir, test_type))
    return results


def compute_exit_code(passed: int, failed: int, skipped: int, fail_on_skip_ratio: float | None) -> int:
    """Exit 1 on failures (unchanged semantics), or when the optional
    skip-ratio guard trips. With zero tests total the guard does not trip."""
    total = passed + failed + skipped
    if fail_on_skip_ratio is not None and total > 0:
        ratio = skipped / total
        if ratio > fail_on_skip_ratio:
            print(f"Skip ratio {ratio:.2f} exceeds --fail-on-skip-ratio {fail_on_skip_ratio}")
            return 1
    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Run OpenJD conformance tests")
    parser.add_argument("pattern", nargs="?", default="*/*/*",
                        help="Pattern: {version}/{component}/{type}/{test} or file path")
    parser.add_argument("--fail-on-skip-ratio", type=float, default=None, metavar="X",
                        help="Exit 1 if skipped/(passed+failed+skipped) > X (X in [0,1])")
    args = parser.parse_args()
    if args.fail_on_skip_ratio is not None and not 0.0 <= args.fail_on_skip_ratio <= 1.0:
        parser.error("--fail-on-skip-ratio must be in [0, 1]")

    # Check if it's a direct file path
    potential_path = CONFORMANCE_DIR / args.pattern
    if potential_path.is_file():
        passed, failed, skipped, failed_tests = run_single_file(potential_path)
        print(f"\nTotal: {passed} passed, {failed} failed, {skipped} skipped")
        sys.exit(compute_exit_code(passed, failed, skipped, args.fail_on_skip_ratio))

    # Parse pattern: version/component/type/test_pattern
    parts = args.pattern.strip("/").split("/")
    version_pat = parts[0] if len(parts) > 0 else "*"
    component_pat = parts[1] if len(parts) > 1 else "*"
    type_pat = parts[2] if len(parts) > 2 else "*"
    test_pat = parts[3] if len(parts) > 3 else None

    total_passed = total_failed = total_skipped = 0
    all_failed_tests = []

    for test_dir, test_type in discover_test_dirs(CONFORMANCE_DIR):
        rel_path = test_dir.relative_to(CONFORMANCE_DIR)
        version, component, _ = rel_path.parts

        if not fnmatch.fnmatch(version, version_pat):
            continue
        if not fnmatch.fnmatch(component, component_pat):
            continue
        if not fnmatch.fnmatch(test_type, type_pat):
            continue

        print(f"\n{rel_path}:", flush=True)

        if test_type == "jobs":
            passed, failed, skipped, failed_tests = run_job_tests(test_dir, test_pat)
        else:
            passed, failed, skipped, failed_tests = run_template_tests(test_dir, test_pat)

        total_passed += passed
        total_failed += failed
        total_skipped += skipped
        all_failed_tests.extend(failed_tests)
        summary = f"  {passed} passed, {failed} failed"
        if skipped:
            summary += f", {skipped} skipped"
        print(summary)

    print(f"\nTotal: {total_passed} passed, {total_failed} failed, {total_skipped} skipped")

    if all_failed_tests:
        print(f"\nFailed tests ({len(all_failed_tests)}):")
        for name in all_failed_tests:
            print(f"  - {name}")

    sys.exit(compute_exit_code(total_passed, total_failed, total_skipped, args.fail_on_skip_ratio))


if __name__ == "__main__":
    main()
