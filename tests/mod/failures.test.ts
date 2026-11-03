// The module's one status line when several ops fail: it shows the newest op still failing; an op that works again
// drops its own line and the line goes back to the one before, or none; a start in the same environment (a /clear's
// new session id) drops them all. Each op's own failure and recovery are in its own test file. No process runs.
import { expect, mock, test } from 'claude-code/testing'

const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const TICK = 60000
const TICK_BAD = 'foreman: hook mod tick failed (an answer without "marked")'
const ANSWER_BAD = 'foreman: hook mod answer failed (an answer without "reviewed")'

type On = Parameters<Parameters<typeof test>[1]>[1]

// The engine: `start` answers ACTIVE; each tick and each review the next of `ticks` and `reviews`.
function engine(on: On, ticks: string[], reviews: string[], id: { value: string }) {
  const statuses: (string | undefined)[] = []
  const ops: string[] = []
  on('session.id', () => ({ value: id.value }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => ({ value: [] }))
  on('process.run', (_, e) => {
    const op = e.argv[3] ?? ''
    ops.push(op)
    const stdout =
      op === 'start'
        ? ACTIVE
        : op === 'tick'
          ? (ticks.shift() ?? '{"marked": true}\n')
          : (reviews.shift() ?? '{"reviewed": false}\n')
    return {
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.log', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  return { statuses, ops }
}

function end(text: string, turnId: string) {
  return { answer: text, durationMs: 5, isAborted: false, turnId, reason: 'answer' as const }
}

test('a tick that works again leaves the review still failing on the line; the review working clears it', async ($, on) => {
  const clock = mock.clock(on)
  const { statuses } = engine(on, ['{}\n', '{"marked": true}\n'], ['{}\n', '{}\n', '{"reviewed": true}\n'], {
    value: 'S-orch',
  })
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.turn.complete(end('uno', 't1')) // the review fails
  await clock.advance(TICK) // the tick fails: the newest
  await clock.advance(TICK) // the tick works: back to the review's line
  await $.turn.complete(end('dos', 't2')) // the review fails again: the same line, shown again
  await $.turn.complete(end('tres', 't3')) // the review works: nothing left
  expect(statuses).toEqual([ANSWER_BAD, TICK_BAD, ANSWER_BAD, ANSWER_BAD, undefined])
})

test('an op failing again becomes the newest: the other one recovering leaves its line', async ($, on) => {
  const clock = mock.clock(on)
  const { statuses } = engine(on, ['{}\n', '{}\n'], ['{}\n', '{"reviewed": true}\n'], { value: 'S-orch' })
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK) // the tick fails
  await $.turn.complete(end('uno', 't1')) // the review fails: the newest
  await clock.advance(TICK) // the tick fails again: now the newest
  await $.turn.complete(end('dos', 't2')) // the review works: the tick's line stays
  expect(statuses).toEqual([TICK_BAD, ANSWER_BAD, TICK_BAD, TICK_BAD])
})

test('a start under a new session id drops the failures and clears the line', async ($, on) => {
  const clock = mock.clock(on)
  const id = { value: 'S-old' }
  const { statuses, ops } = engine(on, ['{}\n'], [], id)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK) // the tick fails
  id.value = 'S-new' // a /clear
  await clock.advance(TICK) // the tick finds the new id and starts again
  expect(ops).toEqual(['start', 'tick', 'start'])
  expect(statuses).toEqual([TICK_BAD, undefined])
})
