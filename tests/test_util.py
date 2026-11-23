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
