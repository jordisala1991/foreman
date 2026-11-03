"""foreman plain: the rule-based check of what the owner reads (docs/owner.md). The good texts must give no
finding and the bad ones exactly the flagged parts; examples come from the guide and from the plan's baseline,
with invented names."""

import json

from helpers import ProjectCase

from foreman import config, plain

TOML = """
[project]
name = "fake"
owner = "Owner"
language = "es"
owner_knows = ["CI", "PR", "local_ci"]

[ids]
issue = "^[A-Z]{2}-\\\\d+$"
"""

# Each one must pass clean.
GOOD = [
    # docs/owner.md, «after»
    "Two questions wait for you in the panel: who writes the machine's GPU table, and whether the config folder "
    "goes into a local git",
    "The export to spreadsheet is in (AB-12): a 10 000-row report now takes 4 s instead of 40. Nothing to do on "
    "your side",
    "Should the warning also go to the other products' orchestrators? It would be an optional last part of the work "
    "(AB-54). I recommend yes: it costs one more step and they miss it today",
    "The check that tells whether the GPU is in use was wrong and let two jobs in at once; it is fixed and tested. "
    "Nothing to do on your side",
    "I have handed over to a fresh session because this one's memory was full; the work goes on as it was and "
    "nothing is asked of you",
    "type `/reload-plugins` in that session to load the new version",
    "the dead-orchestrator warning (AB-54)",
    # the id beside what it names, in Spanish too
    "el aviso de orquestador muerto (AB-54)",
    "AB-54, el aviso de orquestador muerto, ya sale una vez por caso",
    "AB-77 J1, si lees las notas del día: recomiendo que sigan técnicas",
    "AB-12: la exportación a hoja de cálculo",
    "la tercera parte del trabajo (AB-12 T3) ya está",
    "el paso 3: la mezcla con la rama principal",
    "**1. AB-69 J1: ¿quién escribe la GPU en la configuración de la máquina?**",
    "- **AB-69:** la GPU compartida, declarada una sola vez",
    # rewritten versions of the plan's baseline (AB-77 §6)
    "Dos preguntas te esperan en el panel: quién escribe la tabla de la GPU de la máquina y si la carpeta de "
    "config va en un git local",
    "¿Enseño en el panel qué procesos usan la GPU fuera del turno? Hoy solo lo avisa el orquestador en su repaso "
    "de cada hora. Recomiendo que baste con ese aviso",
    "Los recursos de la máquina, como la GPU, se declaran una sola vez para todos los productos",
    # what the owner knows, commands, URLs, code blocks and ordinary text
    "The CI is green and the PR is ready; local_ci runs twice",
    "run `foreman plain -` to check a text before sending it",
    "the page is at https://example.com/a/b_c/d.md",
    "the steps were:\n```\nfoo_bar step 4 T3 lib/x.py:12\n```\nall done",
    "and/or 24/7 at 80 km/h, 1/2 of them, version 0.21.0, 2026-10-31",
    "a decade ago, café, ¿qué tal?, e.g. TV, USB-C, UTF-8, ISO-8601",
]

# Each one with exactly the flagged texts, in order.
BAD = [
    ("I need your answers to J1 and J2", ["J1", "J2"]),
    ("necesito tus respuestas a J1 y J2", ["J1", "J2"]),
    ("necesito tu sí para T5", ["T5"]),
    ("Yes, as optional T4?", ["T4"]),
    ("AB-12 T3 merged, zed7 delivered, main_ci green", ["AB-12 T3", "zed7", "main_ci"]),
    ("busy script exit 1 in step 4, fixed in lock.py:212", ["step 4", "lock.py:212"]),
    ("Rotating: context_rotate reached, orch-b2 relieves orch-a1", ["context_rotate", "orch-b2", "orch-a1"]),
    ("como en el paso 10", ["paso 10"]),
    ("J1, J2 and J3 wait", ["J1", "J2", "J3"]),
    ("AB-12, and then AB-13", ["AB-12", "AB-13"]),
    # the plan's baseline, invented names
    (
        "El 8765 enseñaba qué procesos usan la GPU. Tras el corte, el bucle lo avisa en la pasada (loop_checks). "
        "T5 lo pondría también en la tarjeta del recurso.",
        ["loop_checks", "T5"],
    ),
    (
        "~/.config/foreman/bin/gpu-busy.sh no queda en ningún git. ¿Vale así o quieres ~/.config/foreman/ en un git "
        "local?",
        ["~/.config/foreman/bin/gpu-busy.sh", "~/.config/foreman/"],
    ),
    ("identidad registrada al lanzarlos; FOREMAN_WHO sin que el agente lo escriba", ["FOREMAN_WHO"]),
    ("kit-9 terminó en la rama kit/export", ["kit-9", "kit/export"]),
    # internal names
    ("set [repos.*] main_ci and `busy` in [agents]", ["[repos.*] main_ci", "busy", "[agents]"]),
    ("[deny] sigue igual y [resources.gpu] del registro", ["[deny]", "[resources.gpu]"]),
    ("see docs/owner.md and lib/foreman/plain.py", ["docs/owner.md", "lib/foreman/plain.py"]),
    ("merged as 940852d into main", ["940852d"]),
    ("session f2e71488-a630-425e-b0c4-61be0d626023 is gone", ["f2e71488-a630-425e-b0c4-61be0d626023"]),
    ("handover() writes it; see foreman.toml", ["handover()", "foreman.toml"]),
    ("`$FOREMAN_STATE_HOME` points elsewhere", ["$FOREMAN_STATE_HOME"]),
]


class PlainTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        st = self.project.state
        st.mkdir(parents=True, exist_ok=True)
        (st / "agents.json").write_text(
            json.dumps({"a1": {"name": "zed7", "type": "dev"}, "a2": {"name": "scout", "type": "dev"}})
        )
        (st / "launches.json").write_text(json.dumps({"tok": {"name": "orch-b2", "branch": None}}))
        (st / "orchestrator.json").write_text(json.dumps({"session": "s1", "name": "orch-a1", "since": "x"}))
        (self.home / "backlog.json").write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "issues": [
                        {"id": "AB-12", "title": "t", "subs": [{"id": "T1", "agent": "kit-9", "branch": "kit/export"}]}
                    ],
                    "archive": [],
                }
            )
        )

    def flagged(self, text):
        return [f["text"] for f in plain.findings(self.project, text)]

    def test_good_texts_pass(self):
        for text in GOOD:
            self.assertEqual(self.flagged(text), [], text)

    def test_bad_texts_are_flagged(self):
        for text, want in BAD:
            self.assertEqual(self.flagged(text), want, text)

    def test_findings_have_kind_span_and_reason(self):
        text = "AB-12 T3 merged, zed7 delivered, main_ci green"
        found = plain.findings(self.project, text)
        self.assertEqual([f["kind"] for f in found], ["code", "name", "internal"])
        for f in found:
            self.assertEqual(text[f["start"] : f["end"]], f["text"])
            self.assertTrue(f["reason"])
            self.assertIn(f["what"], plain.REASONS)

    def test_known_names_from_state_and_backlog(self):
        names = plain.known_names(self.project)
        self.assertEqual(
            names, {"zed7": "agent", "orch-b2": "agent", "orch-a1": "session", "kit-9": "agent", "kit/export": "branch"}
        )  # «scout» is letters only: an ordinary word
        self.assertEqual(plain.known_names(None), {})

    def test_without_project_or_state(self):
        self.assertEqual([f["text"] for f in plain.findings(None, "zed7 needs J1 and main_ci")], ["J1", "main_ci"])
        for f in ("agents.json", "launches.json", "orchestrator.json"):
            (self.project.state / f).write_text("{not json")
        self.assertEqual(self.flagged("zed7 and kit-9"), ["kit-9"])

    def test_owner_knows(self):
        self.assertEqual(self.flagged("local_ci and ci_runs"), ["ci_runs"])

    def test_config_values(self):
        for bad in ("CI", [1], [""]):
            err, _ = config.problems({"project": {"name": "x", "owner_knows": bad}})
            self.assertTrue(any("owner_knows" in e for e in err), bad)
        self.assertEqual(config.problems({"project": {"name": "x", "owner_knows": ["CI"]}}), ([], []))

    def test_cli_exit_codes_and_json(self):
        r = self.cli("plain", "necesito", "tu", "sí", "para", "T5")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("«T5»", r.stdout)
        self.assertIn("1:21", r.stdout)
        r = self.cli("plain", "--json", "-", inp="AB-12 T3 merged, zed7 delivered")
        self.assertEqual(r.returncode, 1, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["words"], 5)
        self.assertEqual([f["text"] for f in d["findings"]], ["AB-12 T3", "zed7"])
        r = self.cli("plain", "the dead-orchestrator warning (AB-54)")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "plain: nothing to explain"))
        r = self.cli("plain", "--json", "-", inp="nothing odd here")
        self.assertEqual((r.returncode, json.loads(r.stdout)["findings"]), (0, []))
        self.assertEqual(self.cli("plain", "-", inp="  \n").returncode, 2)

    def test_summary_line(self):
        found = plain.findings(self.project, "J1 J2 J3 J4")
        self.assertEqual(plain.summary(found, limit=2).count("«"), 2)
        self.assertTrue(plain.summary(found, limit=2).endswith("and 2 more"))
