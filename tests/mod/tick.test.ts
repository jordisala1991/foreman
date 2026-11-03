// The module's loop timer against every `tick` case of contract.ts, on a mocked clock: the session starts with the
// case's `hook mod start` answer, then the clock moves one tick; the test checks whether the module asks `hook mod
// tick`, what it sends, and what it does with Python's answer (one $.prompt.submit of `submit`, one $.ui.log line
// of `log`, the status line). Then the retry of a pass that did not reach the model, and the heartbeat's parts
// (`busy`, `hold` while a pass waits to be submitted or runs, the subagents' statuses). A `tick` case whose stdin
// says `hold` is the tick the module asks while its pass waits: driven by its own test after a pass that stays
// pending. No process runs.
import { expect, mock, test } from 'claude-code/testing'
import cases from './contract.ts'

type Run = { exitCode?: number; stdout?: string; stderr?: string; reject?: string }

type Case = {
  name: string
  op: string
  python: boolean
  engine: { cwd: string; session: string; version: string; start: string; agents?: unknown[] }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

type On = Parameters<Parameters<typeof test>[1]>[1]

const LAUNCHER = '/bin/foreman'
const TICK = 60000

function answer(run: Run) {
  if (run.reject !== undefined) {
    return { deny: run.reject } // the call rejects, as when the child cannot start or outlasts its timeout
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

function expected(run: Run | null): { submit?: string; log?: string } {
  try {
    const out = JSON.parse(run?.stdout ?? '') as { submit?: unknown; log?: unknown }
    return {
      submit: typeof out.submit === 'string' ? out.submit : undefined,
      log: typeof out.log === 'string' ? out.log : undefined,
    }
  } catch {
    return {}
  }
}

// A tick that answers `renew` runs a command as well: those cases are renew.test.ts's.
function renews(c: Case): boolean {
  try {
    const out = JSON.parse(c.run?.stdout ?? '') as object
    return 'renew' in out
  } catch {
    return false
  }
}

function held(c: Case): boolean {
  return c.stdin?.hold === true
}

for (const c of (cases as Case[]).filter(c => c.op === 'tick' && !renews(c) && !held(c))) {
  test(`tick: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    const runs: { argv: readonly string[]; stdin?: string; timeoutMs?: number }[] = []
    const statuses: (string | undefined)[] = []
    const submits: string[] = []
    const logs: string[] = []

    on('session.id', () => ({ value: c.engine.session }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
    on('agent.list', () => ({ value: (c.engine.agents ?? []) as never }))
    on('process.run', (_, e) => {
      if (e.argv[3] === 'start') {
        return answer({ exitCode: 0, stdout: c.engine.start, stderr: '' })
      }
      runs.push({ argv: e.argv, stdin: e.init?.stdin, timeoutMs: e.init?.timeoutMs })
      return answer(c.run ?? { reject: 'no call expected' })
    })
    on('ui.status', (_, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    on('ui.log', (_, e) => {
      logs.push(e.text)
      return { value: undefined }
    })
    on('prompt.submit', (_, e) => {
      submits.push(e.text)
      return { text: e.text }
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    statuses.length = 0 // what session.start showed is start.test.ts's business
    await clock.advance(TICK - 1)
    expect(runs.length).toBe(0) // nothing before the first tick
    await clock.advance(1)

    if (c.stdin === null) {
      expect(runs.length).toBe(0)
    } else {
      // one call to `<plugin root>/bin/foreman hook mod tick`, with the case's stdin
      expect(runs.length).toBe(1)
      const run = runs[0]!
      expect((run.argv[0] ?? '').endsWith(LAUNCHER)).toBe(true)
      expect(run.argv.slice(1)).toEqual(['hook', 'mod', c.op])
      expect(run.timeoutMs).toBe(30000)
      expect(JSON.parse(run.stdin ?? 'null')).toEqual(c.stdin)
    }
    const want = c.stdin === null ? {} : expected(c.run)
    expect(submits).toEqual(want.submit === undefined ? [] : [want.submit])
    expect(logs).toEqual(want.log === undefined ? [] : [want.log])
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
  })
}

const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const PASS = 'harbor orchestrator loop\n\nLoop pass 10:00 (heartbeat written, 0.4 s). Act on each line:\n- inbox'
const DUE = JSON.stringify({ due: true, marked: true, submit: PASS })

// A submission that stays pending until the test settles it (the session is not idle yet).
const PENDING = Symbol('pending')
// A submission the engine rejects (the call throws): the module counts the pass as dropped.
const REJECTED = Symbol('rejected')

// The engine for the scenarios below: each tick answers the next of `ticks` (then "no pass due"; a function: called
// when the tick asks, its answer comes when the promise it returns resolves); each submission answers the next of
// `drops` (a reason: dropped; null: it entered; PENDING: it settles, as entered, when the test calls the next of
// `pending`; REJECTED: the call rejects).
function engine(
  on: On,
  ticks: (string | (() => Promise<string>))[],
  drops: (string | null | typeof PENDING | typeof REJECTED)[],
  held = '{"marked": true}\n',
) {
  const asked: string[] = []
  const sent: Record<string, unknown>[] = [] // the stdin of each tick
  const submits: string[] = []
  const contexts: (readonly string[] | undefined)[] = [] // the context each prompt reached the engine with
  const agents: { list: unknown[] | null } = { list: [] } // null: $.agent.list() fails
  const pending: (() => void)[] = [] // what settles each PENDING submission, in order
  on('session.id', () => ({ value: 'S-orch' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => (agents.list === null ? { deny: 'no agents here' } : { value: agents.list as never }))
  on('process.run', (_, e) => {
    if (e.argv[3] === 'start') {
      return answer({ exitCode: 0, stdout: ACTIVE, stderr: '' })
    }
    if (e.argv[3] === 'answer') {
      return answer({ exitCode: 0, stdout: '{"reviewed": false}\n', stderr: '' }) // the chat reviewer: answer.test.ts
    }
    asked.push(e.argv[3] ?? '')
    const input = JSON.parse(e.init?.stdin ?? '{}') as Record<string, unknown>
    sent.push(input)
    if (input.hold === true) {
      return answer({ exitCode: 0, stdout: held, stderr: '' }) // the heartbeat alone
    }
    const out = ticks.shift() ?? '{"due": false, "marked": true}\n'
    if (typeof out === 'string') {
      return answer({ exitCode: 0, stdout: out, stderr: '' })
    }
    return out().then(stdout => answer({ exitCode: 0, stdout, stderr: '' }))
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  on('prompt.submit', (_, e, next) => {
    submits.push(e.text)
    contexts.push(e.context)
    const drop = drops.shift() ?? null
    if (drop === PENDING) {
      return new Promise<{ text: string }>(resolve => pending.push(() => resolve({ text: e.text })))
    }
    if (drop === REJECTED) {
      return next(e) // nothing beneath answers, so the call rejects with the engine's words
    }
    return drop === null ? { text: e.text } : { drop }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  return { asked, sent, submits, contexts, agents, pending }
}

// A tick whose answer the test hands over when it chooses (`give`); `asked` resolves once the tick has asked.
function slowTick() {
  let give: (stdout: string) => void = () => {}
  let asking: () => void = () => {}
  const out = new Promise<string>(resolve => {
    give = resolve
  })
  const asked = new Promise<void>(resolve => {
    asking = resolve
  })
  const tick = () => {
    asking()
    return out
  }
  return { tick, asked, give: (stdout: string) => give(stdout) }
}

// Each turn.complete below has its own turnId, as the engine gives.
let turns = 0

function end(aborted = false, agentId?: string) {
  turns += 1
  return {
    answer: aborted ? '' : 'done',
    durationMs: 5,
    isAborted: aborted,
    turnId: `t${turns}`,
    reason: aborted ? ('aborted' as const) : ('answer' as const),
    ...(agentId === undefined ? {} : { agentId }),
  }
}

test('a dropped pass is submitted again once, at the next turn.complete', async ($, on) => {
  const clock = mock.clock(on)
  const { submits } = engine(on, [DUE], ['interrupted', 'interrupted'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(submits).toEqual([PASS])
  await $.turn.complete(end(false, 'a1')) // a subagent's turn is not the moment
  expect(submits).toEqual([PASS])
  await $.turn.complete(end())
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
  await $.turn.complete(end()) // dropped again: no third time
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
})

test('a pass whose turn is interrupted (Esc) is submitted again once, at the next turn.complete', async ($, on) => {
  const clock = mock.clock(on)
  const { submits } = engine(on, [DUE], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(submits).toEqual([PASS])
  await $.turn.complete(end(true)) // the pass's own turn, interrupted
  expect(submits).toEqual([PASS])
  await $.turn.complete(end()) // the owner's next turn ends: the retry
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
  await $.turn.complete(end(true)) // the retry interrupted too: given up
  await $.turn.complete(end())
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
})

test('a pass that ran is not submitted again, and while its turn runs the ticks ask for the heartbeat alone', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, submits } = engine(on, [DUE], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(sent.map(x => x.hold)).toEqual([undefined])
  await clock.advance(2 * TICK) // the pass's turn still runs: the heartbeat goes on, no pass is asked for
  expect(sent.map(x => x.hold)).toEqual([undefined, true, true])
  await $.turn.complete(end())
  await $.turn.complete(end())
  await clock.advance(TICK)
  expect(sent.map(x => x.hold)).toEqual([undefined, true, true, undefined])
  expect(submits).toEqual([PASS])
})

test('a held tick leaves the pass alone, whatever it answers', async ($, on) => {
  const clock = mock.clock(on)
  // a held answer that says unmarked (the mark moved meanwhile) is read at the next full tick, not here
  const { sent, submits } = engine(on, [DUE], [], '{"marked": false}\n')
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK) // submitted: its turn runs
  await clock.advance(TICK)
  expect(sent.at(-1)?.hold).toBe(true)
  await $.turn.complete(end(true)) // its turn is interrupted: still this session's pass, retried once
  await $.turn.complete(end())
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
})

test('a held tick does not hand over notices', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, contexts } = engine(on, [DUE], [], '{"marked": true, "notices": "Inbox: 1 unread"}\n')
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(2 * TICK) // the pass, then a held tick while its turn runs
  expect(sent.at(-1)?.hold).toBe(true)
  await $.turn.complete(end())
  await $.prompt.submit({ text: 'hello', wait: false, origin: { kind: 'composer' } })
  expect(contexts.at(-1) ?? []).toEqual([])
})

test('the heartbeat says busy while a main-loop turn runs, not a subagent\'s', async ($, on) => {
  const clock = mock.clock(on)
  const { sent } = engine(on, [], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  await $.turn.start({ text: '', turnId: 't-sub', agentId: 'a1' })
  await clock.advance(TICK)
  await $.turn.start({ text: 'go on', turnId: 't1' })
  await clock.advance(TICK)
  await $.turn.complete(end(false, 'a1')) // a subagent's turn ends: the main one still runs
  await clock.advance(TICK)
  await $.turn.complete(end())
  await clock.advance(TICK)
  expect(sent.map(x => x.busy)).toEqual([false, false, true, true, false])
  expect(sent.every(x => x.claude_code === '2.1.314' && x.cwd === '/p' && x.session === 'S-orch')).toBe(true)
})

test('the subagents\' statuses go with each tick until a tick says the session is not the marked one', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, agents } = engine(on, ['{"due": false, "marked": true}\n', '{"marked": false}\n'], [])
  agents.list = [{ id: 'a1', description: 'fix', type: 'foreman:dev', status: 'idle', name: 'hb7t1' }]
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(3 * TICK)
  expect(sent.map(x => x.agents)).toEqual([[{ id: 'a1', status: 'idle' }], [{ id: 'a1', status: 'idle' }], undefined])
})

test('a $.agent.list() that fails leaves the statuses out, and the tick goes on', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, agents } = engine(on, [], [])
  agents.list = null
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(sent.length).toBe(1)
  expect('agents' in sent[0]!).toBe(false)
})

test('a second session.start (after /reload-plugins) starts one timer, not two', async ($, on) => {
  const clock = mock.clock(on)
  const { asked } = engine(on, [], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(3 * TICK)
  expect(asked).toEqual(['tick', 'tick', 'tick'])
})

test('a relieved session drops the pass still waiting for its retry', async ($, on) => {
  const clock = mock.clock(on)
  const { submits } = engine(on, [DUE, '{"marked": false}\n'], ['interrupted'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(submits).toEqual([PASS])
  await clock.advance(TICK) // another session was marked meanwhile
  await $.turn.complete(end())
  await clock.settle()
  expect(submits).toEqual([PASS])
})

test('a submission that waits for the session to be idle does not stop the ticks: each one beats, held', async ($, on) => {
  const clock = mock.clock(on)
  const { asked, sent, submits, pending } = engine(on, [DUE], [PENDING])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK) // the pass is due: its submission waits (the owner's turn runs)
  expect(submits).toEqual([PASS])
  await clock.advance(3 * TICK) // three ticks while it waits: one heartbeat each, held, and no second pass
  expect(asked).toEqual(['tick', 'tick', 'tick', 'tick'])
  expect(sent.map(x => x.hold)).toEqual([undefined, true, true, true])
  expect(submits).toEqual([PASS])
  await $.turn.complete(end()) // the owner's turn ends while the pass still waits: not the pass's turn
  pending.shift()!() // the session is idle: the pass's turn starts
  await clock.settle()
  await clock.advance(TICK) // its turn runs: still held
  expect(sent.at(-1)?.hold).toBe(true)
  await $.turn.complete(end()) // the pass's own turn ends
  await clock.advance(TICK)
  expect(sent.at(-1)?.hold).toBe(undefined)
  expect(submits).toEqual([PASS])
})

const PASS2 = 'harbor orchestrator loop\n\nLoop pass 10:30 (heartbeat written, 0.3 s). Act on each line:\n- review'
const DUE2 = JSON.stringify({ due: true, marked: true, submit: PASS2 })

test('a turn that ends while a tick runs does not retry the dropped pass: the tick\'s new pass replaces it', async ($, on) => {
  const clock = mock.clock(on)
  const later = slowTick()
  const { submits } = engine(on, [DUE, later.tick], ['interrupted'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(submits).toEqual([PASS]) // dropped: it waits for its retry
  const next = clock.advance(TICK) // a tick, not held: Python decides a new pass meanwhile
  await later.asked
  await $.turn.complete(end()) // the owner's turn ends during the tick: no retry now
  expect(submits).toEqual([PASS])
  later.give(DUE2)
  await next
  await clock.settle()
  expect(submits).toEqual([PASS, PASS2]) // one pass queued, the new one
  await $.turn.complete(end()) // the new pass's turn ends: the old one is not retried
  await clock.settle()
  expect(submits).toEqual([PASS, PASS2])
})

test('a dropped pass whose retry a tick skipped is retried at the next turn.complete when no new pass came', async ($, on) => {
  const clock = mock.clock(on)
  const later = slowTick()
  const { submits } = engine(on, [DUE, later.tick], ['interrupted'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  const next = clock.advance(TICK)
  await later.asked
  await $.turn.complete(end()) // during the tick: skipped
  later.give('{"due": false, "marked": true}\n')
  await next
  await clock.settle()
  expect(submits).toEqual([PASS])
  await $.turn.complete(end()) // the next one, with no tick running: the retry
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
})

// The held tick of contract.ts: asked while the pass a tick submitted waits for the session to be idle, it sends the
// case's stdin (with `hold`), and the answer, whatever it says, neither submits nor shows anything.
for (const c of (cases as Case[]).filter(c => c.op === 'tick' && held(c))) {
  test(`tick: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    const { sent, submits } = engine(on, [DUE], [PENDING], c.run?.stdout ?? '')
    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    await clock.advance(TICK) // the pass: its submission waits
    await clock.advance(TICK) // held
    expect(sent.length).toBe(2)
    expect(sent[1]).toEqual(c.stdin)
    expect(submits).toEqual([PASS])
  })
}

test('a submission that rejects counts as dropped: the next tick is not held, and the pass is retried once at the next turn.complete', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, submits } = engine(on, [DUE], [REJECTED, null])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  await clock.settle()
  expect(submits).toEqual([PASS])
  await clock.advance(TICK) // dropped: the tick is not held
  expect(sent.map(x => x.hold)).toEqual([undefined, undefined])
  await $.turn.complete(end()) // the retry
  await clock.settle()
  expect(submits).toEqual([PASS, PASS])
  await clock.advance(TICK) // the retried pass entered: held while its turn runs
  expect(sent.at(-1)?.hold).toBe(true)
})
