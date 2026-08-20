# Self-asserting conformance wrapper, shared by every instrumented job test.
#
# A fixture pulls this file in with `dataFile: _conformance_assert.py` on an
# embedded file named OpenJDConformanceAssert; the harness inlines it into that
# file's `data` before submitting the template. See conformance-tests/README.md,
# "Self-asserting job tests", for why this exists and what to preserve when
# editing a case.
#
# Invoked as:
#
#   python <this file> <expect.json> <the case's original command and args...>
#
# argv[1] is the case's OpenJDConformanceExpect embedded file, holding the lines
# the case must and must not print. argv[2:] is the case's original command,
# already expanded by the implementation -- passed through rather than embedded
# here, so a case whose subject IS that expansion stays under test.
import json
import subprocess
import sys

PLATFORM = "windows" if sys.platform == "win32" else "posix"


def _lines(case, key):
    """The case's `key` lines: the common ones plus this platform's.

    Mirrors the fixture's own `expected` block, which spells a platform split
    as `output` plus `output_posix` / `output_windows`.
    """
    return list(case.get(key) or []) + list(case.get("%s_%s" % (key, PLATFORM)) or [])


with open(sys.argv[1]) as handle:
    case = json.load(handle)
expected = _lines(case, "expected")
forbidden = _lines(case, "forbidden")

completed = subprocess.run(sys.argv[2:], capture_output=True, text=True)
# Echoed verbatim so a runner that scans logs still sees the same lines.
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
output = completed.stdout + completed.stderr

missing = [i for i, line in enumerate(expected, 1) if line not in output]
present = [i for i, line in enumerate(forbidden, 1) if line in output]
if completed.returncode or missing or present:
    # Positions, never the text. Printing a literal would place it in the very
    # output a log-scanning runner searches, so a failing case would satisfy
    # that runner with its own error message. Read the positions against the
    # case's `expected` block.
    sys.stderr.write(
        "OPENJD_CONFORMANCE_ASSERT_FAILED: exit=%s missing=%s of %d forbidden=%s of %d\n"
        % (completed.returncode, missing, len(expected), present, len(forbidden))
    )
    sys.exit(1)
# How you confirm the assertion actually ran, rather than the case having passed
# for some other reason.
sys.stdout.write(
    "OPENJD_CONFORMANCE_ASSERT_OK: %d expected, %d forbidden\n" % (len(expected), len(forbidden))
)
