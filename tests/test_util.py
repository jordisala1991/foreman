from pathlib import Path

from foreman import util


def test_log_error_writes_time_step_error_and_extras(tmp_path: Path):
    f = tmp_path / "errors.jsonl"
    util.log_error(f, "queue", "boom", trace="tb")
    util.log_error(f, "source x", "bad", signal={"a": 1})
    first, second = util.read_jsonl(f)
    assert set(first) == {"time", "step", "error", "trace"}
    assert (first["step"], first["error"], first["trace"]) == ("queue", "boom", "tb")
    assert set(second) == {"time", "step", "error", "signal"}
    assert second["signal"] == {"a": 1}
    assert util.age_s(second["time"]) is not None


def test_log_exception_writes_repr_and_the_last_2000_characters_of_the_traceback(tmp_path: Path):
    f = tmp_path / "errors.jsonl"
    try:
        raise ValueError("x" * 5000)
    except ValueError as e:
        long = e
        util.log_exception(f, "step a", e)
    try:
        raise ValueError("short")
    except ValueError as e:
        util.log_exception(f, "step b", e)
    first, second = util.read_jsonl(f)
    assert set(first) == {"time", "step", "error", "trace"}
    assert (first["step"], first["error"]) == ("step a", repr(long))
    assert len(first["trace"]) == 2000
    assert first["trace"].endswith("x" * 100 + "\n")
    assert second["trace"].startswith("Traceback")
    assert second["trace"].endswith("ValueError: short\n")
