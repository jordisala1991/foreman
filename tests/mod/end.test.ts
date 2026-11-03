// The module's session.end against every `end` case of contract.ts: the session starts with the case's `hook mod
// start` answer, then ends; the test checks whether the module asks `hook mod end` (within the event's short
// bound), what it sends, and that it shows nothing whatever Python answers. No process runs. The kit cannot cut a
// dispatch as the engine cuts session.end: `next.budget` reads the hook's own 10 s here; endTimeout's own cases
// (the short bound, nothing left, nothing metered) are at the end.
import { expect, mock, test } from 'claude-code/testing'
import { endTimeout } from '../../hooks/mod/foreman.ts'
import cases from './contract.ts'

type Run = { exitCode?: number; stdout?: string; stderr?: string; reject?: string }

type Case = {
  name: string
  op: string
  python: boolean
  engine: { cwd: string; session: string; version: string; start: string }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

const LAUNCHER = '/bin/foreman'

function answer(run: Run) {
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

for (const c of (cases as Case[]).filter(c => c.op === 'end')) {
  test(`session.end: ${c.name}`, async ($, on) => {
    const runs: { argv: readonly string[]; stdin?: string; timeoutMs?: number }[] = []
    const statuses: (string | undefined)[] = []

    on('session.id', () => ({ value: c.engine.session }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
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
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    on('session.end', (_, e) => ({ sessionId: e.sessionId }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    statuses.length = 0
    const result = await $.session.end({
      reason: 'prompt_input_exit',
      sessionId: c.engine.session,
      resume: { id: c.engine.session },
    })

    expect(result.sessionId).toBe(c.engine.session) // the engine's own answer goes through
    if (c.stdin === null) {
      expect(runs.length).toBe(0)
    } else {
      expect(runs.length).toBe(1)
      const run = runs[0]!
      expect((run.argv[0] ?? '').endsWith(LAUNCHER)).toBe(true)
      expect(run.argv.slice(1)).toEqual(['hook', 'mod', c.op])
      // from what the bound leaves (next.budget): the kit meters a hook's own 10 s, the engine its 1.5 s exit
      expect(run.timeoutMs).toBeGreaterThan(9000)
      expect(run.timeoutMs).toBeLessThanOrEqual(9900)
      expect(JSON.parse(run.stdin ?? 'null')).toEqual(c.stdin)
    }
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
  })
}

// A /clear or an in-process resume (M2): the process goes on under a new session id with no session.start. The
// engine below answers each Python call by its op and records what it was sent; `id` is $.session.id().
const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const PASS = 'harbor orchestrator loop\n\nLoop pass 10:00. Act on each line:\n- inbox'
const DUE = JSON.stringify({ due: true, marked: true, submit: PASS })
const ECHO = 'Your last answer to the owner had 2 paths in it.'
const TICK = 60000

type On = Parameters<Parameters<typeof test>[1]>[1]

function engine(on: On, ticks: string[], drops: (string | null)[]) {
  const state = { id: 'S-before' }
  const sent: { op: string; input: Record<string, unknown> }[] = []
  const submits: string[] = []
  const contexts: (readonly string[] | undefined)[] = [] // the context each prompt reached the engine with
  on('session.id', () => ({ value: state.id }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => ({ value: [] }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    sent.push({ op, input: JSON.parse(e.init?.stdin ?? '{}') as Record<string, unknown> })
    const out: Record<string, string> = {
      start: ACTIVE,
      end: '{"ended": true}\n',
      answer: JSON.stringify({ reminder: ECHO, reviewed: true }),
    }
    const stdout = out[op] ?? ticks.shift() ?? '{"due": false, "marked": true}\n'
    return answer({ exitCode: 0, stdout })
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  on('prompt.submit', (_, e) => {
    submits.push(e.text)
    contexts.push(e.context)
    const drop = drops.shift() ?? null
    return drop === null ? { text: e.text } : { drop }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  return { state, sent, submits, contexts }
}

let turns = 0

function turn(answer = 'done') {
  turns += 1
  return { answer, durationMs: 5, isAborted: false, turnId: `t${turns}`, reason: 'answer' as const }
}

const owner = (text: string) => ({ text, wait: false, origin: { kind: 'composer' as const } })

test('session.end of a /clear names the ending id; the next tick starts again under the new one', async ($, on) => {
  const clock = mock.clock(on)
  const { state, sent } = engine(on, [], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: false })
  await clock.advance(TICK)
  state.id = 'S-after' // /clear: the process goes on under a new id, with no session.start
  await $.session.end({ reason: 'clear', sessionId: 'S-before', resume: { id: 'S-before' } })
  await clock.advance(TICK)
  await clock.advance(TICK)
  expect(sent.map(x => [x.op, x.input.session])).toEqual([
    ['start', 'S-before'],
    ['tick', 'S-before'],
    ['end', 'S-before'],
    ['start', 'S-after'],
    ['tick', 'S-after'],
  ])
  // the second start is the first one again, with the old id: Python records the new session (and moves the mark)
  const [first, again] = sent.filter(x => x.op === 'start')
  expect(again!.input).toEqual({ ...first!.input, session: 'S-after', previous: 'S-before' })
  expect(first!.input.previous).toBeUndefined()
  expect(again!.input.interactive).toBe(false)
})

test('a /clear carries nothing over: no reminder on the first prompt, no retry of a dropped pass', async ($, on) => {
  const clock = mock.clock(on)
  const { state, sent, submits, contexts } = engine(on, [DUE], ['interrupted'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(turn('see /p/a.py and /p/b.py')) // the chat reviewer keeps a reminder
  await clock.advance(TICK) // a pass, dropped: it waits for its retry at the next turn.complete
  expect(submits).toEqual([PASS])
  state.id = 'S-after'
  await $.session.end({ reason: 'clear', sessionId: 'S-before', resume: { id: 'S-before' } })
  await $.prompt.submit(owner('hola')) // the owner's first prompt in the fresh conversation, before any tick
  await $.turn.complete(turn())
  await clock.settle()
  expect(submits).toEqual([PASS, 'hola']) // the old pass is not submitted again
  expect(contexts).toEqual([undefined, undefined]) // nor does the old reminder ride the new prompt
  await clock.advance(TICK)
  expect(sent.filter(x => x.op === 'start').map(x => x.input.previous)).toEqual([undefined, 'S-before'])
})

test('a resume under a new id with no session.end: the tick starts again and drops the old state', async ($, on) => {
  const clock = mock.clock(on)
  const { state, sent, contexts } = engine(on, [], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(turn('see /p/a.py and /p/b.py'))
  state.id = 'S-resumed'
  await clock.advance(TICK)
  await $.prompt.submit(owner('hola'))
  expect(contexts).toEqual([undefined])
  expect(sent.map(x => [x.op, x.input.session, x.input.previous])).toEqual([
    ['start', 'S-before', undefined],
    ['answer', 'S-before', undefined],
    ['start', 'S-resumed', 'S-before'],
  ])
})

test('session.end of a close drops nothing and starts nothing again', async ($, on) => {
  const clock = mock.clock(on)
  const { sent, contexts } = engine(on, [], [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(turn('see /p/a.py and /p/b.py'))
  await $.session.end({ reason: 'prompt_input_exit', sessionId: 'S-before', resume: { id: 'S-before' } })
  await $.prompt.submit(owner('hola'))
  expect(contexts).toEqual([[ECHO]])
  await clock.advance(TICK)
  expect(sent.filter(x => x.op === 'start').length).toBe(1)
})

test('session.end runs the engine end step beside hook mod end, not after it', async ($, on) => {
  const clock = mock.clock(on)
  const order: string[] = []
  on('session.id', () => ({ value: 'S-slow' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('process.run', async (_, e) => {
    const op = e.argv[3] ?? ''
    if (op === 'end') {
      order.push('end asked')
      await clock.sleep(800) // a slow Python start, as on a cold disk
      order.push('end answered')
    }
    const stdout = op === 'start' ? ACTIVE : '{"ended": true}\n'
    return answer({ exitCode: 0, stdout })
  })
  on('ui.status', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => {
    order.push('engine end step')
    return { sessionId: e.sessionId }
  })

  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  const ending = $.session.end({ reason: 'prompt_input_exit', sessionId: 'S-slow', resume: { id: 'S-slow' } })
  await clock.settle()
  expect(order).toEqual(['engine end step', 'end asked']) // the engine's step did not wait for Python
  await clock.advance(800)
  expect((await ending).sessionId).toBe('S-slow') // and the hook returns once the heartbeat's end settled too
  expect(order).toEqual(['engine end step', 'end asked', 'end answered'])
})

test('endTimeout: what the bound leaves less 100 ms, no call under 200 ms, 1.2 s where nothing meters', () => {
  expect(endTimeout(1500)).toBe(1400) // the engine's default bound, untouched by the hooks above
  expect(endTimeout(450.7)).toBe(350) // a hook above spent most of it
  expect(endTimeout(300)).toBe(200)
  expect(endTimeout(299)).toBe(null) // too little for Python to start: no call
  expect(endTimeout(0)).toBe(null)
  expect(endTimeout(Infinity)).toBe(1200)
})
