"""Remote Control on demand for a session agent (remotecontrol.py) and the panel's «see conversation» button: the
panel's data for a session agent, and /api/agent/watch (the token and the project like every write; a session agent
only; the reason of a failure). `claude` is a fake script in PATH that behaves like the real one measured for this
(`claude attach` on a pseudo-terminal: `/remote-control` then Enter shows a link and a dialog that waits for an
Enter), so the code runs for real over a pseudo-terminal."""

import json
import os
import time
from unittest import mock

import test_panel_stuck
from test_panel import PanelCase, transcript

from foreman import identity, remotecontrol

SID = "742e9c3b-2a44-4c55-a33d-abebf1be0bf5"
LINK = "https://claude.ai/code/session_01UsCwUFyumbgvUKJSuJepjd"

# The fake `claude`. State in $FAKE/rc: absent (Remote Control off), `dialog` (on, dialog open) or `on`.
# FAKE_MODE: `stuck` = the first Enter is lost (a second attach has to close the dialog); `never` = no Enter closes it;
# `nolink` = /remote-control shows nothing. The screen paints the link with cursor moves between its parts.
FAKE = r"""#!/bin/sh
state=$(cat "$FAKE/rc" 2>/dev/null)
if [ "$1" = agents ]; then
  w=""
  [ "$state" = dialog ] && w=',"waitingFor":"dialog open"'
  echo '[{"id":"742e9c3b","sessionId":"'$SID'","name":"s2","state":"working"'"$w"'}]'
  exit 0
fi
if [ "$1" = attach ]; then
  echo "$2" >> "$FAKE/attached"
  if [ "$2" != 742e9c3b ]; then echo "No job matching '$2'."; exit 1; fi
  printf '\033[2J\033[H bypass permissions on (shift+tab to cycle)\r\n'
  [ "$state" = dialog ] && printf '%s\r\n%s\r\n' "$LINKTXT" "Enter to  select · Esc to continue"
  while read -r line; do
    n=$(cat "$FAKE/n" 2>/dev/null || echo 0)
    if [ "$line" = /remote-control ]; then
      echo "$line" >> "$FAKE/typed"
      [ "$FAKE_MODE" = nolink ] && continue
      echo dialog > "$FAKE/rc"
      printf '\033[3C%s\033[2C.\r\n%s\r\n' "$LINKTXT" "Enter to select · Esc to  continue"
    elif [ -z "$line" ] && [ "$(cat "$FAKE/rc" 2>/dev/null)" = dialog ]; then
      echo $((n + 1)) > "$FAKE/n"
      [ "$FAKE_MODE" = never ] && continue
      [ "$FAKE_MODE" = stuck ] && [ "$n" = 0 ] && continue
      echo on > "$FAKE/rc"
    fi
  done
fi
"""


class WatchCase(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def setUp(self):
        super().setUp()
        self.fakedir = self.tmp / "fake"
        self.fakedir.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        f = self.bin / "claude"
        f.write_text(FAKE)
        f.chmod(0o755)
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE": str(self.fakedir),
            "SID": SID,
            "LINKTXT": LINK,
            "FAKE_MODE": "",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("SETTLE_S", "KEY_S"):
            p = mock.patch.object(remotecontrol, name, 0.15)
            p.start()
            self.addCleanup(p.stop)
        self.add_agents()

    def add_agents(self):
        f = self.project.state / "agents.json"
        d = json.loads(f.read_text())
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        tr = transcript(self.tmp / "tasks/s2.output", "[foreman launch bcdefghijk] bea2 · PF-2 T1 · branch b\nHaz T1")
        d[SID] = {
            "start": now,
            "type": "foreman:dev",
            "transcript": tr,
            "tool": "Edit",
            "activity": now,
            "name": "bea2",
            "issue": "PF-2",
            "sub": "T1",
            identity.SESSION_MARK: True,
        }
        f.write_text(json.dumps(d))

    def stop_session(self):
        f = self.project.state / "agents.json"
        d = json.loads(f.read_text())
        d[SID]["claude_state"] = "stopped"
        f.write_text(json.dumps(d))

    def watch(self, aid=SID, p="fake", **kw):
        return self.req(f"/api/agent/watch?p={p}&id={aid}", "POST", **kw)

    def rc(self):
        f = self.fakedir / "rc"
        return f.read_text().strip() if f.exists() else ""

    def attached(self):
        f = self.fakedir / "attached"
        return f.read_text().split() if f.exists() else []

    def agents(self):
        return {a["id"]: a for a in self.req("/api/state?p=fake")[1]["project"]["agents"]}

    def entry(self):
        return json.loads((self.project.state / "agents.json").read_text())[SID]


class PanelDataTest(WatchCase):
    def test_a_session_agent_is_marked_and_a_subagent_is_not(self):
        ags = self.agents()
        self.assertIs(ags[SID]["session"], True)
        self.assertIsNone(ags[SID]["link"])
        self.assertIs(ags["a1"]["session"], False)
        self.assertIsNone(ags["a1"]["link"])

    def test_the_link_it_last_gave_is_in_the_data(self):
        self.assertEqual(self.watch()[0], 200)
        self.assertEqual(self.agents()[SID]["link"], LINK)

    def test_a_stopped_session_has_nothing_to_open(self):
        self.stop_session()
        self.assertIs(self.agents()[SID]["session"], False)


class WatchTest(WatchCase):
    def test_turns_remote_control_on_gives_the_link_and_closes_the_dialog(self):
        code, out = self.watch()
        self.assertEqual((code, out), (200, {"link": LINK}))
        self.assertEqual(self.rc(), "on")  # the dialog is closed: the session is not left waiting for an Enter
        self.assertEqual((self.fakedir / "typed").read_text().split(), ["/remote-control"])
        self.assertEqual(self.attached(), [SID[:8]])  # the short id: claude refuses the whole one
        e = self.entry()
        self.assertEqual(e["link"], LINK)
        self.assertRegex(e["link_at"], r"^\d{4}-\d\d-\d\dT")

    def test_asking_again_gives_the_same_link(self):
        self.assertEqual(self.watch()[1], {"link": LINK})
        (self.fakedir / "rc").write_text("on\n")  # connected, as the real one is after the first time
        self.assertEqual(self.watch()[1], {"link": LINK})
        self.assertEqual(self.rc(), "on")

    def test_a_dialog_left_open_by_a_past_try_is_closed_first(self):
        (self.fakedir / "rc").write_text("dialog\n")
        self.assertEqual(self.watch()[1], {"link": LINK})
        self.assertEqual(self.rc(), "on")

    def test_an_enter_that_was_lost_is_given_again(self):
        os.environ["FAKE_MODE"] = "stuck"
        self.assertEqual(self.watch()[1], {"link": LINK})
        self.assertEqual(self.rc(), "on")
        self.assertEqual(len(self.attached()), 2)  # a second attach closed it

    def test_a_dialog_that_stays_open_is_an_error_not_a_silent_wait(self):
        os.environ["FAKE_MODE"] = "never"
        code, out = self.watch()
        self.assertEqual(code, 400)
        self.assertIn("dialog stays open", out["error"])

    def test_no_link_is_an_error(self):
        os.environ["FAKE_MODE"] = "nolink"
        with mock.patch.object(remotecontrol, "LINK_S", 0.6):
            code, out = self.watch()
        self.assertEqual(code, 400)
        self.assertIn("did not give a link", out["error"])
        self.assertNotIn("link", self.entry())

    def test_a_session_that_is_not_running_says_so(self):
        gone = "ffffffff-0000-4000-8000-000000000000"
        f = self.project.state / "agents.json"
        d = json.loads(f.read_text())
        d[gone] = {**d[SID], "name": "gone1"}
        f.write_text(json.dumps(d))
        code, out = self.watch(gone)
        self.assertEqual(code, 400)
        self.assertIn("did not open", out["error"])

    def test_a_subagent_is_not_a_session(self):
        code, out = self.watch("a1")
        self.assertEqual(code, 400)
        self.assertIn("not an agent that runs as a session", out["error"])
        self.assertEqual(self.watch("nope")[0], 400)
        self.assertEqual(self.watch("")[0], 400)
        self.assertEqual(self.attached(), [])  # claude was not even started

    def test_a_stopped_session_is_refused(self):
        self.stop_session()
        code, out = self.watch()
        self.assertEqual(code, 400)
        self.assertIn("stopped", out["error"])
        self.assertEqual(self.attached(), [])

    def test_no_claude_in_path_says_so(self):
        with mock.patch("shutil.which", return_value=None):
            code, out = self.watch()
        self.assertEqual(code, 400)
        self.assertIn("no `claude` command", out["error"])

    def test_a_second_click_while_the_first_waits_is_refused(self):
        lock = remotecontrol._lock(SID)
        lock.acquire()
        self.addCleanup(lock.release)
        code, out = self.watch()
        self.assertEqual(code, 400)
        self.assertIn("already connecting", out["error"])
        self.assertEqual(self.attached(), [])  # nothing was typed to the session

    def test_the_key_and_the_project_rule_like_every_write(self):
        self.assertEqual(self.watch(token=False)[0], 403)
        self.assertEqual(self.watch(p="nope")[0], 403)
        self.assertEqual(self.attached(), [])
        self.assertEqual(self.req(f"/api/agent/watch?p=fake&id={SID}")[0], 404)  # a GET is no write: it does nothing

    def test_only_the_click_turns_it_on(self):
        """Reading the state and the project list never starts claude: Remote Control (and the owner's phone push)
        comes only with the button."""
        self.req("/api/state?p=fake")
        self.req("/api/projects")
        self.assertEqual(self.attached(), [])
        self.assertEqual(self.rc(), "")


class PageTest(WatchCase):
    SETUP = """
const agents = (await (await fetch(api('/api/state?p=fake'))).json()).project.agents.map(a => ({...a, p: 'fake'}));
lastData = {joint: false, ps: [{name: 'fake', writable: true}], agents};
const sid = agents.find(a => a.session).id;
const opened = [];
window.open = () => { const w = {location: {}, close() { opened.push('closed'); }}; opened.push(w); return w; };
"""

    def test_the_button_is_only_on_a_session_agent_and_opens_the_link(self):
        out = self.run_page(
            self.SETUP
            + """
renderWork({agents, notes: [], progress: [], repos: []});
const rows = $('agents').innerHTML;
await watchAgent({dataset: {id: sid, p: 'fake'}, disabled: false, textContent: ''});
return {buttons: (rows.match(/watchAgent/g) || []).length, label: rows.includes('Ver conversación'),
  href: opened[0].location.href, err: $('err').textContent};
""",
            tab="work",
        )
        self.assertEqual((out["buttons"], out["label"]), (1, True))
        self.assertEqual(out["href"], LINK)
        self.assertEqual(out["err"], "")

    def test_a_failure_closes_the_window_and_says_why(self):
        os.environ["FAKE_MODE"] = "never"
        out = self.run_page(
            self.SETUP
            + """
await watchAgent({dataset: {id: sid, p: 'fake'}, disabled: false, textContent: ''});
return {err: $('err').textContent, opened: opened.map(w => typeof w === 'string' ? w : 'window')};
""",
            tab="work",
        )
        self.assertIn("dialog stays open", out["err"])
        self.assertEqual(out["opened"], ["window", "closed"])
