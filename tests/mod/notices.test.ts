// The in-between notices: what a tick of contract.ts answers in `notices` and `version` reaches the model as
// `context` with the session's next own tool result (or its next prompt), once; a subagent's tool call gets
// nothing; a refused tool call and a slash command neither take nor drop what is kept; nothing kept, nothing added;
// an inactive session adds nothing. No process runs.
import { expect, mock, test } from 'claude-code/testing'
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

type On = Parameters<Parameters<typeof test>[1]>[1]

const TICK = 60000
const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const PASS = 'harbor orchestrator loop\n\nLoop pass 10:00 (heartbeat written, 0.4 s). Act on each line:\n- inbox'
const INBOX = 'Inbox: 1 unread from Owner: `foreman inbox new`, attend to them and mark them read.'
const LOCK = 'Lock widget held by stranger for 30 min.'

function answer(stdout: string) {
  return {
    value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }
}

// The engine: `start` answers session.start's call, each tick answers the next of `ticks` (then "no pass due");
// a tool call answers `ok` (with `below` as its context), or is refused while `tool.deny` holds a reason; a prompt
// enters as it came. What the module showed and submitted is recorded.
function engine(on: On, start: string, ticks: string[], below?: readonly string[]) {
  const logs: string[] = []
  const submits: { text: string; context?: readonly string[] }[] = []
  const asked: string[] = []
  const tool: { deny: string | null } = { deny: null }
  on('session.id', () => ({ value: 'S-orch' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    asked.push(op)
    if (op === 'start') {
      return answer(start)
    }
    if (op === 'answer') {
      return answer('{"reviewed": false}\n')
    }
    return answer(ticks.shift() ?? '{"due": false, "marked": true}\n')
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.log', (_, e) => {
    logs.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', (_, e) => {
    submits.push({ text: e.text, context: e.context })
    return { text: e.text, context: e.context }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('tool.call', () => {
    if (tool.deny !== null) {
      return { deny: tool.deny }
    }
    return below === undefined ? { result: 'ok' } : { result: 'ok', context: below }
  })
  return { logs, submits, asked, tool }
}

function prompt(text: string, kind: 'composer' | 'plugin' = 'composer') {
  return { text, wait: false, origin: { kind } }
}

for (const c of (cases as Case[]).filter(c => c.op === 'tick')) {
  let out: { notices?: unknown; version?: unknown; version_owner?: unknown; marked?: unknown } = {}
  try {
    out = JSON.parse(c.run?.stdout ?? '')
  } catch {
    continue
  }
  if (typeof out.notices !== 'string' && typeof out.version !== 'string') {
    continue
  }
  test(`notices: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    const { logs } = engine(on, c.engine.start, [c.run!.stdout!])
    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    await clock.advance(TICK)
    const want: string[] = []
    if (typeof out.notices === 'string' && out.marked === true) {
      want.push(out.notices)
    }
    if (typeof out.version === 'string') {
      want.push(out.version)
    }
    // a subagent's tool call (same session): nothing, and nothing used up
    const sub = await $.tool.call({ tool: 'Bash', command: 'ls', agentId: 'a1' })
    expect(sub.context).toBeUndefined()
    expect(logs).toEqual([])
    // the session's own next tool call: all of it, once
    const own = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(own.context).toEqual(want)
    expect(logs).toEqual(typeof out.version_owner === 'string' ? [out.version_owner] : [])
    const again = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(again.context).toBeUndefined()
  })
}

test('nothing kept: a tool call and a prompt get nothing added', async ($, on) => {
  const clock = mock.clock(on)
  const { submits } = engine(on, ACTIVE, [])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
  await $.prompt.submit(prompt('hola'))
  expect(submits).toEqual([{ text: 'hola', context: undefined }])
})

test('an inactive session: no tick, nothing added', async ($, on) => {
  const clock = mock.clock(on)
  const { asked, submits } = engine(on, '{"active": false}\n', [JSON.stringify({ marked: true, notices: INBOX })])
  await $.session.start({ cwd: '/elsewhere', surface: 'terminal', isInteractive: true })
  await clock.advance(3 * TICK)
  expect(asked).toEqual(['start'])
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
  await $.prompt.submit(prompt('hola'))
  expect(submits[0]?.context).toBeUndefined()
})

test('notices kept over two ticks go out together, with a prompt if it comes first', async ($, on) => {
  const clock = mock.clock(on)
  const n = (notices: string) => JSON.stringify({ due: false, marked: true, notices })
  const { submits } = engine(on, ACTIVE, [n(INBOX), n(INBOX), n(LOCK)])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(3 * TICK)
  await $.prompt.submit(prompt('hola'))
  expect(submits).toEqual([{ text: 'hola', context: [`${INBOX} ${LOCK}`] }])
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
})

test('the tool result keeps the context it had', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, ACTIVE, [JSON.stringify({ due: false, marked: true, notices: INBOX })], ['from below'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toEqual(['from below', INBOX])
})

test('a pass submitted drops the notices kept (the pass says what is there)', async ($, on) => {
  const clock = mock.clock(on)
  const pass = JSON.stringify({ due: true, marked: true, submit: PASS })
  const { submits } = engine(on, ACTIVE, [JSON.stringify({ due: false, marked: true, notices: INBOX }), pass])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(2 * TICK)
  expect(submits.map(s => [s.text, s.context])).toEqual([[PASS, undefined]])
  await $.turn.complete({ answer: '', durationMs: 5, isAborted: false, turnId: 't1', reason: 'answer' })
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
})

test('a relieved session drops the notices kept', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, ACTIVE, [JSON.stringify({ due: false, marked: true, notices: INBOX }), '{"marked": false}\n'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(2 * TICK)
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toBeUndefined()
})

test('a refused tool call gets nothing and leaves the notices for the next one', async ($, on) => {
  const clock = mock.clock(on)
  const { tool } = engine(on, ACTIVE, [JSON.stringify({ due: false, marked: true, notices: INBOX })])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  tool.deny = 'not here'
  const refused = await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(refused.deny).toBe('not here')
  expect(refused.context).toBeUndefined()
  tool.deny = null
  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).context).toEqual([INBOX])
})

test('a slash command gets nothing and leaves the notices for the next prompt', async ($, on) => {
  const clock = mock.clock(on)
  const { submits } = engine(on, ACTIVE, [JSON.stringify({ due: false, marked: true, notices: INBOX })])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  await $.prompt.submit({ ...prompt('/foreman:status'), command: { name: 'foreman:status', args: '' } })
  await $.prompt.submit(prompt('hola'))
  expect(submits).toEqual([
    { text: '/foreman:status', context: undefined },
    { text: 'hola', context: [INBOX] },
  ])
})
