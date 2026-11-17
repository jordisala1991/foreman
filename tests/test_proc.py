import sys

import pytest

from foreman import proc


class TestRun:
    def test_a_result_has_the_code_and_both_outputs(self):
        r = proc.run([sys.executable, "-c", "import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)"])
        assert r == proc.Result(3, "o\n", "e\n")

    def test_input_cwd_and_env_reach_the_program(self):
        r = proc.run([sys.executable, "-c", "import os, sys; print(sys.stdin.read(), os.getcwd(), os.environ['X'])"], input="hi", cwd="/", env={"X": "1"})
        assert r.out.split() == ["hi", "/", "1"]

    def test_a_missing_program_is_a_clear_proc_error(self):
        with pytest.raises(proc.ProcError, match="no-such-program-fm --x: could not start") as c:
            proc.run(["no-such-program-fm", "--x"])
        assert not c.value.timed_out

    def test_a_timeout_is_a_proc_error_that_says_so(self):
        with pytest.raises(proc.ProcError, match="timed out after 0.001s") as c:  # over at once: no real wait, the program is killed
            proc.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.001)
        assert c.value.timed_out


class TestRunJson:
    def test_json_output_is_parsed_whatever_the_exit_code(self):
        assert proc.run_json([sys.executable, "-c", "print('[1, {\"a\": 2}]')"]) == [1, {"a": 2}]

    def test_output_that_is_not_json_is_a_proc_error(self):
        with pytest.raises(proc.ProcError, match="not JSON"):
            proc.run_json([sys.executable, "-c", "print('oops')"])
