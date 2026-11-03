// The orchestrator's band (`ui.render` of `AbovePrompt`) and `/foreman:status` (`command.run`) against the cases of
// contract.ts: after one tick of each `tick` case the band is mounted on the terminal and the desktop, wide enough
// for every part, and its rows must be the rows of the tick's `band`, one Text each holding one Text a part (and one
// a separator), joined with « · » (one space after the dot), the `more` part with its number (none at 0); any other
// answer (not marked, no band, a band of another shape, a failure) leaves the band to the engine. Each `status` case
// runs the command: its output is Python's `text`, or the failure line. Then the band by width (which parts fit at
// 160, 100, 80, 60 and 40 terminal columns, in which order and with which style, on both surfaces), a session losing
// the mark, a survey, a /reload-plugins and an inert session. No process runs.
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
type T$ = Parameters<Parameters<typeof test>[1]>[0]

const TICK = 60000
const SURFACES = ['terminal', 'desktop'] as const
const ACTIVE = '{"active": true, "below": false, "min": "2.1.299"}\n'
const ENGINE = 'the engine draws its own band'

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

function parsed(run: Run | null): Record<string, unknown> {
  try {
    const out = JSON.parse(run?.stdout ?? '') as unknown
    return typeof out === 'object' && out !== null ? (out as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

type Part = { text: string; role: string; prio: number; n?: number }

// A row as one line: the parts' texts joined with « · » (one space after the dot), the `more` part with its number
// and left out at 0.
function line(row: Part[]): string {
  const shown = row
    .filter(p => p.role !== 'more' || (p.n ?? 0) > 0)
    .map(p => ({ role: p.role, text: p.role === 'more' ? p.text.replace('{n}', String(p.n)) : p.text }))
  return shown.map((p, i) => (i === 0 ? '' : shown[i - 1]!.role === 'dot' ? ' ' : ' · ') + p.text).join('')
}

// What the band must show for a tick's answer: its rows as lines when the session is marked and the band has its
// shape ({level, rows}, each row a list of parts with a text and a prio).
function rows(run: Run | null): string[] | null {
  const out = parsed(run)
  const band = out.band as { level?: unknown; rows?: unknown } | undefined
  if (out.marked !== true || typeof band !== 'object' || band === null || typeof band.level !== 'string') {
    return null
  }
  const rs = band.rows
  if (!Array.isArray(rs) || rs.length === 0) {
    return null
  }
  const ok = rs.every(
    r => Array.isArray(r) && r.every(p => typeof p?.text === 'string' && typeof p?.prio === 'number'),
  )
  return ok ? (rs as Part[][]).map(line) : null
}

// Wide enough for every part of any case.
const WIDE = 400

function props(hasSurvey = false, bodyColumns = WIDE) {
  return {
    hasSurvey,
    isWorking: false,
    maxRows: 10,
    bodyColumns,
    scroll: { offset: 0, bodyRows: 10 },
    view: {},
  } as const
}

// The engine beneath the plugin: it draws one Text of its own where the module leaves the band.
function engineBand(on: On) {
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return Text({ children: ENGINE })
  })
}

type Mounted = Awaited<ReturnType<T$['ui']['mount']>>
// An element as FoundElement.children describe it.
type Node = { type?: string; props?: Record<string, unknown>; children?: unknown[] }

// What a described element shows: its strings in document order.
function shows(x: unknown): string {
  return typeof x === 'string' ? x : ((x as Node).children ?? []).map(shows).join('')
}

// The rows of a mounted band: the Texts its Box holds, each with its parts (the Texts in it that are not the
// separators « · » and the space after the dot).
async function bandRows(ui: Mounted) {
  const box = await ui.find({ type: 'Box' })
  return ((box?.children ?? []) as Node[]).map(row => ({
    text: shows(row),
    props: row.props ?? {},
    inline: (row.children ?? []) as Node[],
  }))
}

// The band as drawn on `surface`: the texts of its rows, or null when the engine's own band is drawn.
async function drawn(
  $: T$,
  surface: (typeof SURFACES)[number],
  hasSurvey = false,
  bodyColumns = WIDE,
): Promise<string[] | null> {
  const ui = await $.ui.mount({
    plugin: 'foreman',
    component: 'AbovePrompt',
    surface,
    props: props(hasSurvey, bodyColumns),
  })
  try {
    if (await ui.find({ type: 'Text', text: ENGINE })) {
      return null
    }
    return (await bandRows(ui)).map(t => t.text)
  } finally {
    await ui.unmount()
  }
}

for (const c of (cases as Case[]).filter(c => c.op === 'tick')) {
  test(`band: ${c.name}`, async ($, on) => {
    const clock = mock.clock(on)
    on('session.id', () => ({ value: c.engine.session }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
    on('agent.list', () => ({ value: [] }))
    on('process.run', (_, e) =>
      e.argv[3] === 'start'
        ? answer({ exitCode: 0, stdout: c.engine.start, stderr: '' })
        : answer(c.run ?? { reject: 'no call expected' }),
    )
    on('ui.status', () => ({ value: undefined }))
    on('ui.log', () => ({ value: undefined }))
    on('prompt.submit', (_, e) => ({ text: e.text }))
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    engineBand(on)

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    for (const surface of SURFACES) {
      expect(await drawn($, surface)).toBe(null) // nothing before the first tick
    }
    await clock.advance(TICK)
    const want = c.stdin === null ? null : rows(c.run)
    for (const surface of SURFACES) {
      expect(await drawn($, surface)).toEqual(want)
    }
  })
}

for (const c of (cases as Case[]).filter(c => c.op === 'status')) {
  test(`/foreman:status: ${c.name}`, async ($, on) => {
    const runs: { argv: readonly string[]; stdin?: string }[] = []
    const statuses: (string | undefined)[] = []
    on('session.id', () => ({ value: c.engine.session }))
    on('session.version', () => ({ value: { version: c.engine.version, base: c.engine.version } }))
    on('process.run', (_, e) => {
      if (e.argv[3] === 'start') {
        return answer({ exitCode: 0, stdout: c.engine.start, stderr: '' })
      }
      runs.push({ argv: e.argv, stdin: e.init?.stdin })
      return answer(c.run ?? { reject: 'no call expected' })
    })
    on('ui.status', (_, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    on('command.run', () => ({ text: 'the command file ran' }))

    await $.session.start({ cwd: c.engine.cwd, surface: 'terminal', isInteractive: true })
    statuses.length = 0
    const out = await $.command.run({ command: 'foreman:status' })

    expect(runs.length).toBe(1)
    expect(runs[0]!.argv.slice(1)).toEqual(['hook', 'mod', 'status'])
    expect(JSON.parse(runs[0]!.stdin ?? 'null')).toEqual(c.stdin)
    const text = parsed(c.run).text
    if (c.run?.exitCode === 0 && typeof text === 'string') {
      expect(out.text).toBe(text)
    } else {
      expect(out.text?.startsWith('foreman: hook mod status failed (')).toBe(true)
    }
    expect(statuses).toEqual([]) // the command's output says it; no status line
  })
}

const PARTS = {
  level: 'ask',
  rows: [
    [
      { text: '●', role: 'dot', prio: 0 },
      { text: '1 question for you', role: 'ask', prio: 0 },
      { text: 'memory 84K of 200K', role: 'plain', prio: 2 },
      { text: 'foreman', role: 'dim', prio: 7 },
    ],
    [
      { text: 'Light the harbor (HB-7 T1) 23 min', role: 'dim', prio: 1 },
      { text: '+{n} more', role: 'more', prio: 0, n: 2 },
    ],
  ],
}
const BAND = ['● 1 question for you · memory 84K of 200K · foreman', 'Light the harbor (HB-7 T1) 23 min · +2 more']
const MARKED = JSON.stringify({ band: PARTS, due: false, marked: true })

// An active session whose ticks answer `ticks` in turn (then the last one again); `start` its session.start answer.
function engine(on: On, ticks: string[], start = ACTIVE) {
  const asked: string[] = []
  on('session.id', () => ({ value: 'S-orch' }))
  on('session.version', () => ({ value: { version: '2.1.314', base: '2.1.314' } }))
  on('agent.list', () => ({ value: [] }))
  on('process.run', (_, e) => {
    asked.push(e.argv[3] ?? '')
    if (e.argv[3] === 'start') {
      return answer({ exitCode: 0, stdout: start, stderr: '' })
    }
    const next = ticks.length > 1 ? ticks.shift()! : (ticks[0] ?? '')
    return next === 'fail' ? answer({ exitCode: 1, stdout: '', stderr: 'boom' }) : answer({ exitCode: 0, stdout: next })
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  engineBand(on)
  return { asked }
}

test('a session that loses the mark drops the band at its next tick, and gets it back when marked again', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, [MARKED, '{"marked": false}\n', MARKED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toEqual(BAND)
  await clock.advance(TICK)
  for (const surface of SURFACES) {
    expect(await drawn($, surface)).toBe(null)
  }
  await clock.advance(TICK)
  expect(await drawn($, 'desktop')).toEqual(BAND)
})

test('a band already mounted is drawn again when a tick changes it', async ($, on) => {
  const clock = mock.clock(on)
  const laterBand = {
    level: 'ok',
    rows: [
      [
        { text: '●', role: 'dot', prio: 0 },
        { text: 'memory 110K of 200K', role: 'plain', prio: 2 },
      ],
      [{ text: 'nothing running', role: 'dim', prio: 1 }],
    ],
  }
  const later = ['● memory 110K of 200K', 'nothing running']
  engine(on, [MARKED, JSON.stringify({ band: laterBand, due: false, marked: true }), '{"marked": false}\n'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  const ui = await $.ui.mount({ plugin: 'foreman', component: 'AbovePrompt', surface: 'terminal', props: props() })
  expect((await bandRows(ui)).map(t => t.text)).toEqual(BAND)
  await clock.advance(TICK)
  expect((await bandRows(ui)).map(t => t.text)).toEqual(later)
  await clock.advance(TICK)
  expect((await ui.find({ type: 'Text' }))?.text).toBe(ENGINE)
  await ui.unmount()
})

test('a band of another shape is not drawn', async ($, on) => {
  const clock = mock.clock(on)
  const old = JSON.stringify({ band: ['foreman · pass 10:05', 'no agents running'], due: false, marked: true })
  const noPrio = JSON.stringify({ band: { level: 'ok', rows: [[{ text: '●', role: 'dot' }]] }, due: false, marked: true })
  engine(on, [MARKED, old, MARKED, noPrio])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toEqual(BAND)
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toBe(null)
  await clock.advance(TICK)
  expect(await drawn($, 'desktop')).toEqual(BAND)
  await clock.advance(TICK)
  expect(await drawn($, 'desktop')).toBe(null)
})

test('a tick that fails drops the band', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, [MARKED, 'fail'])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toEqual(BAND)
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toBe(null)
})

test('a survey holds the band', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, [MARKED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  for (const surface of SURFACES) {
    expect(await drawn($, surface, true)).toBe(null)
  }
})

test('a /reload-plugins (session.start again) clears the band until the next tick', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, [MARKED])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toEqual(BAND)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  expect(await drawn($, 'terminal')).toBe(null)
  await clock.advance(TICK)
  expect(await drawn($, 'terminal')).toEqual(BAND)
})

test('a session outside any project draws no band and asks for no tick', async ($, on) => {
  const clock = mock.clock(on)
  const { asked } = engine(on, [MARKED], '{"active": false}\n')
  await $.session.start({ cwd: '/elsewhere', surface: 'terminal', isInteractive: true })
  await clock.advance(3 * TICK)
  expect(asked).toEqual(['start'])
  for (const surface of SURFACES) {
    expect(await drawn($, surface)).toBe(null)
  }
})

// ---------- the band by width ----------

// The state of the live band captures, in Spanish, with invented names: four questions, memory, one
// branch to integrate, the next pass, three jobs and one more.
const FULL = {
  level: 'ask',
  rows: [
    [
      { text: '●', role: 'dot', prio: 0 },
      { text: '4 preguntas para ti', role: 'ask', prio: 0 },
      { text: 'memoria 121K de 200K', role: 'plain', prio: 2 },
      { text: 'por integrar 1', role: 'plain', prio: 3 },
      { text: 'próxima pasada 16:59', role: 'plain', prio: 4 },
      { text: 'foreman', role: 'dim', prio: 7 },
    ],
    [
      { text: 'Pintar el faro (FA-12 T1) 82 min', role: 'dim', prio: 1 },
      { text: 'Reparar el muelle (FA-53 T3) 42 min', role: 'dim', prio: 2 },
      { text: 'Boyas nuevas (FA-54 T0) 14 min', role: 'dim', prio: 3 },
      { text: '+{n} más', role: 'more', prio: 0, n: 1 },
    ],
  ],
}

// A part as the test reads it: its text and its style (`color`, `bold`, `dimColor` of its Text).
type Seen = [string, string]
const DOT: Seen = ['●', 'warning']
const ASK: Seen = ['4 preguntas para ti', 'bold warning']
const MEMORY: Seen = ['memoria 121K de 200K', 'plain']
const READY: Seen = ['por integrar 1', 'plain']
const NEXT: Seen = ['próxima pasada 16:59', 'plain']
const FOREMAN: Seen = ['foreman', 'dim']
const JOB1: Seen = ['Pintar el faro (FA-12 T1) 82 min', 'dim']
const JOB2: Seen = ['Reparar el muelle (FA-53 T3) 42 min', 'dim']
const JOB3: Seen = ['Boyas nuevas (FA-54 T0) 14 min', 'dim']
const more = (n: number): Seen => [`+${n} más`, 'dim']

// Terminal columns: the band gets `bodyColumns` = columns − 5 (the engine's `[-]`), less BAND_MARGIN (1) of its own.
const BY_WIDTH: { columns: number; rows: Seen[][] }[] = [
  { columns: 160, rows: [[DOT, ASK, MEMORY, READY, NEXT, FOREMAN], [JOB1, JOB2, JOB3, more(1)]] },
  { columns: 100, rows: [[DOT, ASK, MEMORY, READY, NEXT, FOREMAN], [JOB1, JOB2, more(2)]] },
  { columns: 80, rows: [[DOT, ASK, MEMORY, READY], [JOB1, more(3)]] },
  { columns: 60, rows: [[DOT, ASK, MEMORY], [JOB1, more(3)]] },
  { columns: 40, rows: [[DOT, ASK], [more(4)]] },
]

function styleOf(props: Record<string, unknown>): string {
  const out = [props.bold === true ? 'bold' : '', typeof props.color === 'string' ? props.color : '']
  return out.filter(Boolean).join(' ') || (props.dimColor === true ? 'dim' : 'plain')
}

// The band drawn on `surface` at `bodyColumns`: per row, its parts as [text, style], the separators, the rows'
// own texts and wraps, and the borders of its Boxes.
async function parts($: T$, surface: (typeof SURFACES)[number], bodyColumns: number) {
  const mount = { plugin: 'foreman', component: 'AbovePrompt', surface, props: props(false, bodyColumns) } as const
  const ui = await $.ui.mount(mount)
  try {
    const rows = await bandRows(ui)
    const isGap = (t: Node) => [' · ', ' '].includes(shows(t))
    const boxes = await ui.findAll({ type: 'Box' })
    return {
      rows: rows.map(r => r.inline.filter(t => !isGap(t)).map(t => [shows(t), styleOf(t.props ?? {})] as Seen)),
      gaps: rows.flatMap(r => r.inline.filter(isGap).map(t => [shows(t), styleOf(t.props ?? {})] as Seen)),
      lines: rows.map(r => r.text),
      wraps: rows.map(r => r.props.wrap),
      borders: boxes.map(b => b.props.borderStyle).filter(b => b !== undefined),
    }
  } finally {
    await ui.unmount()
  }
}

for (const { columns, rows } of BY_WIDTH) {
  test(`the band at ${columns} columns keeps the parts that fit, whole, by prio`, async ($, on) => {
    const clock = mock.clock(on)
    engine(on, [JSON.stringify({ band: FULL, due: false, marked: true })])
    await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
    await clock.advance(TICK)
    for (const surface of SURFACES) {
      const seen = await parts($, surface, columns - 5)
      expect(seen.rows).toEqual(rows)
      expect(seen.lines).toEqual(rows.map(r => r.map(([t]) => t).join(' · ').replace(/^● · /, '● ')))
      for (const line of seen.lines) {
        expect([...line].length <= columns - 6).toBe(true) // no part cut: the row fits whole
      }
      expect(seen.gaps.every(([t, style]) => (t === ' · ' || t === ' ') && style === 'dim')).toBe(true)
      expect(seen.wraps).toEqual(['truncate-end', 'truncate-end'])
      expect(seen.borders).toEqual([])
    }
  })
}

test('the dot takes the colour of the level; warn and bad parts theirs', async ($, on) => {
  const clock = mock.clock(on)
  const band = (level: string, role: string) => ({
    band: {
      level,
      rows: [
        [
          { text: '●', role: 'dot', prio: 0 },
          { text: 'memory 190K of 200K', role, prio: 2 },
        ],
        [{ text: 'nothing running', role: 'dim', prio: 1 }],
      ],
    },
    due: false,
    marked: true,
  })
  engine(on, [
    JSON.stringify(band('ok', 'plain')),
    JSON.stringify(band('ok', 'warn')),
    JSON.stringify(band('bad', 'bad')),
    JSON.stringify(band('odd', 'odd')),
  ])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  const want = [
    [DOT[0], 'success', 'plain'],
    [DOT[0], 'success', 'warning'],
    [DOT[0], 'error', 'error'],
    [DOT[0], 'error', 'plain'], // a level or a role it does not know: the dot red, the part plain
  ]
  for (const [dot, color, style] of want) {
    await clock.advance(TICK)
    for (const surface of SURFACES) {
      const seen = await parts($, surface, 95)
      expect(seen.rows[0]).toEqual([
        [dot!, color!],
        ['memory 190K of 200K', style!],
      ])
    }
  }
})

test('parts of prio 0 stay when even they do not fit, and the row cuts its end', async ($, on) => {
  const clock = mock.clock(on)
  const late = {
    level: 'bad',
    rows: [
      [
        { text: '●', role: 'dot', prio: 0 },
        { text: '12 questions for you', role: 'ask', prio: 0 },
        { text: 'no orchestrator session marked', role: 'bad', prio: 0 },
        { text: 'foreman', role: 'dim', prio: 7 },
      ],
      [{ text: 'nothing running', role: 'dim', prio: 1 }],
    ],
  }
  engine(on, [JSON.stringify({ band: late, due: false, marked: true })])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  for (const surface of SURFACES) {
    const seen = await parts($, surface, 35)
    expect(seen.rows[0]).toEqual([
      ['●', 'error'],
      ['12 questions for you', 'bold warning'],
      ['no orchestrator session marked', 'error'],
    ])
    expect(seen.wraps[0]).toBe('truncate-end')
    expect(seen.rows[1]).toEqual([['nothing running', 'dim']])
  }
})

test('a `more` part of 0 is not drawn until the band leaves jobs out', async ($, on) => {
  const clock = mock.clock(on)
  const two = {
    level: 'ok',
    rows: [
      [{ text: '●', role: 'dot', prio: 0 }],
      [
        { text: 'Light the harbor (HB-7 T1) 23 min', role: 'dim', prio: 1 },
        { text: 'Paint the pier (HB-11) 2 min', role: 'dim', prio: 2 },
        { text: '+{n} more', role: 'more', prio: 0, n: 0 },
      ],
    ],
  }
  engine(on, [JSON.stringify({ band: two, due: false, marked: true })])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await clock.advance(TICK)
  for (const surface of SURFACES) {
    expect(await drawn($, surface, false, 95)).toEqual([
      '●',
      'Light the harbor (HB-7 T1) 23 min · Paint the pier (HB-11) 2 min',
    ])
    expect(await drawn($, surface, false, 55)).toEqual(['●', 'Light the harbor (HB-7 T1) 23 min · +1 more'])
    expect(await drawn($, surface, false, 20)).toEqual(['●', '+2 more'])
  }
})
