"""The typed border of the GitHub thread read."""

import unittest

from fm.providers import ProviderError, gh_threads


class Parse(unittest.TestCase):
    def test_a_thread_without_a_list_of_bodies_is_a_misfit(self):
        with self.assertRaises(ProviderError):
            gh_threads.parse('{"id":"t","resolved":false,"bodies":null}')

    def test_a_line_that_is_not_json_is_a_misfit(self):
        with self.assertRaises(ProviderError):
            gh_threads.parse("nope")

    def test_a_reply_marked_fixed_settles_the_thread(self):
        out = '{"id":"t","resolved":false,"bodies":["x <!-- fm:finding-ab1 -->","Fixed in abc.\\n\\n<!-- fm:fixed-finding-ab1 -->"]}'
        (t,) = gh_threads.parse(out)
        self.assertTrue(t.settled)
