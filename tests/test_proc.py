import sys
import unittest

from fm import proc


class Run(unittest.TestCase):
    def test_a_result_has_the_code_and_both_outputs(self):
        r = proc.run([sys.executable, "-c", "import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)"])
        self.assertEqual(r, proc.Result(3, "o\n", "e\n"))

    def test_input_cwd_and_env_reach_the_program(self):
        r = proc.run([sys.executable, "-c", "import os, sys; print(sys.stdin.read(), os.getcwd(), os.environ['X'])"], input="hi", cwd="/", env={"X": "1"})
        self.assertEqual(r.out.split(), ["hi", "/", "1"])

    def test_a_missing_program_is_a_clear_proc_error(self):
        with self.assertRaisesRegex(proc.ProcError, "no-such-program-fm --x: could not start") as c:
            proc.run(["no-such-program-fm", "--x"])
        self.assertFalse(c.exception.timed_out)

    def test_a_timeout_is_a_proc_error_that_says_so(self):
        with self.assertRaisesRegex(proc.ProcError, "timed out after 0.2s") as c:
            proc.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
        self.assertTrue(c.exception.timed_out)


class RunJson(unittest.TestCase):
    def test_json_output_is_parsed_whatever_the_exit_code(self):
        self.assertEqual(proc.run_json([sys.executable, "-c", "print('[1, {\"a\": 2}]')"]), [1, {"a": 2}])

    def test_output_that_is_not_json_is_a_proc_error(self):
        with self.assertRaisesRegex(proc.ProcError, "not JSON"):
            proc.run_json([sys.executable, "-c", "print('oops')"])


class Spawn(unittest.TestCase):
    def test_spawn_gives_a_live_process(self):
        with proc.spawn([sys.executable, "-c", "import sys; sys.exit(4)"]) as p:
            self.assertEqual(p.wait(timeout=10), 4)

    def test_spawn_of_a_missing_program_is_a_proc_error(self):
        with self.assertRaises(proc.ProcError):
            proc.spawn(["no-such-program-fm"])


if __name__ == "__main__":
    unittest.main()
