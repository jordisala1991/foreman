// The module's session.measure against every `measure` case of contract.ts: the session starts with the case's
// `hook mod start` answer, then the engine raises the case's measure event; the test checks whether the module asks
// `hook mod measure`, what it sends, and the status line it shows. The engine is stubbed here: no process runs.
import { expect, test } from 'claude-code/testing'
import cases from './contract.ts'

type Run = { exitCode?: number; stdout?: string; stderr?: string; reject?: string }

type Case = {
  name: string
  op: string
  python: boolean
  engine: {
    cwd: string
    session: string
    version: string
    start: string
    measure: { context: { tokens?: number; window: number; percent?: number }; rateLimits: []; cost?: { usd: number }; changed: [] }
  }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

const LAUNCHER = '/bin/foreman'

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

for (const c of (cases as Case[]).filter(c => c.op === 'measure')) {
  test(`session.measure: ${c.name}`, async ($, on) => {
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
    on('session.measure', (_, e) => ({ changed: e.changed }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    statuses.length = 0 // what session.start showed is start.test.ts's business
    const result = await $.session.measure(c.engine.measure as never)

    // the engine's own answer goes through untouched
    expect(result.changed).toEqual(c.engine.measure.changed)
    if (c.stdin === null) {
      expect(runs.length).toBe(0)
    } else {
      // one call to `<plugin root>/bin/foreman hook mod measure`, with the case's stdin
      expect(runs.length).toBe(1)
      const run = runs[0]!
      const launcher = run.argv[0] ?? ''
      expect(launcher.endsWith(LAUNCHER)).toBe(true)
      expect(run.argv.slice(1)).toEqual(['hook', 'mod', c.op])
      expect(run.timeoutMs).toBe(30000)
      expect(JSON.parse(run.stdin ?? 'null')).toEqual(c.stdin)
    }
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
  })
}

const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const MEASURE = { context: { tokens: 5000, window: 200000, percent: 3 }, rateLimits: [], changed: ['context'] }

function engine(on: Parameters<Parameters<typeof test>[1]>[1], replies: Run[], statuses: (string | undefined)[]) {
  const runs: string[] = []
  on('session.id', () => ({ value: 'S-main' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('process.run', (_, e) => {
    if (e.argv[3] === 'start') {
      return answer({ exitCode: 0, stdout: ACTIVE, stderr: '' })
    }
    runs.push(e.argv[3] ?? '')
    return answer(replies.shift() ?? { exitCode: 0, stdout: '{"stored": true}\n', stderr: '' })
  })
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
  return runs
}

test("a subagent's turn asks nothing: only the main loop's measure does", async ($, on) => {
  const statuses: (string | undefined)[] = []
  const runs = engine(on, [], statuses)
  on('turn.complete', () => ({ text: '' }))
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete({ answer: 'done', durationMs: 5, isAborted: false, turnId: 't1', agentId: 'a1', reason: 'answer' })
  expect(runs).toEqual([])
  await $.session.measure(MEASURE as never)
  expect(runs).toEqual(['measure'])
})

test('a measure that works after one that failed clears the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const runs = engine(on, [{ exitCode: 1, stdout: '', stderr: 'boom\n' }], statuses)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.session.measure(MEASURE as never)
  await $.session.measure(MEASURE as never)
  await $.session.measure(MEASURE as never)
  expect(runs).toEqual(['measure', 'measure', 'measure'])
  expect(statuses).toEqual(['foreman: hook mod measure failed (exit 1: boom)', undefined])
})
