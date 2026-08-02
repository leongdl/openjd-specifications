"""Hermetic self-tests for run_openjd_cli_tests.py.

Runs the conformance runner in-process against a FAKE `openjd` executable
(written to a temp dir and prepended to PATH), so no real CLI is needed.
Each test cites the numbered case from the harness design doc's self-test
section ("Case N").

Run with: uv run --with pytest,pyyaml python -m pytest test_run_openjd_cli_tests.py -v
"""

import io
import os
import sys
from pathlib import Path

import pytest
import yaml

# No package here; import the runner module from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_openjd_cli_tests as runner  # noqa: E402

THIS_OS = runner.OPERATING_SYSTEM
OTHER_OS = "windows" if THIS_OS == "posix" else "posix"

# The fake CLI, scripted entirely by the fixture under test:
#   check <file>: prints any `# FAKE-CHECK-STDOUT: <msg>` line to stdout; if a
#     `# FAKE-CHECK-ERROR: <msg>` line is present prints <msg> to stderr and
#     exits 1, else exits 0.
#   run <template>: walks steps[*].script.actions.onRun.args and interprets
#     OUT:<text> (stdout), ERR:<text> (stderr), TASKBANNER (the shared task
#     banner), EXITCODE:<n> (prints `exited with code: <n>`, process exits n).
FAKE_OPENJD_PY = '''\
"""Fake `openjd` CLI for hermetic runner self-tests."""
import sys

import yaml


def main():
    if len(sys.argv) < 3:
        sys.exit(2)
    command, path = sys.argv[1], sys.argv[2]
    if command == "check":
        with open(path, encoding="utf-8") as f:
            text = f.read()
        code = 0
        for line in text.splitlines():
            if line.startswith("# FAKE-CHECK-STDOUT: "):
                print(line[len("# FAKE-CHECK-STDOUT: "):])
            if line.startswith("# FAKE-CHECK-ERROR: "):
                print(line[len("# FAKE-CHECK-ERROR: "):], file=sys.stderr)
                code = 1
        sys.exit(code)
    if command == "run":
        with open(path, encoding="utf-8") as f:
            template = yaml.safe_load(f)
        exit_code = 0
        for step in template.get("steps", []):
            for arg in step["script"]["actions"]["onRun"]["args"]:
                if arg.startswith("OUT:"):
                    print(arg[len("OUT:"):])
                elif arg.startswith("ERR:"):
                    print(arg[len("ERR:"):], file=sys.stderr)
                elif arg == "TASKBANNER":
                    print("--------- Running Task")
                elif arg.startswith("EXITCODE:"):
                    exit_code = int(arg[len("EXITCODE:"):])
                    print(f"exited with code: {exit_code}")
        sys.exit(exit_code)
    sys.exit(2)


main()
'''


@pytest.fixture(scope="session")
def fake_cli_dir(tmp_path_factory):
    """Write the fake `openjd` executable (POSIX sh wrapper around a python
    script run by this test session's interpreter, which has pyyaml)."""
    directory = tmp_path_factory.mktemp("fake-openjd")
    fake_py = directory / "fake_openjd.py"
    fake_py.write_text(FAKE_OPENJD_PY, encoding="utf-8")
    wrapper = directory / "openjd"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return directory


@pytest.fixture
def fake_cli(fake_cli_dir, monkeypatch):
    monkeypatch.setenv("PATH", str(fake_cli_dir) + os.pathsep + os.environ["PATH"])
    return fake_cli_dir


def job_doc(args_lists, **extra):
    """A job fixture document whose steps' onRun args script the fake CLI."""
    doc = {
        "template": {
            "specificationVersion": "jobtemplate-2023-09",
            "name": "FakeJob",
            "steps": [
                {"name": f"Step{i}", "script": {"actions": {"onRun": {"command": "noop", "args": list(args)}}}}
                for i, args in enumerate(args_lists)
            ],
        },
    }
    doc.update(extra)
    return doc


def write_job(directory: Path, name: str, doc: dict) -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def run_job_fixture(tmp_path: Path, name: str, doc: dict):
    return runner.run_job(write_job(tmp_path, name, doc))


# ---------------------------------------------------------------------------
# Feature 1: expectedError (job fixtures)
# ---------------------------------------------------------------------------

FAILING_ARGS = [["ERR:bad flavor detected", "EXITCODE:1"]]


def test_expected_error_contains_satisfied_passes(fake_cli, tmp_path):
    # Case 1: expectedError.contains satisfied -> pass.
    doc = job_doc(FAILING_ARGS, expectedError={"contains": "bad flavor"})
    ok, _ = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert ok


def test_expected_error_contains_unsatisfied_fails(fake_cli, tmp_path):
    # Case 1: expectedError.contains unsatisfied -> fail with the missing text.
    doc = job_doc(FAILING_ARGS, expectedError={"contains": "unrelated text"})
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "wrong reason" in detail
    assert "unrelated text" in detail
    assert "bad flavor detected" in detail  # actual output is included


def test_expected_error_anyof_second_alternative_passes(fake_cli, tmp_path):
    # Case 2: second anyOf alternative matches -> pass.
    doc = job_doc(FAILING_ARGS, expectedError={"anyOf": ["not present", "bad flavor"]})
    ok, _ = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert ok


def test_expected_error_anyof_none_match_fails(fake_cli, tmp_path):
    # Case 2: no anyOf alternative matches -> fail.
    doc = job_doc(FAILING_ARGS, expectedError={"anyOf": ["not present", "also missing"]})
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "None of the expected error alternatives matched" in detail


def test_expected_error_contains_list_all_present_passes(fake_cli, tmp_path):
    # Case 3: contains as list, ALL present -> pass (conjunctive).
    doc = job_doc(FAILING_ARGS, expectedError={"contains": ["bad flavor", "exited with code: 1"]})
    ok, _ = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert ok


def test_expected_error_contains_list_one_missing_fails(fake_cli, tmp_path):
    # Case 3: contains as list, one missing -> fail, naming only the missing one.
    doc = job_doc(FAILING_ARGS, expectedError={"contains": ["bad flavor", "zzz-not-there"]})
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "zzz-not-there" in detail
    assert "['zzz-not-there']" in detail  # 'bad flavor' matched, so not listed missing


def test_expected_error_string_shorthand_passes(fake_cli, tmp_path):
    # Case 4: string shorthand == {contains: string}.
    doc = job_doc(FAILING_ARGS, expectedError="bad flavor")
    ok, _ = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert ok


def test_expected_error_string_shorthand_unsatisfied_fails(fake_cli, tmp_path):
    # Case 4: shorthand still enforces.
    doc = job_doc(FAILING_ARGS, expectedError="not in the output")
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "not in the output" in detail


def test_expected_error_with_successful_run_fails(fake_cli, tmp_path):
    # Case 5: .invalid fixture whose run exits 0 fails regardless of expectedError.
    doc = job_doc([["OUT:looks fine"]], expectedError="bad flavor")
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "Expected failure, got success" in detail


def test_expected_error_on_non_invalid_fixture_is_hard_failure(fake_cli, tmp_path):
    # Case 6: expectedError is only valid in .invalid fixtures.
    doc = job_doc([["OUT:hello"]], expectedError="anything")
    ok, detail = run_job_fixture(tmp_path, "a.test.yaml", doc)
    assert not ok
    assert "expectedError is only valid in .invalid fixtures" in detail


@pytest.mark.parametrize(
    "spec",
    [
        {"wrongKey": "x"},
        {},
        {"contains": 42},
        {"contains": []},
        {"contains": [1, 2]},
        {"anyOf": "must be a list"},
        {"anyOf": []},
        {"anyOf": [1]},
        42,
    ],
)
def test_malformed_expected_error_is_hard_failure(fake_cli, tmp_path, spec):
    # Case 7: malformed expectedError (unknown key; empty; wrong types).
    doc = job_doc(FAILING_ARGS, expectedError=spec)
    ok, detail = run_job_fixture(tmp_path, "a.invalid.test.yaml", doc)
    assert not ok
    assert "Malformed expectedError" in detail


# ---------------------------------------------------------------------------
# Feature 1: expectedError (template comment directive)
# ---------------------------------------------------------------------------

RESERVED_SCOPE_TEMPLATE = """\
# FAKE-CHECK-ERROR: capability 'attr.worker.cap' uses reserved scope 'worker'.
# A prose line, ignored by extraction.
# expectedError:
#   anyOf:
#   - "uses reserved scope 'worker'"
#   - "Only Open Job Description defined capabilities"
specificationVersion: jobtemplate-2023-09
name: TestJob
"""


def test_template_directive_extracted_and_satisfied(fake_cli, tmp_path):
    # Case 8: comment directive extracted and enforced (pass direction).
    path = tmp_path / "t.invalid.yaml"
    path.write_text(RESERVED_SCOPE_TEMPLATE, encoding="utf-8")
    ok, _ = runner.run_template_test(path)
    assert ok


def test_template_directive_wrong_reason_fails(fake_cli, tmp_path):
    # Case 8: rejected, but not for the pinned reason -> fail.
    path = tmp_path / "t.invalid.yaml"
    path.write_text(
        RESERVED_SCOPE_TEMPLATE.replace(
            "uses reserved scope 'worker'.", "some entirely different diagnostic."
        ),
        encoding="utf-8",
    )
    ok, detail = runner.run_template_test(path)
    assert not ok
    assert "wrong reason" in detail


def test_template_directive_in_positive_template_is_hard_failure(fake_cli, tmp_path):
    # Case 9: a directive in a non-.invalid template is a hard failure.
    path = tmp_path / "t.yaml"
    path.write_text(
        "# expectedError: \"anything\"\nspecificationVersion: jobtemplate-2023-09\n",
        encoding="utf-8",
    )
    ok, detail = runner.run_template_test(path)
    assert not ok
    assert "only valid in .invalid templates" in detail


@pytest.mark.parametrize(
    "directive_block",
    [
        "# expectedError:\n#   anyOf: [\n",  # YAML parse error
        "# expectedError: \"x\"\n# extraKey: \"y\"\n",  # extra top-level key
    ],
)
def test_template_directive_malformed_is_hard_failure(fake_cli, tmp_path, directive_block):
    path = tmp_path / "t.invalid.yaml"
    path.write_text(
        f"# FAKE-CHECK-ERROR: nope\n{directive_block}specificationVersion: jobtemplate-2023-09\n",
        encoding="utf-8",
    )
    ok, detail = runner.run_template_test(path)
    assert not ok
    assert "Malformed expectedError comment directive" in detail


def test_template_invalid_without_directive_single_bit(fake_cli, tmp_path):
    # Case 10: no directive -> unchanged single-bit behavior, both directions.
    rejected = tmp_path / "rejected.invalid.yaml"
    rejected.write_text("# FAKE-CHECK-ERROR: any reason at all\nname: X\n", encoding="utf-8")
    ok, _ = runner.run_template_test(rejected)
    assert ok

    accepted = tmp_path / "accepted.invalid.yaml"
    accepted.write_text("# just a comment\nname: X\n", encoding="utf-8")
    ok, detail = runner.run_template_test(accepted)
    assert not ok
    assert "Expected failure, got success" in detail


def test_legacy_expected_block_in_invalid_fixture_warns_but_passes(fake_cli, tmp_path, capsys):
    # Case 11: legacy `expected:` in an .invalid job fixture is discarded (the
    # unsatisfiable output assertion proves it is NOT enforced) with a warning.
    doc = job_doc(FAILING_ARGS, expected={"output": ["never printed anywhere"]})
    ok, _ = run_job_fixture(tmp_path, "legacy.invalid.test.yaml", doc)
    assert ok
    out = capsys.readouterr().out
    assert "note: 'expected:' in an .invalid fixture is not enforced" in out
    assert "legacy.invalid.test.yaml" in out


# ---------------------------------------------------------------------------
# Feature 2: expected.outputSequence
# ---------------------------------------------------------------------------

ORDERED_ARGS = [["OUT:ALPHA", "OUT:BETA", "OUT:GAMMA"]]


def test_output_sequence_in_order_passes(fake_cli, tmp_path):
    # Case 12: subsequence holds (entries need not be adjacent).
    doc = job_doc(ORDERED_ARGS, expected={
        "output": ["ALPHA", "BETA", "GAMMA"],
        "outputSequence": ["ALPHA", "GAMMA"],
    })
    ok, _ = run_job_fixture(tmp_path, "seq.test.yaml", doc)
    assert ok


def test_output_sequence_order_swapped_fails(fake_cli, tmp_path):
    # Case 12: membership passes but order is wrong -> FAIL (the core new assertion).
    doc = job_doc(ORDERED_ARGS, expected={
        "output": ["ALPHA", "BETA", "GAMMA"],
        "outputSequence": ["BETA", "ALPHA"],
    })
    ok, detail = run_job_fixture(tmp_path, "seq.test.yaml", doc)
    assert not ok
    assert "outputSequence violated" in detail
    assert "'ALPHA' not found after 'BETA'" in detail
    assert "GAMMA" in detail  # full output included


def test_output_sequence_composes_with_task_failure_passes(fake_cli, tmp_path):
    # Case 13: failing run + ordered cleanup markers -> pass.
    doc = job_doc([["OUT:CLEAN-INNER", "OUT:CLEAN-OUTER", "EXITCODE:7"]], expected={
        "taskFailure": {"exitCode": 7},
        "outputSequence": ["CLEAN-INNER", "CLEAN-OUTER"],
    })
    ok, _ = run_job_fixture(tmp_path, "cleanup.test.yaml", doc)
    assert ok


def test_output_sequence_composes_with_task_failure_order_fails(fake_cli, tmp_path):
    # Case 13: run failed as expected, but order violated -> still a failure.
    doc = job_doc([["OUT:CLEAN-INNER", "OUT:CLEAN-OUTER", "EXITCODE:7"]], expected={
        "taskFailure": {},
        "outputSequence": ["CLEAN-OUTER", "CLEAN-INNER"],
    })
    ok, detail = run_job_fixture(tmp_path, "cleanup.test.yaml", doc)
    assert not ok
    assert "outputSequence violated" in detail


DIAMOND_CHAINS = [["ROOT", "A", "LEAF"], ["ROOT", "B", "LEAF"]]


@pytest.mark.parametrize("order", [["OUT:ROOT", "OUT:A", "OUT:B", "OUT:LEAF"],
                                   ["OUT:ROOT", "OUT:B", "OUT:A", "OUT:LEAF"]])
def test_output_sequence_multiple_chains_diamond_passes(fake_cli, tmp_path, order):
    # Case 14: A and B mutually unordered; both chains hold either way.
    doc = job_doc([order], expected={"outputSequence": DIAMOND_CHAINS})
    ok, _ = run_job_fixture(tmp_path, "diamond.test.yaml", doc)
    assert ok


def test_output_sequence_multiple_chains_violation_fails(fake_cli, tmp_path):
    # Case 14: one violated chain fails the test.
    doc = job_doc([["OUT:ROOT", "OUT:A", "OUT:B", "OUT:LEAF"]], expected={
        "outputSequence": [["ROOT", "A", "LEAF"], ["LEAF", "B"]],
    })
    ok, detail = run_job_fixture(tmp_path, "diamond.test.yaml", doc)
    assert not ok
    assert "'B' not found after 'LEAF'" in detail


def test_output_sequence_platform_variant_checked_other_ignored(fake_cli, tmp_path):
    # Case 15: this platform's list is enforced; the other platform's list
    # (which would fail here) is ignored entirely.
    doc = job_doc(ORDERED_ARGS, expected={
        f"outputSequence_{THIS_OS}": ["ALPHA", "BETA"],
        f"outputSequence_{OTHER_OS}": ["BETA", "ALPHA"],
    })
    ok, _ = run_job_fixture(tmp_path, "plat.test.yaml", doc)
    assert ok


def test_output_sequence_platform_variant_violation_fails(fake_cli, tmp_path):
    # Case 15: the current platform's list is its own chain and is enforced.
    doc = job_doc(ORDERED_ARGS, expected={f"outputSequence_{THIS_OS}": ["GAMMA", "ALPHA"]})
    ok, detail = run_job_fixture(tmp_path, "plat.test.yaml", doc)
    assert not ok
    assert "outputSequence violated" in detail


@pytest.mark.parametrize(
    "chains, fragment",
    [
        (["ALPHA"], "at least 2 entries"),
        ([["ALPHA", "BETA"], ["GAMMA"]], "at least 2 entries"),
        (["ALPHA", ["BETA", "GAMMA"]], "not a mix"),
        ([["ALPHA", 3]], "entries must be strings"),
        ([], "non-empty list"),
        ("ALPHA", "non-empty list"),
    ],
)
def test_output_sequence_lint_failures(fake_cli, tmp_path, chains, fragment):
    # Case 16: chain lint rules are hard failures.
    doc = job_doc(ORDERED_ARGS, expected={"outputSequence": chains})
    ok, detail = run_job_fixture(tmp_path, "lint.test.yaml", doc)
    assert not ok
    assert "Malformed outputSequence" in detail
    assert fragment in detail


@pytest.mark.parametrize("key", ["outputSequence", f"outputSequence_{THIS_OS}"])
def test_output_sequence_in_invalid_fixture_is_hard_failure(fake_cli, tmp_path, key):
    # Case 16: outputSequence in an .invalid.test.yaml fixture is a hard
    # failure (write failure-ordering cases as .test.yaml + taskFailure).
    doc = job_doc(FAILING_ARGS, expected={key: ["ALPHA", "BETA"]})
    ok, detail = run_job_fixture(tmp_path, "bad.invalid.test.yaml", doc)
    assert not ok
    assert "outputSequence is not allowed in .invalid fixtures" in detail


# ---------------------------------------------------------------------------
# Feature 3: expected.taskCount
# ---------------------------------------------------------------------------

# Two real task banners plus look-alikes that must NOT count: an env banner
# and a line containing "Running Task" without the banner dashes.
TASK_COUNT_ARGS = [
    ["TASKBANNER", "OUT:--------- Entering Environment: Env1"],
    ["TASKBANNER", "OUT:Running Task setup helper"],
]


def test_task_count_exact_match_passes(fake_cli, tmp_path):
    # Case 17: exact banner count matches.
    doc = job_doc(TASK_COUNT_ARGS, expected={"taskCount": 2})
    ok, _ = run_job_fixture(tmp_path, "count.test.yaml", doc)
    assert ok


def test_task_count_off_by_one_fails(fake_cli, tmp_path):
    # Case 17: off-by-one -> fail, reporting the actual count.
    doc = job_doc(TASK_COUNT_ARGS, expected={"taskCount": 3})
    ok, detail = run_job_fixture(tmp_path, "count.test.yaml", doc)
    assert not ok
    assert "Expected taskCount 3 but counted 2" in detail


@pytest.mark.parametrize("value", [-1, "2", True, None, 1.5])
def test_task_count_malformed_is_hard_failure(fake_cli, tmp_path, value):
    # Case 17: non-int or negative taskCount is a hard failure.
    doc = job_doc(TASK_COUNT_ARGS, expected={"taskCount": value})
    ok, detail = run_job_fixture(tmp_path, "count.test.yaml", doc)
    assert not ok
    assert "Malformed taskCount" in detail


def test_task_count_zero_composes_with_failure(fake_cli, tmp_path):
    # Case 17: a failing run counts the tasks that ran (here: none).
    doc = job_doc([["ERR:boom", "EXITCODE:1"]], expected={
        "taskFailure": {"exitCode": 1},
        "taskCount": 0,
    })
    ok, _ = run_job_fixture(tmp_path, "count0.test.yaml", doc)
    assert ok


# ---------------------------------------------------------------------------
# Feature 4: skip visibility
# ---------------------------------------------------------------------------

def make_jobs_tree(root: Path) -> Path:
    jobs = root / "2023-09" / "base" / "jobs"
    jobs.mkdir(parents=True)
    return jobs


def test_skipped_fixture_counts_and_tuple_shapes(fake_cli, tmp_path, monkeypatch):
    # Case 18: runOn-mismatched fixture counts as skipped; directory runners
    # return (passed, failed, skipped, failed_tests).
    monkeypatch.setattr(runner, "CONFORMANCE_DIR", tmp_path)
    jobs = make_jobs_tree(tmp_path)
    write_job(jobs, "skipme.test.yaml", job_doc([["OUT:hi"]], runOn=[OTHER_OS]))
    write_job(jobs, "pass.test.yaml", job_doc([["OUT:hi"]], expected={"output": ["hi"]}))
    write_job(jobs, "fail.test.yaml", job_doc([["OUT:hi"]], expected={"output": ["absent"]}))

    passed, failed, skipped, failed_tests = runner.run_job_tests(jobs)
    assert (passed, failed, skipped) == (1, 1, 1)
    assert failed_tests == [str(Path("2023-09/base/jobs/fail.test.yaml"))]


def test_single_file_skip_returns_skipped_count(fake_cli, tmp_path):
    # Case 18: run_single_file threads the skipped count too.
    path = write_job(tmp_path, "skipme.test.yaml", job_doc([["OUT:hi"]], runOn=[OTHER_OS]))
    assert runner.run_single_file(path) == (0, 0, 1, [])


def test_main_reports_skips_in_summary_and_total(fake_cli, tmp_path, monkeypatch, capsys):
    # Case 18: skips appear in the per-directory summary and the Total line;
    # without the flag, an all-skipped run still exits 0.
    monkeypatch.setattr(runner, "CONFORMANCE_DIR", tmp_path)
    jobs = make_jobs_tree(tmp_path)
    write_job(jobs, "skipme.test.yaml", job_doc([["OUT:hi"]], runOn=[OTHER_OS]))
    monkeypatch.setattr(sys, "argv", ["run_openjd_cli_tests.py"])
    with pytest.raises(SystemExit) as excinfo:
        runner.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "0 passed, 0 failed, 1 skipped" in out
    assert "Total: 0 passed, 0 failed, 1 skipped" in out


def test_main_fail_on_skip_ratio_trips_on_all_skipped(fake_cli, tmp_path, monkeypatch, capsys):
    # Case 18: --fail-on-skip-ratio 0.0 makes an all-skipped run exit 1.
    monkeypatch.setattr(runner, "CONFORMANCE_DIR", tmp_path)
    jobs = make_jobs_tree(tmp_path)
    write_job(jobs, "skipme.test.yaml", job_doc([["OUT:hi"]], runOn=[OTHER_OS]))
    monkeypatch.setattr(sys, "argv", ["run_openjd_cli_tests.py", "--fail-on-skip-ratio", "0.0"])
    with pytest.raises(SystemExit) as excinfo:
        runner.main()
    assert excinfo.value.code == 1
    assert "Skip ratio 1.00 exceeds" in capsys.readouterr().out


def test_compute_exit_code_ratio_semantics():
    # Case 18: guard is strictly greater-than, absent by default, and does
    # not trip on zero total tests; failure exit semantics unchanged.
    assert runner.compute_exit_code(1, 0, 0, None) == 0
    assert runner.compute_exit_code(1, 1, 0, None) == 1
    assert runner.compute_exit_code(0, 0, 0, 0.0) == 0  # zero total: no trip
    assert runner.compute_exit_code(1, 0, 1, 0.5) == 0  # ratio == X: no trip
    assert runner.compute_exit_code(1, 0, 1, 0.4) == 1
    assert runner.compute_exit_code(0, 1, 0, 1.0) == 1  # failures still fail


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------

def test_plain_fixture_passes_identically(fake_cli, tmp_path):
    # Case 19: no new keys -> same behavior, raw output returned on pass.
    doc = job_doc([["OUT:hello world"]], expected={"output": ["hello world"]})
    result = run_job_fixture(tmp_path, "plain.test.yaml", doc)
    assert result is not None
    ok, output = result
    assert ok
    assert "hello world" in output


def test_legacy_anyof_in_expected_output_still_works(fake_cli, tmp_path):
    # Case 19: the pre-existing anyOf dict form inside expected.output.
    doc = job_doc([["OUT:hello world"]], expected={"output": [{"anyOf": ["nope", "hello world"]}]})
    ok, _ = run_job_fixture(tmp_path, "anyof.test.yaml", doc)
    assert ok

    doc = job_doc([["OUT:hello world"]], expected={"output": [{"anyOf": ["nope", "zilch"]}]})
    ok, detail = run_job_fixture(tmp_path, "anyof.test.yaml", doc)
    assert not ok
    assert "none of anyOf matched" in detail


def test_forbidden_still_enforced(fake_cli, tmp_path):
    # Case 19: forbidden assertions unchanged.
    doc = job_doc([["OUT:hello world"]], expected={"forbidden": ["hello"]})
    ok, detail = run_job_fixture(tmp_path, "forbid.test.yaml", doc)
    assert not ok
    assert "Found forbidden output: hello" in detail


def test_template_dir_runner_tuple_and_passes(fake_cli, tmp_path, monkeypatch):
    # Case 19/10: run_template_tests returns the 4-tuple with skipped == 0.
    monkeypatch.setattr(runner, "CONFORMANCE_DIR", tmp_path)
    tdir = tmp_path / "2023-09" / "base" / "job_templates"
    tdir.mkdir(parents=True)
    (tdir / "good.yaml").write_text("# ok\nname: X\n", encoding="utf-8")
    (tdir / "bad.invalid.yaml").write_text("# FAKE-CHECK-ERROR: broken\nname: X\n", encoding="utf-8")
    passed, failed, skipped, failed_tests = runner.run_template_tests(tdir)
    assert (passed, failed, skipped, failed_tests) == (2, 0, 0, [])


def test_run_check_returns_combined_stderr_and_stdout(fake_cli, tmp_path):
    # Case 20: run_check must return stderr + stdout combined.
    path = tmp_path / "combined.invalid.yaml"
    path.write_text(
        "# FAKE-CHECK-STDOUT: OUT-PIECE\n# FAKE-CHECK-ERROR: ERR-PIECE\nname: X\n",
        encoding="utf-8",
    )
    success, output = runner.run_check(path)
    assert not success
    assert "ERR-PIECE" in output
    assert "OUT-PIECE" in output


# ---------------------------------------------------------------------------
# UTF-8 stream guard (the import-time stdout rewrap must not break pytest)
# ---------------------------------------------------------------------------

def test_utf8_stream_leaves_bufferless_streams_alone():
    stream = io.StringIO()  # like pytest capture objects without .buffer
    assert runner._utf8_stream(stream) is stream


def test_utf8_stream_leaves_utf8_streams_alone():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="UTF-8")
    assert runner._utf8_stream(stream) is stream


def test_utf8_stream_rewraps_non_utf8_buffered_stream():
    # Conceptual Windows console: cp1252 text stream over a raw buffer gets
    # rewrapped to UTF-8 over the same buffer.
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    wrapped = runner._utf8_stream(stream)
    assert wrapped is not stream
    wrapped.write("check mark: \u2713")
    wrapped.flush()
    assert raw.getvalue() == "check mark: \u2713".encode("utf-8")
