"""Backlog, Wiki and Sistema of the new panel: the built bundle mounted in node over the minimal DOM of
tests/next_dom.mjs, with the panel's answers replaced by the scenario's (see test_panel_next.py)."""

import unittest
from typing import ClassVar

from helpers import ProjectCase
from test_panel_next import NextPageTest


def urls(out):
    """The POSTs a scenario sent, as the browser sent them (with the key)."""
    return out["rawPosts"]


def card(cid, status, title, **kw):
    return {"id": cid, "project": "E1", "title": title, "status": status, "subs": [], "plan": [], **kw}


A1 = card(
    "A-1",
    "in progress",
    "Un título largo del panel nuevo con todos sus detalles",
    short_name="Panel nuevo",
    plan=["reviews/plan.md"],
    subs=[
        {"id": "T0", "title": "Maquetas", "status": "done", "ref": "abc1234", "commit": "abc1234"},
        {"id": "T1", "title": "Armazón", "status": "in progress", "ref": "ag1"},
    ],
    outcome={"claim": "Un panel que se usa.", "criterion": "Lo usa una semana."},
    integrated={"n": 1, "of": 2, "commits": ["abc1234"]},
    detail="2026-11-06 Ana (chat): «quiero verlo». 2026-11-07 orquestador: hecho.",
    docs=[
        {"kind": "plan", "doc": "reviews/plan.md", "title": "Plan del panel"},
        {"kind": "report", "doc": "reviews/t0.md", "sub": "T0"},
        {"kind": "review", "doc": "reviews/r1.md", "sub": "T0", "round": 1, "open": 0},
    ],
)
A2 = card("A-2", "ready", "Bloqueada por otra", blocked="espera a tidepool: que confirmen", why="espera a tidepool")
A3 = card("A-3", "ready", "Lista para empezar")
A4 = card("A-4", "triage", "Sin clasificar")
A5 = card("A-5", "done", "Ya hecha", short=True)
BACKLOG = {
    "project": {
        "name": "alpha",
        "backlog": {
            "projects": [{"id": "E1", "title": "core", "status": "in progress", "issues": [A1, A2, A3, A4, A5]}],
            "issues": [],
            "archive": [],
            "counts": {},
        },
    }
}
STATE = {
    "project": {
        "issue_live": {"A-1": {"agent": "ag1", "production": True}},
        "targets": {"production_slots": 3, "ready_target": 2},
        "activity": [
            {"id": 1, "time": "2026-11-07 10:00", "text": "A-3: 2 -> 1 in E1", "move": "A-3"},
            {"id": 2, "time": "2026-11-07 09:00", "text": "A-1: 3 -> 1 in E1", "move": "A-1"},
        ],
        "notes": [{"time": "09:30", "text": "Una nota del orquestador"}],
        "progress": [],
        "recurring": [
            {
                "name": "Bucle",
                "every": "cada 60 min",
                "late": True,
                "parts": [{"last": "2026-11-06 10:00", "late": True}],
            }
        ],
    }
}
STATE_QUIET = {"project": {**STATE["project"], "recurring": []}}
ISSUE = {"issue": A1, "cost": {"usd": 22.81, "runs": 9, "tokens": 1000, "cheap_share": 34.4}}


class NextMoreTest(ProjectCase):
    mount = NextPageTest.mount
    ALPHA = NextPageTest.ALPHA
    PROJECTS = NextPageTest.PROJECTS

    def screen(self, hash_, **answers):
        base = {
            "/api/projects": self.PROJECTS,
            "/api/backlog?p=alpha&closed=recent": BACKLOG,
            "/api/state?p=alpha": STATE,
        }
        base.update(answers)
        return self.mount({"hash": hash_, "answers": base})


class BacklogTest(NextMoreTest):
    def test_the_list_groups_by_status_with_the_goal_in_words(self):
        out = self.screen("#/backlog")
        rows = out["rows"]
        self.assertIn("▾ En curso 1 1 de 3 agentes trabajando", rows)
        self.assertIn("▾ Por empezar 2 1 listas · quieres 2 · 1 con bloqueo", rows)
        self.assertIn("▾ Por clasificar 1", rows)
        self.assertIn("▾ Hecho 1", rows)
        text = out["text"]
        self.assertIn("Panel nuevo", text)  # the short name, not the long title
        self.assertNotIn("Un título largo del panel nuevo", text)
        self.assertIn("espera a tidepool", text)  # the short reason of the block, next to the name
        self.assertNotIn("que confirmen", text)

    def test_an_empty_working_group_says_why(self):
        state = {"project": {**STATE["project"], "issue_live": {}}}
        solo = {
            "project": {
                **BACKLOG["project"],
                "backlog": {
                    **BACKLOG["project"]["backlog"],
                    "projects": [{"id": "E1", "title": "core", "status": "in progress", "issues": [A3]}],
                },
            }
        }
        out = self.screen("#/backlog", **{"/api/state?p=alpha": state, "/api/backlog?p=alpha&closed=recent": solo})
        self.assertIn("Nadie trabaja ahora. Hay 1 listas para empezar.", out["text"])

    def test_the_projects_view(self):
        out = self.screen("#/backlog/projects")
        self.assertIn("E1", out["text"])
        self.assertIn("1 de 4 hechas", out["text"])  # 1 done of the 4 that count (not triage)
        self.assertIn("1 en curso", out["text"])
        self.assertIn("1 con bloqueo", out["text"])

    def test_a_project_page(self):
        out = self.screen("#/backlog/p/E1")
        self.assertIn("Backlog / Proyectos / E1", out["text"])
        self.assertIn("Progreso", out["text"])
        self.assertIn("Lo que has hecho tú en el proyecto", out["text"])
        self.assertIn("A-3: 2 -> 1 in E1", out["text"])
        self.assertIn("5 tareas", out["text"])

    def test_the_task_page(self):
        out = self.screen("#/backlog/A-1", **{"/api/issue?p=alpha&id=A-1": ISSUE})
        text = out["text"]
        for want in (
            "Backlog / E1 / A-1",
            "Panel nuevo",
            "Un título largo del panel nuevo",  # the long title under the short name
            "Para qué es",
            "Está hecha cuando",
            "1 de 2 hechas",
            "Plan del panel",
            "Leer aquí",
            "T0 · ronda 1 · nada pendiente",
            "1.ª de 3 en E1",
            "$22.81 · 9 ejecuciones · 34 % con Sonnet o Haiku",
            "1 de 2 partes con commit · última abc1234",
            "Lo que se ha dicho",
            "Lo que has hecho tú",
        ):
            self.assertIn(want, text)
        self.assertNotIn(".md", text)  # documents by type, never by file name
        self.assertIn(["Leer aquí", "#/backlog/doc/reviews%2Fplan.md"], [list(a) for a in out["links"]])
        # empty data is not shown: an agent is live, so no «nothing now» line
        self.assertNotIn("Ahora sin agente", text)

    def test_a_task_in_triage_asks_first(self):
        triage = {"issue": A4, "cost": None}
        out = self.screen("#/backlog/A-4", **{"/api/issue?p=alpha&id=A-4": triage})
        self.assertIn("¿La quieres hacer?", out["text"])
        self.assertIn("La quiero hacer", out["buttons"])
        self.assertIn("No la quiero", out["buttons"])
        self.assertIn("Aún sin objetivo ni criterio", out["text"])
        out = self.mount(
            {
                "hash": "#/backlog/A-4",
                "click": "No la quiero",
                "answers": {
                    "/api/projects": self.PROJECTS,
                    "/api/backlog?p=alpha&closed=recent": BACKLOG,
                    "/api/state?p=alpha": STATE,
                    "/api/issue?p=alpha&id=A-4": triage,
                },
            }
        )
        self.assertIn("¿Seguro?", out["text"])  # «I don't want it» still asks to confirm

    def test_a_closed_card_without_detail_still_opens(self):
        closed = {"issue": {**A5, "detail": "", "docs": []}, "cost": None}
        out = self.screen("#/backlog/A-5", **{"/api/issue?p=alpha&id=A-5": closed})
        self.assertIn("Ya hecha", out["text"])
        self.assertIn("Hecho", out["text"])

    def write(self, hash_, answers, **scenario):
        base = {
            "/api/projects": self.PROJECTS,
            "/api/backlog?p=alpha&closed=recent": BACKLOG,
            "/api/state?p=alpha": STATE,
            "/api/issue?p=alpha&id=A-4": {"issue": A4, "cost": None},
        }
        base.update(answers)
        return self.mount({"hash": hash_, "answers": base, **scenario})

    def test_want_posts_with_the_key_and_the_flag(self):
        out = self.write("#/backlog/A-4", {"POST /api/want?p=alpha&id=A-4&on=1": {"ok": True}}, click="La quiero hacer")
        self.assertEqual(urls(out), ["/api/want?p=alpha&id=A-4&on=1&t=k3y"])
        self.assertIn("Hecho", out["text"])

    def test_a_refused_write_shows_why(self):
        refused = {"__status": 400, "error": "orchestrator not active"}
        out = self.write("#/backlog/A-4", {"POST /api/want?p=alpha&id=A-4&on=1": refused}, click="La quiero hacer")
        self.assertIn("No se pudo: orchestrator not active", out["text"])

    def test_reject_asks_then_posts(self):
        answers = {"POST /api/reject?p=alpha&id=A-4": {"ok": True}}
        asked = self.write("#/backlog/A-4", answers, click="No la quiero")
        self.assertEqual(urls(asked), [])  # the first click only asks
        out = self.write("#/backlog/A-4", answers, click="No la quiero", then="Sí, no la quiero")
        self.assertEqual(urls(out), ["/api/reject?p=alpha&id=A-4&t=k3y"])

    def test_a_double_click_sends_one_post(self):
        out = self.write(
            "#/backlog/A-4",
            {"POST /api/want?p=alpha&id=A-4&on=1": {"ok": True}},
            clicks=["La quiero hacer", "La quiero hacer"],
        )
        self.assertEqual(len(urls(out)), 1)

    def test_arrows_on_a_grip_move_the_task(self):
        ok = {"POST /api/move?p=alpha&id=A-2&a=2": {"ok": True}, "POST /api/move?p=alpha&id=A-3&a=1": {"ok": True}}
        out = self.write("#/backlog/p/E1", ok, press={"nth": 1, "key": "ArrowDown"})
        self.assertEqual(urls(out), ["/api/move?p=alpha&id=A-2&a=2&t=k3y"])
        out = self.write("#/backlog/p/E1", ok, press={"nth": 2, "key": "ArrowUp"})
        self.assertEqual(urls(out), ["/api/move?p=alpha&id=A-3&a=1&t=k3y"])

    def test_arrows_on_a_project_grip_move_the_project(self):
        two = {
            "project": {
                "name": "alpha",
                "backlog": {
                    **BACKLOG["project"]["backlog"],
                    "projects": [
                        {"id": "E1", "title": "core", "status": "in progress", "issues": [A1]},
                        {"id": "E2", "title": "extra", "status": "planned", "issues": [A3]},
                    ],
                },
            }
        }
        answers = {
            "/api/backlog?p=alpha&closed=recent": two,
            "POST /api/move?p=alpha&kind=project&id=E1&a=1": {"ok": True},
        }
        out = self.write("#/backlog/projects", answers, press={"nth": 0, "key": "ArrowDown"})
        self.assertEqual(urls(out), ["/api/move?p=alpha&kind=project&id=E1&a=1&t=k3y"])

    def test_a_refused_move_shows_why(self):
        refused = {"__status": 403, "error": "orchestrator not active"}
        out = self.write(
            "#/backlog/p/E1", {"POST /api/move?p=alpha&id=A-2&a=2": refused}, press={"nth": 1, "key": "ArrowDown"}
        )
        self.assertIn("orchestrator not active", out["text"])


PAGES = [
    {"n": "overview", "title": "Panorama", "type": "overview", "status": "activo", "updated": "2026-11-06"},
    {"n": "splash", "title": "splash", "type": "engine", "status": "activo", "updated": "2026-11-07"},
    {"n": "mlx", "title": "mlx-serve", "type": "engine", "status": "en-prueba", "updated": "2026-11-01"},
    {"n": "old", "title": "Viejo", "type": "engine", "status": "descartado", "updated": "2026-10-01"},
]
PAGE = {
    **PAGES[1],
    "sources": ["reviews/x.md"],
    "docs": {"reviews/x.md": "reviews/x.md"},
    "md": (
        "# splash\n\nIntro con [[mlx]] y [[nada]].\n\n## Uso\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "- uno\n- dos\n\n### Detalle\n\n`código`\n"
    ),
}


class WikiTest(NextMoreTest):
    WIKI_PRODUCTS: ClassVar[dict] = {
        "projects": [
            {**NextPageTest.ALPHA, "wiki": False},
            {"name": "beta", "title": "Beta", "next": True, "wiki": True, "wiki_first": ["overview"]},
        ],
        "language": "es",
    }

    def test_a_product_without_wiki_leads_to_one_that_has_it(self):
        out = self.mount({"hash": "#/wiki", "answers": {"/api/projects": self.WIKI_PRODUCTS}})
        self.assertIn("Alpha no tiene wiki", out["text"])
        self.assertIn("Ver la wiki de Beta", out["buttons"])

    def beta(self, hash_):
        return self.mount(
            {
                "hash": hash_,
                "answers": {
                    "/api/projects": {**self.WIKI_PRODUCTS, "projects": [self.WIKI_PRODUCTS["projects"][1]]},
                    "/api/wiki?p=beta": {"pages": PAGES},
                    "/api/wiki/page?p=beta&n=splash": PAGE,
                },
            }
        )

    def test_the_front_page(self):
        text = self.beta("#/wiki")["text"]
        for want in (
            "Beta · 4 páginas",
            "Cambiadas hace poco",
            "Para empezar",
            "Panorama",  # the general pages (and the pinned ones) as cards
            "Secciones",
            "Motores 3",
            "1 en uso · 1 en prueba",  # how the section's pages stand
        ):
            self.assertIn(want, text)

    def test_a_page(self):
        out = self.beta("#/wiki/page/splash")
        text = out["text"]
        self.assertIn("Wiki / Motores / splash", text)
        self.assertIn("En esta página", text)
        self.assertIn("Uso", text)
        self.assertIn("Detalle", text)
        self.assertIn(
            ["mlx-serve", "#/wiki/page/mlx"], [list(a) for a in out["links"]]
        )  # a [[link]] shows the page's title
        self.assertNotIn("[[nada]]", text)  # a link to no page is plain text, marked
        self.assertIn("nada", text)
        self.assertIn("Fuentes", text)
        self.assertNotIn("# splash", text)


class SystemTest(NextMoreTest):
    MAC: ClassVar[dict] = {
        "mem_free_pct": 47, "mem_total_gb": 32, "swap_gb": 5.3, "swap_total_gb": 6.0, "disk_free_gb": 245,
        "disk_total_gb": 460, "load": 2.1, "ncpu": 12, "top": [{"cpu": 137.0, "cmd": "node"}],
    }  # fmt: skip

    def system(self, mac, **extra):
        projects = {
            **self.PROJECTS,
            "mac": mac,
            "quota": [{"kind": "five_hour", "percent_used": 41, "resets_at": None}],
            "errors": [],
            **extra,
        }
        return self.mount({"hash": "#/system", "answers": {"/api/projects": projects, "/api/state?p=alpha": STATE}})

    def test_things_to_look_at_say_what_to_do(self):
        text = self.system(self.MAC)["text"]
        self.assertIn("2 cosas que mirar", text)
        self.assertIn("La swap está casi llena: 5,3 de 6 GB.", text)
        self.assertIn("Bucle no se ha ejecutado a tiempo.", text)
        for want in (
            "Memoria 53 %",
            "Disco libre 245 GB",
            "Lo que más CPU gasta",
            "node",
            "Tu cuota de Claude",
            "41 %",
            "Tareas periódicas",
            "Lo último",
            "Una nota del orquestador",
            "Tus productos",
            "Detalles técnicos",
        ):
            self.assertIn(want, text)

    def test_all_good(self):
        calm = {**self.MAC, "swap_gb": 1.1}
        projects = {
            **self.PROJECTS,
            "mac": calm,
            "quota": [{"kind": "five_hour", "percent_used": 10, "resets_at": None}],
            "errors": [],
        }
        out = self.mount(
            {"hash": "#/system", "answers": {"/api/projects": projects, "/api/state?p=alpha": STATE_QUIET}}
        )
        self.assertIn("Todo bien", out["text"])

    def test_no_data_is_not_all_good(self):
        calm = {**self.MAC, "swap_gb": 1.1}
        quota = [{"kind": "five_hour", "percent_used": 10, "resets_at": None}]
        cases = {
            "no state answer": (
                {**self.PROJECTS, "mac": calm, "quota": quota, "errors": []},
                {"down": ["/api/state?p=alpha"]},
            ),
            "slow state": (
                {**self.PROJECTS, "mac": calm, "quota": quota, "errors": []},
                {"hang": ["/api/state?p=alpha"]},
            ),
            "no Mac": ({**self.PROJECTS, "mac": None, "quota": quota, "errors": []}, {}),
            "no quota": ({**self.PROJECTS, "mac": calm, "quota": [], "errors": []}, {}),
        }
        for name, (projects, extra) in cases.items():
            with self.subTest(name):
                out = self.mount(
                    {
                        "hash": "#/system",
                        "answers": {"/api/projects": projects, "/api/state?p=alpha": STATE_QUIET},
                        **extra,
                    }
                )
                self.assertNotIn("Todo bien", out["text"])
                self.assertIn("Sin datos", out["text"])

    def test_an_old_session_is_a_thing_to_look_at(self):
        text = self.system(
            {**self.MAC, "swap_gb": 1.0},
            sessions=[{"session": "s1", "name": "x", "project": "alpha", "version": "0.60.0", "behind": True}],
            foreman_installed={"version": "0.66.0"},
        )["text"]
        self.assertIn("corre un foreman viejo (0.60.0). /reload-plugins la pone al día.", text)


if __name__ == "__main__":
    unittest.main()
