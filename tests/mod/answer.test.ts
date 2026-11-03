// The chat reviewer against every `answer` case of contract.ts: the session starts with the case's `hook mod start`
// answer, a main-loop turn ends with the case's answer; the test checks whether the module asks `hook mod answer`,
// what it sends, the status line, and that a `reminder` reaches the model with the next prompt a person types, once.
// Then the turns and prompts that must not take part. No process runs.
import { expect, mock, test } from 'claude-code/testing'
import cases from './contract.ts'

type Run = { exitCode?: number; stdout?: string; stderr?: string; reject?: string }

type Case = {
  name: string
  op: string
  python: boolean
  engine: { cwd: string; session: string; version: string; start: string; turn: string; answer: string }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

type On = Parameters<Parameters<typeof test>[1]>[1]
type Kind = 'composer' | 'plugin' | 'task-notification' | 'peer'

const LAUNCHER = '/bin/foreman'
const TICK = 60000
const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const ECHO = 'Your last message to the owner named without explaining: J1, J2.'

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

function end(text: string, turnId = 't1', agentId?: string) {
  return {
    answer: text,
    durationMs: 5,
    isAborted: false,
    turnId,
    reason: 'answer' as const,
    ...(agentId === undefined ? {} : { agentId }),
  }
}

function prompt(text: string, kind: Kind = 'composer') {
  return { text, wait: false, origin: { kind } }
}

// The engine: session.start's call answers `start`, a tick `tick`, each review the next of `reviews`.
function engine(on: On, start: string, reviews: string[], tick = '{"due": false, "marked": true}\n') {
  const runs: { op: string; stdin?: string; timeoutMs?: number; argv: readonly string[] }[] = []
  const statuses: (string | undefined)[] = []
  const contexts: (readonly string[] | undefined)[] = []
  on('session.id', () => ({ value: 'S-orch' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    if (op === 'start') {
      return answer({ stdout: start })
    }
    if (op === 'tick') {
      return answer({ stdout: tick })
    }
    runs.push({ op, stdin: e.init?.stdin, timeoutMs: e.init?.timeoutMs, argv: e.argv })
    return answer({ stdout: reviews.shift() ?? '{"reviewed": false}\n' })
  })
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.log', () => ({ value: undefined }))
  on('prompt.submit', (_, e) => {
    contexts.push(e.context)
    return { text: e.text, context: e.context }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('tool.call', () => ({ result: 'ok' }))
  return { runs, statuses, contexts }
}

for (const c of (cases as Case[]).filter(c => c.op === 'answer')) {
  test(`answer: ${c.name}`, async ($, on) => {
    const runs: { argv: readonly string[]; stdin?: string; timeoutMs?: number }[] = []
    const statuses: (string | undefined)[] = []
    const contexts: (readonly string[] | undefined)[] = []
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
    on('ui.log', () => ({ value: undefined }))
    on('prompt.submit', (_, e) => {
      contexts.push(e.context)
      return { text: e.text, context: e.context }
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    on('turn.complete', (_, e) => ({ text: e.answer }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    statuses.length = 0 // what session.start showed is start.test.ts's business
    await $.turn.complete(end(c.engine.answer, c.engine.turn))

    if (c.stdin === null) {
      expect(runs.length).toBe(0)
    } else {
      expect(runs.length).toBe(1)
      const run = runs[0]!
      expect((run.argv[0] ?? '').endsWith(LAUNCHER)).toBe(true)
      expect(run.argv.slice(1)).toEqual(['hook', 'mod', c.op])
      expect(run.timeoutMs).toBe(30000)
      expect(JSON.parse(run.stdin ?? 'null')).toEqual(c.stdin)
    }
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
    let reminder: string | undefined
    try {
      const out = JSON.parse(c.run?.stdout ?? '') as { reminder?: unknown }
      reminder = typeof out.reminder === 'string' ? out.reminder : undefined
    } catch {
      reminder = undefined
    }
    await $.prompt.submit(prompt('¿y ahora?'))
    await $.prompt.submit(prompt('¿y ahora?'))
    expect(contexts).toEqual([reminder === undefined ? undefined : [reminder], undefined])
  })
}

const FLAGGED = JSON.stringify({ reminder: ECHO, reviewed: true })

test('a subagent turn asks no review', async ($, on) => {
  const { runs } = engine(on, ACTIVE, [FLAGGED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('fo99t9 is on step 4', 't1', 'a1'))
  expect(runs).toEqual([])
})

test('the reminder waits for a prompt a person types: not a notification, a peer or the loop pass', async ($, on) => {
  const { contexts } = engine(on, ACTIVE, [FLAGGED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('Antes necesito J1 y J2.'))
  for (const kind of ['task-notification', 'peer', 'plugin'] as const) {
    await $.prompt.submit(prompt('x', kind))
  }
  await $.prompt.submit(prompt('hola'))
  expect(contexts).toEqual([undefined, undefined, undefined, [ECHO]])
})

test('a newer answer replaces the reminder: a clean one clears it', async ($, on) => {
  const { runs, contexts } = engine(on, ACTIVE, [FLAGGED, '{"reviewed": true}\n'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('Antes necesito J1 y J2.', 't1'))
  await $.turn.complete(end('Todo listo.', 't2'))
  await $.prompt.submit(prompt('hola'))
  expect(runs.map(r => JSON.parse(r.stdin ?? '{}').turn)).toEqual(['t1', 't2'])
  expect(contexts).toEqual([undefined])
})

test('a session the tick found unmarked asks no review', async ($, on) => {
  const clock = mock.clock(on)
  const { runs } = engine(on, ACTIVE, [FLAGGED], '{"marked": false}\n')
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  await $.turn.complete(end('Antes necesito J1 y J2.'))
  expect(runs).toEqual([])
})

test('the reminder never rides a tool result', async ($, on) => {
  const { contexts } = engine(on, ACTIVE, [FLAGGED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('Antes necesito J1 y J2.'))
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
  await $.prompt.submit(prompt('hola'))
  expect(contexts).toEqual([[ECHO]])
})

test('a review that works again clears the failure line', async ($, on) => {
  const { statuses } = engine(on, ACTIVE, ['{}\n', '{"reviewed": true}\n'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('uno', 't1'))
  await $.turn.complete(end('dos', 't2'))
  expect(statuses).toEqual(['foreman: hook mod answer failed (an answer without "reviewed")', undefined])
})
