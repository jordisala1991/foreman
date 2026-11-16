"""The verdict as a review body: pure."""

from foreman.core.review import verdict
from foreman.core.review.verdict import FIX, LOOK, READY, Case, Finding, findings, key, render


def case(risk="low", tests="pass", ci="pass", open=0, reasons=None) -> Case:
    return Case(risk, reasons or ["r"], tests, ci, "abcdef1234567", "does x", 3, 120, open)


def f(sev: str, what: str, place: str = "a.py:1", why: str = "") -> Finding:
    return Finding("F1", sev, place, what, why)


class TestVerdict:
    def test_the_headline_follows_the_findings_checks_and_risk(self):
        rows = [
            (case(), [], READY),
            (case(open=0), [f("L", "a"), f("L", "b")], READY),
            (case(open=1), [f("M", "a")], LOOK.format(n=1)),
            (case("medium"), [], LOOK.format(n=1)),
            (case("medium", open=2), [f("M", "a"), f("M", "b")], LOOK.format(n=3)),
            (case("high", open=1), [f("H", "a")], FIX.format(n=1)),
            (case(tests="fail"), [], FIX.format(n=1)),
            (case("high", "fail", "fail", 1), [f("H", "a")], FIX.format(n=3)),
            (case(open=1), [], LOOK.format(n=1)),
        ]
        for c, found, want in rows:
            assert verdict.headline(c, found) == want

    def test_the_body_has_the_facts_in_order(self):
        c = case("medium", ci="", reasons=["r one", "r two"])
        body = render(c, [], set())
        order = [body.index(x) for x in ("Look", "does x", "r one", "abcdef123", "120")]
        assert order == sorted(order)
        assert "r two" in body
        placed = f("M", "w", why="secret why")
        assert "secret why" not in render(case(open=1), [placed], set())

    def test_only_blocking_findings_sit_outside_the_fold(self):
        found = [f("H", "hhh"), f("M", "mmm"), f("L", "placed", "a.py:2"), f("L", "unplaced", "a.py:3")]
        placed = {found[2].place}
        body = render(case(open=2), found, placed)
        fold = body.index("<details")
        assert body.index("hhh") < fold
        assert "mmm" not in body[:fold]
        assert body.index("mmm") > fold
        assert body.index("unplaced") > fold
        assert "placed" not in body.replace("unplaced", "")

    def test_a_repeated_nit_is_still_reachable(self):
        a, b = f("L", "same", "a.py:999"), f("L", "same", "a.py:3")
        body = render(case(open=0), [a, b], {"a.py:3"})
        assert "a.py:999" in body
        assert "a.py:3" not in body

    def test_counts_use_the_singular_for_one(self):
        assert "1 nit<" in render(case(open=0), [f("L", "x", "-")], set())
        assert "1 nits" not in render(case(open=1), [f("L", "x", "-")], set())
        assert "2 nits" in render(case(open=0), [f("L", "x", "-"), f("L", "y", "-")], set())

    def test_reviewer_text_cannot_notify_or_break_the_fold(self):
        body = render(case(open=1), [f("H", "ping @someone </details>")], set())
        assert "@someone" not in body
        assert "</details>" not in body

    def test_findings_parse_only_the_fixed_line(self):
        text = "Open: H 1\nFindings:\nF1 | H | a.py:1 | bad | it breaks\n**F1 · H** prose\n- F2 | M | - | other\nF3 | X | a | b"
        assert findings(text) == [Finding("F1", "H", "a.py:1", "bad", "it breaks"), Finding("F2", "M", "-", "other", "")]
        assert findings("") == []

    def test_a_finding_keeps_its_key_when_its_id_or_line_moves(self):
        assert key(findings("F1|M|a.py:3|x")[0]) == key(findings("F7|M|a.py:9|x")[0])
        assert key(findings("F1|M|a.py:3|x")[0]) != key(findings("F1|M|a.py:3|y")[0])
