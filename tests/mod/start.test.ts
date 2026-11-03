// The module's session.start against every `start` case of contract.ts: what it sends to Python, and the status
// line it shows for each answer. The engine (session id and version, $.process.run, $.ui.status) is stubbed here:
// no process runs.
import { expect, mock, test } from 'claude-code/testing'
import cases from './contract.ts'

type Case = {
  name: string
  op: string
  python: boolean
  engine: { cwd: string; session: string; version: string; previous?: string }
  stdin: Record<string, string | boolean>
  run: { exitCode?: number; stdout?: string; stderr?: string; reject?: string }
  status: string | null
}

const LAUNCHER = '/bin/foreman'

for (const c of (cases as Case[]).filter(c => c.op === 'start')) {
  test(`session.start: ${c.name}`, async ($, on) => {
    const runs: { argv: readonly string[]; stdin?: string; timeoutMs?: number }[] = []
    const statuses: (string | undefined)[] = []
    // With `previous` the session starts under that id; then a /clear moves it to `session` and a tick goes by.
    const clock = mock.clock(on)
    let id = c.engine.previous ?? c.engine.session

    on('session.id', () => ({ value: id }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
    on('process.run', (_, e) => {
      runs.push({ argv: e.argv, stdin: e.init?.stdin, timeoutMs: e.init?.timeoutMs })
      if (c.run.reject !== undefined) {
        return { deny: c.run.reject } // the call rejects, as when the child cannot start or outlasts its timeout
      }
      return {
        value: {
          exitCode: c.run.exitCode ?? 0,
          stdout: c.run.stdout ?? '',
          stderr: c.run.stderr ?? '',
          isStdoutTruncated: false,
          isStderrTruncated: false,
        },
      }
    })
    on('ui.status', (_, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))

    const result = await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    if (c.engine.previous !== undefined) {
      id = c.engine.session
      await clock.advance(60000)
      runs.shift() // the start under the old id
    }

    // the engine's own answer goes through untouched
    expect(result.cwd).toBe(c.engine.cwd)
    // one call to `<plugin root>/bin/foreman hook mod start`, with the case's stdin
    expect(runs.length).toBe(1)
    const run = runs[0]!
    const launcher = run.argv[0] ?? ''
    expect(launcher.endsWith(LAUNCHER)).toBe(true)
    expect(run.argv.slice(1)).toEqual(['hook', 'mod', c.op])
    expect(run.timeoutMs).toBe(10000) // the first prompt waits for it: shorter than the ticks' 30 s
    const root = launcher.slice(0, -LAUNCHER.length)
    const sent = JSON.parse(run.stdin ?? 'null')
    expect({ ...sent, root: sent.root === root ? '<root>' : sent.root }).toEqual(c.stdin)
    // the status line, or none
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
  })
}

test('session.start after /reload-plugins asks Python again', async ($, on) => {
  let runs = 0
  on('session.id', () => ({ value: 'S-reload' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('process.run', () => {
    runs += 1
    return {
      value: {
        exitCode: 0,
        stdout: '{"active": false}\n',
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))

  await $.session.start({ cwd: '/a', surface: null, isInteractive: false })
  await $.session.start({ cwd: '/a', surface: null, isInteractive: false })

  expect(runs).toBe(2)
})

test('the start call is bounded at 10 s, the ticks after it keep 30 s', async ($, on) => {
  const clock = mock.clock(on)
  const runs: [string, number | undefined][] = []
  on('session.id', () => ({ value: 'S-bounds' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => ({ value: [] }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    runs.push([op, e.init?.timeoutMs])
    const stdout = op === 'start' ? '{"active": true, "below": false, "min": "2.1.299"}\n' : '{"due": false, "marked": false}\n'
    return {
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))

  await $.session.start({ cwd: '/a', surface: 'terminal', isInteractive: true })
  await clock.advance(60000)

  expect(runs).toEqual([
    ['start', 10000],
    ['tick', 30000],
  ])
})
