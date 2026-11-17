from helpers import SLOW_TEST_S, SlowLimit


def test_a_test_over_the_limit_in_all_phases_is_named_with_its_time_and_the_limit():
    limit = SlowLimit()
    assert limit.record("t", "setup", 1.0) == ""
    assert limit.record("t", "call", 3.0) == ""
    note = limit.record("t", "teardown", 1.5)
    assert "5.5" in note
    assert "5 s" in note
    assert note.startswith("t ")


def test_a_test_within_the_limit_is_not_named():
    limit = SlowLimit()
    limit.record("t", "setup", 1.0)
    limit.record("t", "call", 3.0)
    assert limit.record("t", "teardown", 0.9) == ""


def test_exactly_the_limit_is_within_it():
    assert SlowLimit().record("t", "teardown", SLOW_TEST_S) == ""


def test_two_tests_are_added_up_apart():
    limit = SlowLimit()
    limit.record("a", "call", 3.0)
    limit.record("b", "call", 3.0)
    assert limit.record("a", "teardown", 1.0) == ""
    assert limit.record("b", "teardown", 1.0) == ""
