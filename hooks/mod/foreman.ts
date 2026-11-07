// foreman's hooks module: a thin adapter between Claude Code's native events and foreman's Python.
//
// Each hook runs `<plugin root>/bin/foreman hook mod OP` with $.process.run (an argument vector, no shell; one JSON
// object on stdin, one on stdout) and applies the answer. Projects, state, texts and the minimum Claude Code version
// live in Python only. What each op is sent, what it answers and what this file does with the answer is written once,
// in lib/foreman/hooks_mod.py's docstring; CONTRIBUTING.md «The hooks module» has the architecture, and
// tests/mod/contract.ts the cases both sides are checked against. What holds throughout:
// - No hook throws into the session (each has a `.catch`). A Python call that fails is a status line, one for the
//   plugin (the newest op still failing); only a failed start leaves the module inert.
// - Outside an active project the module is inert: no timer, no other call.
// - No hook awaits $.prompt.submit or $.command.run: the timer fires them (and turn.complete a pass's one retry),
//   since they settle only when the session is idle.
// - One tick at a time; while a pass this module submitted waits or runs, each tick still asks, with `hold`, for the
//   heartbeat alone, so a busy session never looks gone.
// - What the module keeps between events (notices, the version notice, the reviewer's reminder, a pass to retry, the
//   grants it heard the owner give) lives in memory only: a /reload-plugins or a relief loses it, and the next pass
//   says what is still pending (a grant lost is asked again).
// - The one hook that waits on the owner: `tool.call` holds a main-loop call of a [deny.grants] group (a Bash
//   command, or a call of any other tool by the places and commands it names), or a main-loop call that writes Claude Code's settings or the registry, while $.ui.ask waits (one question per group
//   or class of file, at most `ask_ms`); every other call goes straight on.

import type {
  AgentInfo,
  CommandRunResult,
  EngineInterface,
  PromptOrigin,
  Register,
  SessionMeasureInput,
  Timer,
  TurnCompleteInput,
} from 'claude-code'

type Mode = 'inert' | 'active' | 'below'

type Measure = {
  stored: boolean
  log?: string
}

type Tick = {
  marked: boolean
  submit?: string
  log?: string
  notices?: string
  version?: string
  version_owner?: string
  renew?: string
  instructions?: string
  band?: Band
  grants?: boolean
}

// The orchestrator's band as a tick gives it (lib/foreman/band.py).
type Part = { text: string; role: string; prio: number; n?: number }
type Band = { level: string; rows: Part[][] }

// What `hook mod renewed` answers.
type Recorded = {
  recorded: boolean
  log?: string
}

type Status = {
  text: string
}

type Answer = {
  reviewed: boolean
  reminder?: string
}

type Ended = {
  ended: boolean
}

// A pass the module submitted, by its state: `submitting` until $.prompt.submit settles (it waits for the session to
// be idle and settles as the pass's own turn starts, not when it is queued behind a running turn: checked live in
// Claude Code 2.1.316), `live` while its turn runs, `dropped` when it did not reach the model and waits for its one
// retry (`retried` once it had it).
type Pass = {
  text: string
  state: 'submitting' | 'live' | 'dropped'
  retried: boolean
}

type Start = {
  active: boolean
  below?: boolean
  status?: string
  grants?: boolean
}

// What `hook mod grant` answers a check: {} (nothing to ask), a refusal with no question (`deny`, a cooldown), or the
// question about a group, with `live` and `until` when grants.json already holds a grant of it.
type GrantCheck = {
  group?: string
  deny?: string
  live?: boolean
  until?: string
  question?: string
  yes?: string
  no?: string
  header?: string
  ask_ms?: number
  waiting?: string
}

// What `hook mod grant` answers an answer.
type Granted = {
  granted: boolean
  until?: string
  log?: string
  deny?: string
}

// How a question about a group ended: the label chosen (or text typed under "Other"), a rejection by what it means,
// or the module's own time limit.
type Asked = { answer: string } | { rejected: 'dismissed' | 'unavailable' } | { timeout: true }

const TIMEOUT_MS = 30000
// The engine awaits the first session.start before the session's first prompt: a hung Python holds it this long.
const START_TIMEOUT_MS = 10000
// session.end has one short wall-clock bound (1.5 s by default) for the whole chain, which `next.budget` reads: the
// heartbeat's end gets what it leaves less END_MARGIN_MS (room for the run to settle before the cut), no call when
// that is under END_MIN_MS (Python cannot start), and END_TIMEOUT_MS where nothing meters (Infinity).
const END_TIMEOUT_MS = 1200
const END_MARGIN_MS = 100
const END_MIN_MS = 200
// Where a prompt typed by a person comes from (PromptOrigin): the chat reviewer's reminder rides only these.
const PERSON_ORIGINS: readonly string[] = ['composer', 'bridge', 'sdk', 'channel', 'slack-ping', 'unclassified']
const TICK_MS = 60000 // lib/foreman/heartbeat.py TICK_S
// How $.ui.ask fails where nobody can be asked (a -p run, an SDK host with no permission callback): checked live in
// Claude Code 2.1.316. Any other rejection is a dismissal.
const NO_ASK = 'no tool named "AskUserQuestion"'

// What the session is for foreman, set at each session.start: the other hooks act only when 'active'.
let mode: Mode = 'inert'
// The session's folder, from session.start: Python finds the project by it.
let cwd = ''
// The Claude Code version, from session.start, and whether a main-loop turn runs: both go into each heartbeat.
let claudeCode = ''
let busy = false
// The timer of the loop pass (active sessions only), the tick under way, and the last pass submitted.
let timer: Timer | null = null
let ticking = false
let pass: Pass | null = null
// Whether the last tick found this session marked (null until a tick answers): an unmarked one asks no review.
let marked: boolean | null = null
// What waits for the session's next own tool call or prompt: in-between notices, a version notice for the model and
// its owner's line; and the chat reviewer's reminder for the next prompt a person types.
let notices: string[] = []
let version: string | null = null
let versionOwner: string | null = null
let reminder: string | null = null
// The orchestrator's band, from the last tick of the marked session; null: no band.
let band: Band | null = null
// The session id the last start saw, and its `isInteractive`: a tick under another id starts again with them.
let seen: string | null = null
let interactive = false
// Bumped when the module drops a conversation's state (each start, a /clear's or resume's session.end): an answer
// that was asked before it belongs to the old conversation and is not applied.
let epoch = 0
// The ops whose last call failed, each with its status line, the newest last: the line shows the newest.
const failures = new Map<string, string>()
// Whether the project has [deny.grants] groups (start's `grants`, then each tick's): only then is a main-loop call
// checked (Bash, or any other tool but the module's own question) (besides `permissions apply … --opens`, opensPermissions). A tick refreshes it, so a group added to the project's toml while the session runs counts.
let hasGrants = false
// The grants this module heard the owner give in this conversation, by group: the `until` Python answered. A live
// grant of grants.json lets a call on only when it is one of these, so a file written by hand is asked again.
const granted = new Map<string, string>()
// The groups whose question is open: asked by a call that waits for it, or still drawn after its time limit refused
// the call (the engine cannot close it). One question per group: another call of the group meanwhile is refused.
const asking = new Set<string>()

function firstLine(text: string): string {
  return (text.trim().split('\n')[0] ?? '').slice(0, 200)
}

// The band as a tick gives it (null: none); a change asks the surfaces to draw the band again.
function setBand($: EngineInterface, next: Band | null): void {
  if (JSON.stringify(next) === JSON.stringify(band)) {
    return
  }
  band = next
  $.ui.invalidate('ui.render')
}

function isPart(x: unknown): x is Part {
  const p = x as Part
  return typeof x === 'object' && x !== null && typeof p.text === 'string' && typeof p.prio === 'number'
}

// The band when `x` has its shape: a level and a non-empty list of rows, each a list of parts with a text and a prio.
function bandOf(x: unknown): Band | null {
  const b = x as Band
  if (typeof x !== 'object' || x === null || typeof b.level !== 'string' || !Array.isArray(b.rows)) {
    return null
  }
  const ok = b.rows.length > 0 && b.rows.every(r => Array.isArray(r) && r.every(isPart))
  return ok ? { level: b.level, rows: b.rows } : null
}

// The band's separators: « · » between parts, one space after the dot.
const SEP = ' · '
const AFTER_DOT = ' '
// Columns the band leaves free at its right end (a dot drawn two cells wide by a terminal that counts it so).
const BAND_MARGIN = 1
// The dot's colour by the band's level, and each role's style; all theme keys, so the band follows the theme.
const LEVEL_COLOR: Record<string, string> = { ok: 'success', ask: 'warning', bad: 'error' }
const ROLE_STYLE: Record<string, { color?: string; bold?: boolean; dimColor?: boolean }> = {
  ask: { color: 'warning', bold: true },
  warn: { color: 'warning' },
  bad: { color: 'error' },
  plain: {},
  dim: { dimColor: true },
  more: { dimColor: true },
}

// A text's width in cells: its code points (the texts come in NFC, the dot takes one cell).
function cells(text: string): number {
  return [...text].length
}

// One part as drawn, with the style of its role: a `more` part's text with its number.
type Drawn = { text: string; role: string }

// The parts of `row` that fit in `columns` (fit), in their order: those of prio 0 always; then, by prio from the
// lowest up (ties in their order), each next one while the row still fits, stopping at the first that does not.
// A `more` part counts its `n` plus the other parts of its row left out; at 0 it is not drawn. When the parts of
// prio 0 alone do not fit, the row is drawn whole and its Text cuts the end.
function fit(row: Part[], columns: number): Drawn[] {
  const drawn = (kept: Set<number>): Drawn[] => {
    const left = row.filter((p, i) => !kept.has(i) && p.role !== 'more').length
    return row.flatMap((p, i) => {
      if (!kept.has(i)) {
        return []
      }
      if (p.role !== 'more') {
        return [{ text: p.text, role: p.role }]
      }
      const n = (p.n ?? 0) + left
      return n > 0 ? [{ text: p.text.replace('{n}', String(n)), role: p.role }] : []
    })
  }
  const kept = new Set(row.flatMap((p, i) => (p.prio <= 0 ? [i] : [])))
  const order = row
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.prio > 0)
    .sort((a, b) => a.p.prio - b.p.prio || a.i - b.i)
  for (const { i } of order) {
    kept.add(i)
    if (width(drawn(kept)) > columns) {
      kept.delete(i)
      break
    }
  }
  return drawn(kept)
}

// A drawn row's width in cells, its separators included.
function width(parts: Drawn[]): number {
  return parts.reduce((sum, p, i) => sum + cells(p.text) + (i === 0 ? 0 : cells(gap(parts[i - 1]!))), 0)
}

// What goes between a part and the next one: one space after the dot, « · » otherwise.
function gap(before: Drawn): string {
  return before.role === 'dot' ? AFTER_DOT : SEP
}

async function ask(
  $: EngineInterface,
  op: string,
  input: Record<string, unknown>,
  timeoutMs: number = TIMEOUT_MS,
): Promise<unknown> {
  const run = await $.process.run([`${$.plugin.root}/bin/foreman`, 'hook', 'mod', op], {
    stdin: JSON.stringify(input),
    timeoutMs,
  })
  if (run.exitCode !== 0) {
    throw new Error(`exit ${run.exitCode}${run.stderr.trim() ? `: ${firstLine(run.stderr)}` : ''}`)
  }
  return JSON.parse(run.stdout)
}

// `ask` for an answer that is an object whose `key` is of `type`; anything else throws.
async function askFor<T>(
  $: EngineInterface,
  op: string,
  input: Record<string, unknown>,
  key: keyof T & string,
  type: 'boolean' | 'string',
  timeoutMs: number = TIMEOUT_MS,
): Promise<T> {
  const answer = await ask($, op, input, timeoutMs)
  if (typeof answer !== 'object' || answer === null || typeof (answer as Record<string, unknown>)[key] !== type) {
    throw new Error(`an answer without "${key}"`)
  }
  return answer as T
}

// A `log` line from an answer, when it has one.
function logOf($: EngineInterface, line: unknown): void {
  if (typeof line === 'string' && line) {
    $.ui.log(line)
  }
}

function failed($: EngineInterface, op: string, err: unknown): string {
  const why = firstLine(err instanceof Error ? err.message : String(err))
  const own = `${$.plugin.name}: `
  return `${$.plugin.name}: hook mod ${op} failed (${why.startsWith(own) ? why.slice(own.length) : why})`
}

// `op` failed: its line becomes the newest and the status line shows it.
function fail($: EngineInterface, op: string, err: unknown): void {
  const line = failed($, op, err)
  failures.delete(op)
  failures.set(op, line)
  $.ui.status(line)
}

// `op` worked: its failure, if it had one, goes, and the line shows the newest op still failing, or nothing.
function recovered($: EngineInterface, op: string): void {
  if (failures.delete(op)) {
    $.ui.status([...failures.values()].pop())
  }
}

// What the module keeps for one conversation, dropped at each start and when the session id changes.
function forget($: EngineInterface): void {
  epoch++
  pass = null
  marked = null
  busy = false
  notices = []
  version = null
  versionOwner = null
  reminder = null
  granted.clear() // a grant is the old conversation's (Python ties it to the session); an open question stays drawn
  setBand($, null)
}

// `previous`: the session id this module ran under until now (a tick found another one); Python moves the mark.
async function start($: EngineInterface, folder: string, isInteractive: boolean, previous?: string): Promise<void> {
  mode = 'inert'
  cwd = folder
  interactive = isInteractive
  hasGrants = false
  timer?.cancel()
  timer = null
  claudeCode = ''
  forget($)
  if (failures.size) {
    failures.clear() // what failed belongs to the environment before this start
    $.ui.status(undefined)
  }
  try {
    const [session, cc] = await Promise.all([$.session.id(), $.session.version()])
    seen = session
    claudeCode = cc.base ?? cc.version
    const input: Record<string, unknown> = {
      cwd: folder,
      session,
      claude_code: claudeCode,
      root: $.plugin.root,
      interactive: isInteractive,
    }
    if (previous !== undefined) {
      input.previous = previous
    }
    const answer = await askFor<Start>($, 'start', input, 'active', 'boolean', START_TIMEOUT_MS)
    if (!answer.active) {
      return
    }
    if (answer.below) {
      mode = 'below'
      if (answer.status) {
        $.ui.status(answer.status)
      }
      return
    }
    mode = 'active'
    hasGrants = answer.grants === true
    timer = $.clock.every(TICK_MS, () => {
      void tick($)
    })
  } catch (err) {
    mode = 'inert'
    fail($, 'start', err)
  }
}

async function measure($: EngineInterface, e: SessionMeasureInput): Promise<void> {
  try {
    const input = {
      cwd,
      session: await $.session.id(),
      context: e.context,
      rate_limits: e.rateLimits,
      cost: e.cost ?? null,
    }
    const answer = await askFor<Measure>($, 'measure', input, 'stored', 'boolean')
    recovered($, 'measure')
    logOf($, answer.log)
  } catch (err) {
    fail($, 'measure', err)
  }
}

async function submit($: EngineInterface, p: Pass): Promise<void> {
  p.state = 'submitting'
  try {
    const result = await $.prompt.submit({ text: p.text })
    p.state = result.drop === undefined ? 'live' : 'dropped'
  } catch {
    p.state = 'dropped'
  }
}

// The statuses of this session's subagents ($.agent.list()), or null when the engine cannot tell.
async function agentStatuses($: EngineInterface): Promise<{ id: string; status: AgentInfo['status'] }[] | null> {
  try {
    return (await $.agent.list()).map(a => ({ id: a.id, status: a.status }))
  } catch {
    return null
  }
}

async function tick($: EngineInterface): Promise<void> {
  if (mode !== 'active' || ticking) {
    return
  }
  // A submitted pass that waits or runs: the tick asks for the heartbeat alone.
  const hold = pass?.state === 'submitting' || pass?.state === 'live'
  const at = epoch
  ticking = true
  try {
    const session = await $.session.id()
    if (seen !== null && session !== seen) {
      // A new conversation (/clear, resume): start again under its id; start() catches its own errors.
      await start($, cwd, interactive, seen)
      return
    }
    const input: Record<string, unknown> = { cwd, session, busy, claude_code: claudeCode }
    if (hold) {
      input.hold = true
    }
    if (marked !== false) {
      const agents = await agentStatuses($)
      if (agents !== null) {
        input.agents = agents
      }
    }
    const answer = await askFor<Tick>($, 'tick', input, 'marked', 'boolean')
    recovered($, 'tick')
    if (epoch === at && typeof answer.grants === 'boolean') {
      hasGrants = answer.grants // held or not: a group added or taken away while the session runs
    }
    if (hold || epoch !== at) {
      return // held, or asked for a conversation the module has dropped since
    }
    marked = answer.marked
    if (typeof answer.version === 'string' && answer.version) {
      version = answer.version
      versionOwner = typeof answer.version_owner === 'string' && answer.version_owner ? answer.version_owner : null
    }
    if (!answer.marked) {
      pass = null // relieved: a pass still waiting for its retry is no longer this session's
      notices = []
      setBand($, null)
      return
    }
    setBand($, bandOf(answer.band))
    if (typeof answer.notices === 'string' && answer.notices && !notices.includes(answer.notices)) {
      notices.push(answer.notices)
    }
    logOf($, answer.log)
    if (typeof answer.renew === 'string' && answer.renew) {
      renew($, answer.renew, typeof answer.instructions === 'string' ? answer.instructions : '')
      return
    }
    if (typeof answer.submit === 'string' && answer.submit) {
      notices = [] // the pass says what is there
      pass = { text: answer.submit, state: 'submitting', retried: false }
      // Not awaited: the submission settles only when the session is idle, and the ticks meanwhile must go on
      // writing the heartbeat (held while the pass is `submitting`). submit() catches its own errors.
      void submit($, pass)
    }
  } catch (err) {
    setBand($, null)
    fail($, 'tick', err)
  } finally {
    ticking = false
  }
}

// A slash command from the timer, never awaited by a hook: `settled` gets the text it resolved with, or how it failed
// (rejected, or threw before it started) with the error's first line.
type Settled = { text: string | undefined } | { how: 'rejected' | 'threw'; error: string }

function detached($: EngineInterface, run: { command: string; args?: string }, settled: (s: Settled) => void): void {
  const why = (err: unknown): string => firstLine(err instanceof Error ? err.message : String(err))
  try {
    void $.command.run(run).then(
      r => settled({ text: r.text }),
      err => settled({ how: 'rejected', error: why(err) }),
    )
  } catch (err) {
    settled({ how: 'threw', error: why(err) })
  }
}

// `/compact <instructions>`: the engine queues it until the session is idle and runs its own compaction. Whatever it
// settles with goes to Python, which records it (the compaction's SessionStart says whether it ran).
function renew($: EngineInterface, id: string, instructions: string): void {
  detached($, { command: 'compact', args: instructions }, s => {
    const input: Record<string, unknown> = { id, text: 'how' in s ? '' : (s.text ?? '') }
    if ('how' in s) {
      input.error = s.error || s.how
    }
    void record($, 'renewed', input)
  })
}

// How a detached command settled, to `hook mod renewed`; the answer's `log` is shown.
async function record($: EngineInterface, op: 'renewed', fields: Record<string, unknown>): Promise<void> {
  try {
    const input = { cwd, session: await $.session.id(), ...fields }
    const answer = await askFor<Recorded>($, op, input, 'recorded', 'boolean')
    recovered($, op)
    logOf($, answer.log)
  } catch (err) {
    fail($, op, err)
  }
}

// The end of a main-loop turn: the end of a submitted pass's own turn, or the moment to retry one that was dropped.
// A turn that ends while the pass still waits to be submitted is someone else's. The retry is not awaited: the
// submission waits for the session to be idle, which this turn's end is part of. No retry while a tick runs: that
// tick was not held and may submit a new pass, so the dropped one waits for the next turn.complete (or is replaced).
function turnEnded($: EngineInterface, e: TurnCompleteInput): void {
  const p = pass
  if (p === null || p.state === 'submitting') {
    return
  }
  if (p.state === 'live') {
    if (e.isAborted) {
      p.state = 'dropped' // interrupted (Esc): retried at the next turn.complete
    } else {
      pass = null
    }
    return
  }
  if (ticking) {
    return
  }
  if (!p.retried) {
    p.retried = true
    void submit($, p)
    return
  }
  pass = null
}

// What the model gets with the session's next own tool result or prompt (once): the kept notices, then the version
// notice, then (for a prompt a person typed) the chat reviewer's reminder. The version's owner line goes to the log.
function take($: EngineInterface, person: boolean): string[] {
  const out: string[] = []
  if (notices.length) {
    out.push(notices.join(' '))
    notices = []
  }
  if (version !== null) {
    out.push(version)
    if (versionOwner !== null) {
      $.ui.log(versionOwner)
    }
    version = null
    versionOwner = null
  }
  if (person && reminder !== null) {
    out.push(reminder)
    reminder = null
  }
  return out
}

function byPerson(origin: PromptOrigin | undefined): boolean {
  return origin === undefined || PERSON_ORIGINS.includes(origin.kind)
}

// The chat reviewer: the answer of a main-loop turn to `hook mod answer`; its reminder (or none) replaces the kept one.
async function review($: EngineInterface, e: TurnCompleteInput): Promise<void> {
  if (marked === false || !e.answer.trim()) {
    return
  }
  const at = epoch
  try {
    const input = { cwd, session: await $.session.id(), turn: e.turnId, answer: e.answer }
    const answer = await askFor<Answer>($, 'answer', input, 'reviewed', 'boolean')
    recovered($, 'answer')
    if (answer.reviewed && epoch === at) {
      reminder = typeof answer.reminder === 'string' && answer.reminder ? answer.reminder : null
    }
  } catch (err) {
    fail($, 'answer', err)
  }
}

// How long `hook mod end` may take, from what the session.end bound leaves (`next.budget.remainingMs`); null: no call.
// Exported for end.test.ts: the test kit cannot cut a dispatch as the engine does.
export function endTimeout(remainingMs: number): number | null {
  if (!Number.isFinite(remainingMs)) {
    return END_TIMEOUT_MS
  }
  const ms = Math.floor(remainingMs) - END_MARGIN_MS
  return ms < END_MIN_MS ? null : ms
}

// The session ends (closed, /clear, another took its place): its heartbeat is marked ended, within the event's
// short bound. After a /clear or a resume the process goes on: forget() ran first, and the next tick starts again.
// A failure shows nothing: the session is going, and its ticks stopping says the same two minutes on.
async function ended($: EngineInterface, session: string, timeoutMs: number): Promise<void> {
  try {
    await askFor<Ended>($, 'end', { cwd, session }, 'ended', 'boolean', timeoutMs)
  } catch {
    // nothing to show
  }
}

// `/foreman:status`: the summary from `hook mod status`, or the failure, as the command's output.
async function status($: EngineInterface): Promise<CommandRunResult> {
  try {
    const answer = await askFor<Status>($, 'status', { cwd, session: await $.session.id() }, 'text', 'string')
    return { text: answer.text }
  } catch (err) {
    return { text: failed($, 'status', err) }
  }
}

// What a question's end means to `hook mod grant`: only the yes label is a yes; any other label or text is a no; a
// rejection is not a no (Esc, «Chat about this», a host that refuses: dismissed; nobody to ask: unavailable).
function meaning(asked: Asked, yes: string): string {
  if ('answer' in asked) {
    return asked.answer === yes ? 'yes' : 'no'
  }
  return 'timeout' in asked ? 'timeout' : asked.rejected
}

// `hook mod grant`'s answer phase: what it answered applied (a grant kept in memory, its `log` shown). Resolves to the
// refusal for the call, or null when it may go on (the classic guard still decides; it finds the grant Python wrote).
async function answered($: EngineInterface, group: string, input: Record<string, unknown>): Promise<string | null> {
  try {
    const answer = await askFor<Granted>($, 'grant', input, 'granted', 'boolean')
    recovered($, 'grant')
    logOf($, answer.log)
    if (answer.granted) {
      if (typeof answer.until === 'string') {
        granted.set(group, answer.until)
      }
      return null
    }
    return typeof answer.deny === 'string' && answer.deny ? answer.deny : null
  } catch (err) {
    fail($, 'grant', err)
    return null
  }
}

// A main-loop call in a project with [deny.grants] groups: `hook mod grant` checks it (a Bash call by its
// `command`; any other tool by its `input`, the event without its envelope, which Python reads for the places and
// commands it names, as the classic guard does) and, when the one thing it lacks is the owner's yes, the owner is asked with $.ui.ask, the call waiting (the engine sets no time limit; the
// module's is `ask_ms`). Resolves to the refusal for the call, or null when it may go on: then the classic guard
// still decides, so a failure here leaves it to the guard, which refuses a group's command with no grant.
// The same for a main-loop call in any session of the project that writes Claude Code's settings or the registry
// (a file tool's `path`, or a Bash command): the group is `file:<class>`, and Python answers no `until` (a yes is
// for that one call, which the classic guard lets through once), so nothing is kept in `granted`.
// `foreman permissions apply … --opens` and `foreman integrate … --opens` (a branch that changes a live foreman.toml or
// guard table so that it allows something new) need the owner's yes in every product (foreman's own group
// `permissions`, grants.INTERNAL), so such a call is checked even when the product has no groups. A loose test of the
// text, without quotes and backslashes: Python decides.
function opensPermissions(command: string): boolean {
  const flat = command.replace(/\\\n|[\\'"]/g, '')
  if (!flat.includes('--opens')) {
    return false
  }
  return (flat.includes('permissions') && flat.includes('apply')) || flat.includes('integrate')
}

// The tools whose call names the file it writes (guard.FILE_TOOLS), and the file it names.
const FILE_TOOLS: readonly string[] = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit']

function filePath(e: Record<string, unknown>): string {
  const path = e.file_path ?? e.notebook_path
  return typeof path === 'string' ? path : ''
}

// The keys of a tool.call event that are not the tool's input (the tool's arguments ride beside them).
const NOT_INPUT: readonly string[] = ['tool', 'tool_use_id', 'agentId', 'requestMeta']

// The tool's input of a call: the event without its envelope.
function toolInput(e: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(e).filter(([k]) => !NOT_INPUT.includes(k)))
}

// The tool $.ui.ask's question runs as: the module's own question, never checked.
const ASK_TOOL = 'AskUserQuestion'

// A file the owner may let one main-loop call write (Claude Code's settings, the registry: guard.ASK_KINDS), as
// text: a loose test that only decides whether to ask Python; a path that reaches one otherwise is not asked about,
// and the classic guard refuses it.
function namesAskable(text: string): boolean {
  return text.includes('.claude') || text.includes('.config/foreman') || text.includes('projects.toml')
}

async function grantFor($: EngineInterface, what: Record<string, unknown>, toolUseId: string): Promise<string | null> {
  const session = await $.session.id()
  const call = { cwd, session, ...what, tool_use_id: toolUseId }
  let q: GrantCheck
  try {
    const answer = await ask($, 'grant', { phase: 'check', ...call })
    if (typeof answer !== 'object' || answer === null) {
      throw new Error('an answer that is not an object')
    }
    q = answer as GrantCheck
    recovered($, 'grant')
  } catch (err) {
    fail($, 'grant', err)
    return null
  }
  const group = q.group
  if (typeof group !== 'string' || !group) {
    return null // nothing to ask: the guard decides alone
  }
  if (typeof q.deny === 'string' && q.deny) {
    return q.deny // after the owner's no, the group cools down: no question
  }
  if (q.live === true && typeof q.until === 'string' && granted.get(group) === q.until) {
    return null // the grant this module heard given
  }
  granted.delete(group) // gone from grants.json (ended, taken away) or never heard here: ask
  const { question, yes, no } = q
  if (typeof question !== 'string' || typeof yes !== 'string' || typeof no !== 'string') {
    fail($, 'grant', new Error('an answer without "question"'))
    return null
  }
  if (asking.has(group)) {
    return typeof q.waiting === 'string' && q.waiting ? q.waiting : null
  }
  asking.add(group)
  const surfaces = [...(await $.session.surfaces())]
  const options = typeof q.header === 'string' && q.header ? { options: [yes, no], header: q.header } : { options: [yes, no] }
  const asked: Promise<Asked> = $.ui.ask(question, options).then(
    (answer): Asked => ({ answer }),
    (err: unknown): Asked => {
      const why = err instanceof Error ? err.message : String(err)
      return { rejected: why.includes(NO_ASK) ? 'unavailable' : 'dismissed' }
    },
  )
  const ms = typeof q.ask_ms === 'number' && q.ask_ms > 0 ? q.ask_ms : 0
  let outcome: Asked
  if (ms) {
    let expire: (a: Asked) => void = () => {}
    const timeout = new Promise<Asked>(resolve => {
      expire = resolve
    })
    const timer = $.clock.after(ms, () => expire({ timeout: true }))
    outcome = await Promise.race([asked, timeout])
    timer.cancel()
  } else {
    outcome = await asked
  }
  const input = { phase: 'answer', ...call, group, surfaces }
  if ('timeout' in outcome) {
    // The question stays drawn and its answer still comes, marked late: Python applies a yes or a no to the request
    // this question left pending (only for the marked session, in its own process) and records anything else. It
    // goes with the session's id at answer time: after a /clear the mark and the request moved to the new id.
    // Until then the group's question is open, so no other one piles up behind it.
    void asked.then(async late => {
      asking.delete(group)
      let now = session
      try {
        now = await $.session.id()
      } catch {
        // the id it asked under; Python also takes it for a request carried from it
      }
      void answered($, group, { ...input, session: now, answer: meaning(late, yes), late: true })
    })
  } else {
    asking.delete(group)
  }
  return answered($, group, { ...input, answer: meaning(outcome, yes) })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await start($, e.cwd, e.isInteractive === true)
    return result
  }).catch(($, e, next) => next(e))

  on('session.measure', async ($, e, next) => {
    const result = await next(e)
    if (mode === 'active' && e.changed.includes('context')) {
      await measure($, e)
    }
    return result
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    if (mode === 'active' && e.agentId === undefined) {
      busy = true
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (mode === 'active' && e.agentId === undefined) {
      busy = false
      turnEnded($, e)
      await review($, e)
    }
    return result
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    if (mode === 'active' && e.agentId === undefined) {
      let what: Record<string, unknown> | null = null
      if (e.tool === 'Bash') {
        const command = String(e.command)
        if (hasGrants || opensPermissions(command) || namesAskable(command)) {
          what = { command }
        }
      } else if (e.tool !== ASK_TOOL) {
        const path = FILE_TOOLS.includes(e.tool) ? filePath(e as Record<string, unknown>) : ''
        if (path && namesAskable(path)) {
          what = { tool: e.tool, path }
        } else if (hasGrants) {
          what = { tool: e.tool, input: toolInput(e as Record<string, unknown>) }
        }
      }
      const deny = what === null ? null : await grantFor($, what, e.tool_use_id)
      if (deny !== null) {
        return { deny }
      }
    }
    const result = await next(e)
    if (mode !== 'active' || e.agentId !== undefined || result.deny !== undefined) {
      return result
    }
    const extra = take($, false)
    return extra.length ? { ...result, context: [...(result.context ?? []), ...extra] } : result
  }).catch(($, e, next) => next(e))

  on('prompt.submit', async ($, e, next) => {
    if (mode !== 'active' || e.command !== undefined) {
      return next(e)
    }
    const extra = take($, byPerson(e.origin))
    return next(extra.length ? { ...e, context: [...(e.context ?? []), ...extra] } : e)
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const shown = band
    if (mode !== 'active' || shown === null || e.props.hasSurvey) {
      return next(e)
    }
    const { Box, Text } = $.ui.resolve(e)
    const columns = e.props.bodyColumns - BAND_MARGIN
    const style = (p: Drawn) =>
      p.role === 'dot' ? { color: LEVEL_COLOR[shown.level] ?? 'error' } : (ROLE_STYLE[p.role] ?? {})
    return Box({
      flexDirection: 'column',
      children: shown.rows.map((row, n) =>
        Text({
          wrap: 'truncate-end',
          children: fit(row, columns).flatMap((p, i, all) => [
            ...(i === 0 ? [] : [Text({ dimColor: true, children: gap(all[i - 1]!) })]),
            Text({ ...style(p), children: p.text }),
          ]),
        }),
      ),
    })
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'foreman:status' }, async $ => status($)).catch(($, e, next) => next(e))

  // The hook only observes: the engine's end step (and the hooks beneath) run beside the heartbeat's end, not after
  // it, so a slow Python start never eats their share of the one bound.
  on('session.end', async ($, e, next) => {
    if (mode !== 'active') {
      return next(e)
    }
    if (e.reason === 'clear' || e.reason === 'resume') {
      forget($) // the old conversation's state must not reach the new one's first prompt or turn
    }
    const timeoutMs = endTimeout(next.budget.remainingMs) // read before next(e): the bound's clock never stops
    const [result] = await Promise.all([next(e), timeoutMs === null ? null : ended($, e.sessionId, timeoutMs)])
    return result
  }).catch(($, e, next) => next(e))
}
