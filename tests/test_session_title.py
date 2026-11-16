from foreman import util


class TestSessionTitle:
    def test_a_long_title_is_cut_at_the_limit_with_an_ellipsis(self):
        assert (
            util.session_title("FN-106", "review", "Failing tests or CI send the task back to its agent")
            == "FN-106 review · Failing tests or CI send the task back t…"
        )

    def test_a_title_of_the_limit_is_whole_and_one_more_is_cut(self):
        assert util.session_title("FN-1", "parent", "a" * 40) == "FN-1 parent · " + "a" * 40
        assert util.session_title("FN-1", "parent", "a" * 41) == "FN-1 parent · " + "a" * 40 + "…"

    def test_id_and_role_are_never_cut(self):
        assert util.session_title("FN-106", "review", "x" * 500).startswith("FN-106 review · ")
