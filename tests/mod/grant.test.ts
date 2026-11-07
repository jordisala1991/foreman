// The owner's yes for a [deny.grants] group: a main-loop Bash call is checked with `hook mod grant`, and when the one
// thing it lacks is that yes, the module asks with $.ui.ask while the call waits. First every `grant` case of
// contract.ts (what the module sends, whether it asks, what it does with the answer); then what lives only in the
// module: the grants it heard given, one question per group, the time limit, a late answer. The test's own
// `tool.call` hook on AskUserQuestion stands for the person (or the host) answering the question. No process runs.
import { expect, mock, test } from 'claude-code/testing'
import cases from './contract.ts'

type Run = { exitCode?: number; stdout?: string; stderr?: string; reject?: string }
type Ask = { answer?: string; reject?: string; timeout?: boolean }

type Case = {
  name: string
  op: string
  python: boolean
  engine: {
    cwd: string
    session: string
    version: string
    start: string
    command?: string
    tool?: string
    file_path?: string
    input?: Record<string, unknown>
    tool_use_id: string
    surfaces: string[]
    agentId?: string
    check?: string
    ask?: Ask
  }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

type On = Parameters<Parameters<typeof test>[1]>[1]
type Input = Record<string, unknown>
type Question = { question: string; header: string; options: { label: string }[] }

const LAUNCHER = '/bin/foreman'
const ACTIVE = '{"active": true, "below": false, "grants": true, "min": "2.1.299"}\n'
const REFUSED = '{"deny": "refused", "granted": false}\n'
const DEPLOY = 'make deploy'
const ASK_MS = 300000
const TICK_MS = 60000
const TICKED = '{"marked": true}\n'
const UNTIL = '2026-11-01T10:30:00'
const GRANTED = JSON.stringify({ granted: true, log: '«deploy» allowed for orq until 10:30', until: UNTIL })
const WAITING = 'Blocked: a question about the group «deploy» is already waiting for Owner.'

function reply(run: Run) {
  if (run.reject !== undefined) {
    return { deny: run.reject }
  }
  return {
    value: {
      exitCode: run.exitCode ?? 0,
      stdout: run.stdout ?? '',
      stderr: run.stderr ?? '',
      isStdoutTruncated: false,
      isStderrTruncated: false,
    },
  }
}

// What the check answers about the group: the question (`live` and `until` when grants.json holds a grant).
function question(ms = ASK_MS, live?: string): string {
  return JSON.stringify({
    ask_ms: ms,
    group: 'deploy',
    header: 'foreman',
    no: 'No',
    question: 'May orq run «deploy» for 30 minutes?',
    waiting: WAITING,
    yes: 'Yes, for 30 min',
    ...(live === undefined ? { live: false } : { live: true, until: live }),
  })
}

// A promise the test settles by hand: a question left open until the test answers it.
function later<T>() {
  let settle: (v: T) => void = () => {}
  const promise = new Promise<T>(resolve => {
    settle = resolve
  })
  return { promise, settle }
}

// How the test's AskUserQuestion hook answers: as the person picking `answer`, or refusing with `reject`.
function answering(q: Question, a: Ask) {
  if (a.reject !== undefined) {
    return { deny: a.reject }
  }
  return { result: { answers: { [q.question]: a.answer ?? '' } } }
}

// The engine of one session in a project with groups: `start` answers ACTIVE, each grant check the next of `checks`
// (the last one again), each grant answer the next of `answers` (REFUSED when none is left); each question the next
// of `asks`, a promise for one the test answers later; each tick the next of `ticks` (the last one again, TICKED
// when none).
function engine(
  on: On,
  checks: string[],
  answers: string[],
  asks: (Ask | Promise<Ask>)[],
  start = ACTIVE,
  ticks: string[] = [],
) {
  const grants: Input[] = []
  const questions: Question[] = []
  const statuses: (string | undefined)[] = []
  const logs: string[] = []
  const asked = later<void>()
  const clock = mock.clock(on)
  on('session.id', () => ({ value: 'S-orch' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('session.surfaces', () => ({ value: ['desktop'] }))
  on('agent.list', () => ({ value: [] }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    if (op === 'start') {
      return reply({ stdout: start })
    }
    if (op !== 'grant') {
      return reply({ stdout: (ticks.length > 1 ? ticks.shift() : ticks[0]) ?? TICKED })
    }
    const input = JSON.parse(e.init?.stdin ?? '{}') as Input
    grants.push(input)
    if (input.phase === 'check') {
      return reply({ stdout: (checks.length > 1 ? checks.shift() : checks[0]) ?? '{}\n' })
    }
    return reply({ stdout: answers.shift() ?? REFUSED })
  })
  on('tool.call', async (_, e) => {
    if (e.tool !== 'AskUserQuestion') {
      return { result: 'ok' }
    }
    const q = e.questions[0] as Question
    questions.push(q)
    asked.settle()
    return answering(q, await (asks.shift() ?? { reject: 'no question expected' }))
  })
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.log', (_, e) => {
    logs.push(e.text)
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  return { grants, questions, statuses, logs, clock, asked: asked.promise }
}

function phases(grants: Input[]): unknown[] {
  return grants.map(g => (g.late === true ? 'late' : g.phase === 'check' ? 'check' : g.answer))
}

for (const c of (cases as Case[]).filter(c => c.op === 'grant')) {
  test(`grant: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    const runs: { argv: readonly string[]; input: Input; timeoutMs?: number }[] = []
    const questions: Question[] = []
    const statuses: (string | undefined)[] = []
    const logs: string[] = []
    const ownPhase = c.stdin?.phase
    on('session.id', () => ({ value: c.engine.session }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
    on('session.surfaces', () => ({ value: c.engine.surfaces }))
    on('agent.list', () => ({ value: [] }))
    on('process.run', (_, e) => {
      const op = e.argv[3] ?? ''
      if (op === 'start') {
        return reply({ stdout: c.engine.start })
      }
      if (op === 'tick') {
        return reply({ stdout: '{"marked": true}\n' })
      }
      const input = JSON.parse(e.init?.stdin ?? '{}') as Input
      runs.push({ argv: e.argv, input, timeoutMs: e.init?.timeoutMs })
      if (c.stdin === null) {
        return reply({ reject: 'no call expected' })
      }
      if (input.phase === ownPhase) {
        return reply(c.run ?? { reject: 'no run' })
      }
      return reply({ stdout: input.phase === 'check' ? (c.engine.check ?? '{}\n') : REFUSED })
    })
    on('tool.call', (_, e) => {
      if (e.tool !== 'AskUserQuestion') {
        return { result: 'ok' }
      }
      const q = e.questions[0] as Question
      questions.push(q)
      const a = c.engine.ask ?? { reject: 'no question expected' }
      return a.timeout ? new Promise<never>(() => {}) : answering(q, a)
    })
    on('ui.status', (_, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    on('ui.log', (_, e) => {
      logs.push(e.text)
      return { value: undefined }
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'desktop', isInteractive: true })
    statuses.length = 0 // what session.start showed is start.test.ts's business
    const call = $.tool.call({
      ...(c.engine.tool === undefined
        ? { tool: 'Bash', command: c.engine.command ?? '' }
        : c.engine.input !== undefined
          ? { tool: c.engine.tool, ...c.engine.input }
          : { tool: c.engine.tool, file_path: c.engine.file_path ?? '' }),
      tool_use_id: c.engine.tool_use_id,
      ...(c.engine.agentId === undefined ? {} : { agentId: c.engine.agentId }),
    })
    if (c.engine.ask?.timeout) {
      await clock.advance(ASK_MS)
    }
    const result = await call

    // What the module sent: the case's own call, by its phase, with the launcher and the usual time limit.
    if (c.stdin === null) {
      expect(runs).toEqual([])
    } else {
      const own = runs.filter(r => r.input.phase === ownPhase)
      expect(own.length).toBe(1)
      const run = own[0]!
      expect((run.argv[0] ?? '').endsWith(LAUNCHER)).toBe(true)
      expect(run.argv.slice(1)).toEqual(['hook', 'mod', 'grant'])
      expect(run.timeoutMs).toBe(30000)
      expect(run.input).toEqual(c.stdin)
    }
    expect(statuses).toEqual(c.status === null ? [] : [c.status])

    // Whether it asked, what, and what became of the call.
    let out: Record<string, unknown> = {}
    try {
      out = JSON.parse(c.run?.stdout ?? '') as Record<string, unknown>
    } catch {
      out = {}
    }
    const check = (ownPhase === 'check' ? out : JSON.parse(c.engine.check ?? '{}')) as Record<string, unknown>
    const asks = typeof check.question === 'string' && check.deny === undefined && c.stdin !== null
    expect(questions.length).toBe(asks ? 1 : 0)
    if (asks) {
      const q = questions[0]!
      expect(q.question).toBe(check.question as string)
      expect(q.header).toBe(check.header as string)
      expect(q.options.map(o => o.label)).toEqual([check.yes, check.no])
    }
    let deny: string | undefined
    if (ownPhase === 'answer') {
      deny = out.granted === true ? undefined : (out.deny as string)
      expect(logs).toEqual(typeof out.log === 'string' ? [out.log] : [])
    } else if (typeof out.deny === 'string') {
      deny = out.deny
    } else if (asks) {
      deny = 'refused' // the test's answer phase refuses
    }
    if (deny === undefined) {
      expect(result.deny).toBeUndefined()
      expect(result.result).toBe('ok')
    } else {
      expect(result.deny).toContain(deny)
    }
  })
}

test('a second call within the grant asks nothing: the module heard it given', async ($, on) => {
  const w = engine(on, [question(), question(ASK_MS, UNTIL)], [GRANTED], [{ answer: 'Yes, for 30 min' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).result).toBe('ok')
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).result).toBe('ok')
  expect(w.questions.length).toBe(1)
  expect(phases(w.grants)).toEqual(['check', 'yes', 'check'])
  expect(w.logs).toEqual(['«deploy» allowed for orq until 10:30'])
})

test('a live grant with another until than the one heard given is asked again', async ($, on) => {
  const w = engine(
    on,
    [question(), question(ASK_MS, '2026-11-01T11:00:00')],
    [GRANTED],
    [{ answer: 'Yes, for 30 min' }, { answer: 'No' }],
  )
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  const second = await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(second.deny).toBeDefined()
  expect(w.questions.length).toBe(2)
  expect(phases(w.grants)).toEqual(['check', 'yes', 'check', 'no'])
})

test('a grant that ended (taken away, the mark moved) is forgotten: the next call asks again', async ($, on) => {
  const w = engine(on, [question(), question(), question(ASK_MS, UNTIL)], [GRANTED], [
    { answer: 'Yes, for 30 min' },
    { answer: 'No' },
  ])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY }) // granted
  await $.tool.call({ tool: 'Bash', command: DEPLOY }) // Python: no grant any more; asked, no
  const third = await $.tool.call({ tool: 'Bash', command: DEPLOY }) // the file says live again: never heard
  expect(third.deny).toBeDefined()
  expect(w.questions.length).toBe(3)
})

test('a new conversation (/clear) forgets the grants heard', async ($, on) => {
  const w = engine(on, [question(), question(ASK_MS, UNTIL)], [GRANTED], [{ answer: 'Yes, for 30 min' }, { answer: 'No' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  await $.session.end({ reason: 'clear', sessionId: 'S-orch' })
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(w.questions.length).toBe(2)
})

test('text typed under "Other" that is not the yes label is a no', async ($, on) => {
  const w = engine(on, [question()], [], [{ answer: 'Yes' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).deny).toBeDefined()
  expect(phases(w.grants)).toEqual(['check', 'no'])
})

test('one question per group: a call while it is open is refused at once, the first goes on with the yes', async ($, on) => {
  const answer = later<Ask>()
  const w = engine(on, [question()], [GRANTED], [answer.promise])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const first = $.tool.call({ tool: 'Bash', command: DEPLOY })
  await w.asked
  const second = await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(second.deny).toBeDefined()
  expect(second.deny).toContain(WAITING)
  answer.settle({ answer: 'Yes, for 30 min' })
  expect((await first).result).toBe('ok')
  expect(w.questions.length).toBe(1)
  expect(phases(w.grants)).toEqual(['check', 'check', 'yes'])
})

test('past ask_ms the call is refused, the question stays open; its late answer goes to Python marked late', async ($, on) => {
  const answer = later<Ask>()
  const timeout = '{"deny": "no answer in time", "granted": false}\n'
  const late = '{"granted": false, "log": "late: nothing granted"}\n'
  const w = engine(on, [question()], [timeout, late], [answer.promise, { answer: 'No' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const first = $.tool.call({ tool: 'Bash', command: DEPLOY })
  await w.asked
  await w.clock.advance(ASK_MS - 1)
  expect(phases(w.grants)).toEqual(['check'])
  await w.clock.advance(1)
  const refused = await first
  expect(refused.deny).toBeDefined()
  expect(refused.deny).toContain('no answer in time')
  // the question is still drawn: another call of the group is refused without a second question
  const again = await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(again.deny).toContain(WAITING)
  expect(w.questions.length).toBe(1)
  // the owner answers late: recorded as late, with the timed-out call's input
  answer.settle({ answer: 'Yes, for 30 min' })
  await w.clock.advance(0)
  expect(phases(w.grants)).toEqual(['check', 'timeout', 'check', 'late'])
  expect(w.grants[3]).toEqual({ ...w.grants[1], answer: 'yes', late: true })
  expect(w.logs).toEqual(['late: nothing granted'])
  // the question is closed: the next call asks again
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).deny).toBeDefined()
  expect(w.questions.length).toBe(2)
})

test('a late yes that Python grants (the pending request) is kept: the retry goes on without a second question', async ($, on) => {
  const answer = later<Ask>()
  const timeout = '{"deny": "no answer in time", "granted": false}\n'
  const w = engine(on, [question(), question(ASK_MS, UNTIL)], [timeout, GRANTED], [answer.promise])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const first = $.tool.call({ tool: 'Bash', command: DEPLOY })
  await w.asked
  await w.clock.advance(ASK_MS)
  expect((await first).deny).toContain('no answer in time')
  answer.settle({ answer: 'Yes, for 30 min' })
  await w.clock.advance(0)
  expect(phases(w.grants)).toEqual(['check', 'timeout', 'late'])
  expect(w.logs).toEqual(['«deploy» allowed for orq until 10:30'])
  // the orchestrator runs it again (its inbox says so): grants.json holds the grant the module heard given
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).result).toBe('ok')
  expect(w.questions.length).toBe(1)
})

test('a late dismissal (Esc on the old question) is recorded as late too', async ($, on) => {
  const answer = later<Ask>()
  const w = engine(on, [question()], [], [answer.promise])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const first = $.tool.call({ tool: 'Bash', command: DEPLOY })
  await w.asked
  await w.clock.advance(ASK_MS)
  await first
  answer.settle({ reject: "The user doesn't want to proceed with this tool use." })
  await w.clock.advance(0)
  expect(phases(w.grants)).toEqual(['check', 'timeout', 'late'])
  expect(w.grants[2]!.answer).toBe('dismissed')
})

test('ask_ms 0: the question waits with no limit of the module', async ($, on) => {
  const answer = later<Ask>()
  const w = engine(on, [question(0)], [GRANTED], [answer.promise])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const call = $.tool.call({ tool: 'Bash', command: DEPLOY })
  await w.asked
  await w.clock.advance(ASK_MS * 2) // past the usual limit
  expect(phases(w.grants)).toEqual(['check'])
  answer.settle({ answer: 'Yes, for 30 min' })
  expect((await call).result).toBe('ok')
})

test('an answer phase that fails is a status line, and the call goes on to the classic guard', async ($, on) => {
  const w = engine(on, [question()], ['{}\n'], [{ answer: 'Yes, for 30 min' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  expect((await $.tool.call({ tool: 'Bash', command: DEPLOY })).result).toBe('ok')
  expect(w.statuses).toEqual(['foreman: hook mod grant failed (an answer without "granted")'])
})

test('a check that works again clears the failure line', async ($, on) => {
  const w = engine(on, ['nope', '{}\n'], [], [])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(w.statuses.length).toBe(2)
  expect(w.statuses[0]).toContain('foreman: hook mod grant failed (')
  expect(w.statuses[1]).toBeUndefined()
})

test('only main-loop calls are checked, of any tool', async ($, on) => {
  const w = engine(on, ['{}\n'], [], [])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY, agentId: 'a1' })
  await $.tool.call({ tool: 'Read', file_path: '/srv/prod-api/config.json', agentId: 'a1' })
  expect(w.grants).toEqual([])
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await $.tool.call({ tool: 'Read', file_path: '/p/x', limit: 5, tool_use_id: 't2' })
  expect(phases(w.grants)).toEqual(['check', 'check'])
  // any other tool goes with its arguments, the event's envelope left out
  expect(w.grants[1]).toEqual({
    phase: 'check',
    cwd: '/p',
    session: 'S-orch',
    tool: 'Read',
    input: { file_path: '/p/x', limit: 5 },
    tool_use_id: 't2',
  })
})

test("a Read the owner says yes to goes on; the module's own question is never checked", async ($, on) => {
  const w = engine(on, [question()], [GRANTED], [{ answer: 'Yes, for 30 min' }])
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const read = { tool: 'Read', file_path: '/srv/prod-api/config.json', tool_use_id: 't1' } as const
  expect((await $.tool.call(read)).result).toBe('ok')
  expect(w.questions.length).toBe(1)
  expect(phases(w.grants)).toEqual(['check', 'yes'])
  expect(w.grants[1]!.input).toEqual({ file_path: '/srv/prod-api/config.json' })
  expect(w.grants.some(g => g.tool === 'AskUserQuestion')).toBe(false)
})

test('a group added or taken away while the session runs counts from the next tick', async ($, on) => {
  const none = '{"active": true, "below": false, "min": "2.1.299"}\n'
  const ticks = ['{"grants": true, "marked": false}\n', '{"marked": false}\n', '{"grants": false, "marked": false}\n']
  const w = engine(on, ['{}\n'], [], [], none, ticks)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(w.grants).toEqual([]) // no groups at start: nothing checked
  await w.clock.advance(TICK_MS) // a group added: the tick says so
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(phases(w.grants)).toEqual(['check'])
  await w.clock.advance(TICK_MS) // a tick without `grants` keeps what the module had
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(phases(w.grants)).toEqual(['check', 'check'])
  await w.clock.advance(TICK_MS) // taken away
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(phases(w.grants)).toEqual(['check', 'check'])
})

test('outside an active project nothing is checked', async ($, on) => {
  const w = engine(on, ['{}\n'], [], [], '{"active": false}\n')
  await $.session.start({ cwd: '/elsewhere', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: DEPLOY })
  expect(w.grants).toEqual([])
})

// A reserved file of Claude Code's settings or the registry (hooks_mod `grant`, file:<class>): the owner's yes is for
// one call, so the module keeps nothing and the next write asks again; any session's main loop asks, with or without
// [deny.grants] groups.
const SETTINGS = '/p/.claude/settings.local.json'
const FILE_Q = JSON.stringify({
  ask_ms: ASK_MS,
  group: 'file:claude',
  header: 'foreman',
  live: false,
  no: 'No',
  question: 'May this session of fake write the settings?',
  waiting: 'Blocked: a question about the settings is already waiting.',
  yes: 'Yes, this change only',
})
const FILE_YES = JSON.stringify({ granted: true, log: 'may be written once' })
const NO_GROUPS = '{"active": true, "below": false, "min": "2.1.299"}\n'

test('a yes about a file lets that one call on; the next write of it asks again', async ($, on) => {
  const w = engine(on, [FILE_Q], [FILE_YES, FILE_YES], [{ answer: 'Yes, this change only' }, { answer: 'Yes, this change only' }], NO_GROUPS)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  expect((await $.tool.call({ tool: 'Edit', file_path: SETTINGS, tool_use_id: 't1' })).result).toBe('ok')
  expect((await $.tool.call({ tool: 'Write', file_path: SETTINGS, tool_use_id: 't2' })).result).toBe('ok')
  expect(w.questions.length).toBe(2)
  expect(phases(w.grants)).toEqual(['check', 'yes', 'check', 'yes'])
  expect(w.grants[0]).toEqual({ phase: 'check', cwd: '/p', session: 'S-orch', tool: 'Edit', path: SETTINGS, tool_use_id: 't1' })
})

test('a NotebookEdit names its file by notebook_path', async ($, on) => {
  const w = engine(on, ['{}\n'], [], [], NO_GROUPS)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'NotebookEdit', notebook_path: '/p/.claude/n.ipynb' })
  expect(w.grants[0]!.path).toBe('/p/.claude/n.ipynb')
})

test('without groups, only a Bash command that names such a file is checked', async ($, on) => {
  const w = engine(on, ['{}\n'], [], [], NO_GROUPS)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await $.tool.call({ tool: 'Write', file_path: '/p/foreman.toml' })
  expect(w.grants).toEqual([])
  await $.tool.call({ tool: 'Bash', command: 'cp x ~/.config/foreman/projects.toml' })
  expect(phases(w.grants)).toEqual(['check'])
})

test('a subagent writing such a file is never asked about', async ($, on) => {
  const w = engine(on, [FILE_Q], [], [], NO_GROUPS)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Write', file_path: SETTINGS, agentId: 'a1' })
  await $.tool.call({ tool: 'Bash', command: 'echo x > .claude/settings.json', agentId: 'a1' })
  expect(w.grants).toEqual([])
  expect(w.questions).toEqual([])
})
