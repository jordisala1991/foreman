// The renewal in place against the cases of contract.ts: a tick that answers `renew` (the module shows the line and
// runs `/compact <instructions>` from its timer, once, with no pass), each `renewed` case (what the call settled
// with goes to Python, whose `log` is shown, or the failure as a status line), and the first measure after a
// renewal (its `log` shown). No process runs and nothing compacts: $.command.run is the test's.
import { expect, mock, test } from 'claude-code/testing'
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
    start?: string
    text?: string
    reject?: string
    measure?: { context: { tokens?: number; window: number; percent?: number }; rateLimits: []; changed: string[] }
  }
  stdin: Record<string, unknown> | null
  run: Run | null
  status: string | null
}

const TICK = 60000
const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const all = cases as Case[]
const due = all.find(c => c.op === 'tick' && c.run?.stdout?.includes('"renew"'))!
const closing = all.find(c => c.op === 'measure' && c.run?.stdout?.includes('"log"'))!

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

function fields(run: Run | null): Record<string, unknown> {
  try {
    return JSON.parse(run?.stdout ?? '') as Record<string, unknown>
  } catch {
    return {}
  }
}

type On = Parameters<Parameters<typeof test>[1]>[1]

// The engine: `start` answers ACTIVE, each tick `tickOut`, `renewed` `renewedRun`, `measure` `measureRun`;
// $.command.run answers `command` (`reject`: no answer, so the call rejects with the engine's words; null: it
// never settles).
function engine(
  on: On,
  c: { session: string; tickOut: string; renewedRun?: Run; measureRun?: Run },
  command: { text?: string; reject?: string } | null,
) {
  const asked: { op: string; stdin: Record<string, unknown> }[] = []
  const logs: string[] = []
  const statuses: (string | undefined)[] = []
  const commands: { command: string; args?: string }[] = []
  const submits: string[] = []
  on('session.id', () => ({ value: c.session }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => ({ value: [] }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    asked.push({ op, stdin: JSON.parse(e.init?.stdin ?? '{}') as Record<string, unknown> })
    if (op === 'start') {
      return answer({ stdout: ACTIVE })
    }
    if (op === 'renewed') {
      return answer(c.renewedRun ?? { stdout: '{"recorded": false}\n' })
    }
    if (op === 'measure') {
      return answer(c.measureRun ?? { stdout: '{"stored": true}\n' })
    }
    return answer({ stdout: c.tickOut })
  })
  on('ui.log', (_, e) => {
    logs.push(e.text)
    return { value: undefined }
  })
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('command.run', (_, e, next) => {
    commands.push({ command: e.command, args: e.args })
    if (command === null) {
      return new Promise<never>(() => {})
    }
    if (command.reject !== undefined) {
      return next(e) // nothing beneath answers, so the call rejects with the engine's words
    }
    return { text: command.text }
  })
  on('prompt.submit', (_, e) => {
    submits.push(e.text)
    return { text: e.text }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
  return { asked, logs, statuses, commands, submits }
}

test(`tick: ${due.name}`, async ($, on) => {
  const clock = mock.clock(on)
  const out = fields(due.run)
  const { asked, logs, commands, submits, statuses } = engine(
    on,
    { session: due.engine.session, tickOut: due.run?.stdout ?? '' },
    null,
  )
  await $.session.start({ cwd: due.engine.cwd, surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(asked.map(a => a.op)).toEqual(['start', 'tick'])
  expect(asked[1]!.stdin).toEqual(due.stdin)
  expect(logs).toEqual([out.log])
  expect(commands).toEqual([{ command: 'compact', args: out.instructions as string }])
  expect(submits).toEqual([]) // a renewal is no pass
  expect(statuses).toEqual([])
})

for (const c of all.filter(c => c.op === 'renewed')) {
  test(`renewed: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    const command = c.engine.reject !== undefined ? { reject: c.engine.reject } : { text: c.engine.text }
    const { asked, logs, commands, statuses } = engine(
      on,
      { session: c.engine.session, tickOut: due.run?.stdout ?? '', renewedRun: c.run ?? undefined },
      command,
    )
    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    await clock.advance(TICK)
    await clock.settle()
    expect(commands.map(x => x.command)).toEqual(['compact'])
    const sent = asked.filter(a => a.op === 'renewed')
    expect(sent.length).toBe(1)
    expect(sent[0]!.stdin).toEqual(c.stdin)
    const log = fields(c.run).log
    expect(logs).toEqual([fields(due.run).log, ...(typeof log === 'string' ? [log] : [])])
    expect(statuses).toEqual(c.status === null ? [] : [c.status])
  })
}

test(`session.measure: ${closing.name}`, async ($, on) => {
  const { asked, logs, statuses } = engine(
    on,
    { session: closing.engine.session, tickOut: '{"due": false, "marked": true}\n', measureRun: closing.run ?? undefined },
    null,
  )
  await $.session.start({ cwd: closing.engine.cwd, surface: 'terminal', isInteractive: true })
  const m = closing.engine.measure!
  await $.session.measure(m as never)
  const sent = asked.filter(a => a.op === 'measure')
  expect(sent.map(a => a.stdin)).toEqual([closing.stdin])
  expect(logs).toEqual([fields(closing.run).log])
  expect(statuses).toEqual([])
})
