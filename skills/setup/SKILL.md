---
name: setup
description: Guided first setup of foreman in someone's own project, for a person who has never used it - a short interview in their language, then foreman's files, the web panel, a plain-words health check and a first guided orchestrator pass. Use it when the person types /foreman:setup, or asks to start, install or configure foreman for a project.
---
# Setting foreman up with a new person (the owner)

You run this in the main session, talking with the person who will own the project in foreman (the owner). They
know their project, not foreman, and have not read its documentation: the whole point is that they never need to.

## How to talk

- **Their language.** Speak the language the person writes in, whatever it is. What foreman itself writes (its
  files, pages, panel, notices) stays in English, the only exception being Spanish, which foreman also writes; say
  so once if it comes up. Translating foreman is not part of this.
- **Plain words**, as `foreman docs owner` says (docs/owner.md): no ids, no config keys, no paths or internal names
  unless they must type or open them, and then with what they are. Say «the panel», «the list of work», «a check
  that the code works», «the session that runs your project» — never «worktree», «local_ci», «registry», «hook»,
  «backlog.json».
- **One question at a time**, each with your proposal already filled in, so the answer can be «yes». Six questions
  at most. Never ask what you can find out yourself.
- **Nothing written without a yes.** Show what will be made, in plain words, then make it.
- **Commands are yours to run.** The person types only what you ask in so many words (a slash command, opening a
  session, clicking in the panel), and you always say what it does.

## 0. Where are we

Run these yourself before saying anything beyond a greeting:

1. `foreman --version`. If it fails, foreman's plugin is not installed or not loaded in this session: say so, ask
   them to install it (or to type `/reload-plugins` if they just did) and stop here.
2. `foreman config --projects`: the projects foreman already knows on this computer.
3. Look at the current folder: `git rev-parse --show-toplevel`, `git remote -v`, `git branch --show-current`, and
   the files that tell how the code is checked (`package.json` scripts, `Makefile`, `pyproject.toml`, `Cargo.toml`,
   `go.mod`, a CI file under `.github/workflows/`…).

Then pick the part to do:

- **This folder is the foreman folder of a project foreman knows** (a `foreman.toml` here, and this folder is a
  project's home in `foreman config --projects`, which prints name, mode and home): if the setup did not happen earlier
  in this same conversation, go to **5. First guided pass**; if it did, go to **4. Health check** and then ask for a
  new session (step 5).
- **A project foreman knows has its home inside this folder** (say, `ops/`): tell them it is already set up and
  that the next step happens in a session opened in that folder (step 5.1).
- **Otherwise:** start with **1. Interview**.

## 1. Interview

Ask in this order, one per message, each with your proposal (what you found in step 0):

1. **Name and owner.** «What do you call this project, and who decides about it?» Propose the repository's folder
   name and their name. The name foreman uses is lowercase letters, digits and `-` (make it from theirs; tell them
   only if it changes).
2. **Where the code is.** «Is the code in this folder, or in several?» Propose the repository of the current
   folder. Several repositories are fine: one `--repo` each, with a short lowercase key.
3. **Where foreman keeps its own things** (the list of work, notes, reports). Propose a folder `ops` inside the
   repository when there is one repository, or a folder beside them when there are several. Say that the agents'
   working copies always go outside the code, by themselves.
4. **How we know the code works.** «Which command tells you the code is fine?» Propose what you found (`npm test`,
   `make test`, `pytest`…). «None yet» is a valid answer: say that agents will then only be able to check by hand.
5. **Pace.** «How many tasks at once, and how often should I check on the work?» Propose 1 task at a time and a
   check every 60 minutes.
6. **The panel.** «Do you want a page in your browser to follow the work and answer questions?» Propose yes, open
   only from this computer.

No git repository at all: say that foreman's agents work on git branches, offer to stop here so they can make one
(`git init`), and go on only if they want to.

## 2. Show, confirm, make

Build the command from the answers (home = the folder of question 3; one `--repo KEY=PATH` per repository; the
check command with `--local-ci`, `KEY=CMD` when there are several repositories; `--slots` and `--every-min` only
when they differ from 1 and 60; `--language es` for Spanish speakers, else `--language en`; always `--sample`):

```
foreman init HOME --name NAME --owner "OWNER" --language en --repo KEY=PATH --local-ci "CMD" --sample --dry-run
```

Run it with `--dry-run` first and summarise it in plain words, for example: «I will create a folder `ops` in your
project with foreman's settings, a short page of rules for the agents (yours to edit), an empty list of work with
one example task, and add the project to foreman's list on this computer. Nothing else changes.» Ask for a yes.

With the yes, run the same command without `--dry-run`. If it exits with 2, it wrote nothing: read its message,
fix the cause (often a name already used, or a folder that already has different settings), explain it in one
sentence and ask again. Never edit foreman's list of projects by hand.

Tell them the rules page is theirs: what the product is, and what must never happen without them. Offer to fill in
the first part with them now, in a few sentences.

## 3. Panel (only if they said yes)

1. `foreman panel service status`. If a panel service is already installed and running, keep it: the new project
   appears in it by itself.
2. Otherwise, `foreman panel service install` (macOS or Linux). Its default listens on 127.0.0.1 only, reachable
   from this computer alone; never add `--host 0.0.0.0` unless they ask to open it from their phone, and then say
   that anyone on their network who has the key can see it.
3. Give them the address with its key: `http://127.0.0.1:PORT/?t=KEY`, the port from the status or install output
   (8766 by default) and the key read from the file the install names (`~/.local/state/foreman/panel/token` by
   default). Offer to open it (`open URL` on macOS, `xdg-open URL` on Linux). Never write the key into any file.
4. Ask them to keep the page open: the first pass will use it.

## 4. Health check, in plain words

Run `foreman --project NAME check` (the init output already ends with it). Lines that end in `ok` need nothing; say
«everything checks out» if all do. Translate every other line into one sentence and what to do:

| the line says | tell them |
|---|---|
| `claude code … below the minimum, run \`claude update\`` | Claude Code is older than foreman needs; type `claude update` in a terminal, then reopen Claude Code. |
| `claude code not found` / `version unknown` | foreman could not tell which Claude Code runs here; usually harmless, the first pass will show it. |
| `repo KEY: … (not a git checkout)` | that folder is not a git repository; agents cannot work there until it is. |
| `rules …: missing` | the rules page is missing; offer to write it with them. |
| `config schema … N problems` and the lines under it | a setting is wrong; fix it with them (re-run `foreman init` after correcting the answer, or edit that one line), then check again. |
| `backlog …: N problems` | the list of work has an error; read the lines under it and fix it. |
| `backlog …: missing (foreman init …)` | the list of work is not there yet; run the `foreman init` it names (it keeps the settings file and makes only what is missing), then check again. |
| `(registry) …` | foreman's list of projects on this computer has a problem; read it to them, do not edit that file without their yes. |
| `warning: …` | read it, say whether it matters for them now (usually not). |

Exit code 0 means ready. Anything else: fix it before the first pass.

## 5. First guided pass

The session where the setup ran cannot run the project: foreman decides that when a session opens. So:

1. Ask them to **open a new Claude Code session in the project's foreman folder** (the home made in step 2, give the
   exact path) and type `/foreman:setup` there again; this guide picks up here. Say this session can be closed.
2. In the new session, run `foreman start`. It gives this session's id and the exact `foreman session` line that
   makes it the session that runs the project. Run that line with `--name` set to a short name such as
   «NAME orchestrator». Say in plain words what it means: this chat now runs their project; from here on foreman
   sends it a short summary of the work regularly (a pass), and it acts on it.
3. Create the sample question, in the person's language (title, text and options translated), so they can answer it
   in the panel:

   ```
   foreman backlog decision add --title "Did this question reach you?" --text "A sample question from the setup guide, to show where questions for you appear. Any answer is fine: nothing else changes." --option "Yes, I can see it" --option "Something looks wrong" --recommend-option "Yes, I can see it" --recommend "Yes: it only checks that questions reach you."
   ```

4. **The first pass** arrives within a minute of marking the session, as a message that starts with the project's
   loop prompt («Orchestrator loop» by default) followed by a summary. When it comes, do the pass (the
   orchestrator-pass skill) and then tell them in two or three plain sentences what it is: foreman's regular look
   at the project (what runs, what waits for them, what is ready), every 60 minutes or what they chose. If nothing
   arrives in two minutes, run `foreman session`: when it says there are no passes, the session started before
   foreman was loaded; ask them to type `/reload-plugins` and wait one more minute, and meanwhile run `foreman loop`
   to show what a pass contains.
5. **The band.** Point them at the line drawn above the chat box: a coloured dot (green fine, yellow a question
   waits for them, red something is late), the questions waiting, and when the next pass comes. The sample question
   should make the dot yellow.
6. **The panel.** Ask them to open (or reload) the panel page, find the project and answer the sample question
   there. Check with `foreman backlog decision list`: when it no longer lists it, the answer arrived; tell them so,
   and that the band's question count went down.
7. **Close** with what they can do now, in two or three sentences: ask in this chat for a task in their own words
   («add a dark mode to the settings page»), and you will turn it into work for an agent; questions for them appear
   in the panel and in the band; the example task in the list of work can be kept, changed or deleted. To set up
   another project, type `/foreman:setup` in a session opened in that project's folder.
