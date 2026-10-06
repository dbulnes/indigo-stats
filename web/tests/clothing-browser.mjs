// Run `npm run build --prefix web && npm run test:clothing --prefix web`.
// Node 22+, local Chrome only. No real API, credentials, or external requests.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, writeFile, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, extname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'

const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const epoch = Date.parse('2026-09-20T08:30:00Z')
const hour = h => Date.parse(`2026-09-20T${String(h).padStart(2, '0')}:00:00Z`) / 1000
const groups = ['bottoms', 'base_tops', 'midlayers', 'outerwear', 'footwear']
const options = [
  ['Warm trousers', 'Lightweight trousers'], ['Long-sleeved top', 'Short-sleeved top'],
  ['Light sweater', 'No midlayer'], ['Rain shell', 'Wind shell'], ['Waterproof shoes', 'Breathable closed shoes'],
]
const results = ['Morning', 'Afternoon', 'Evening'].map((period, i) => ({
  period: period.toLowerCase(), start: hour([6, 11, 17][i]), end: hour([11, 17, 23][i]),
  generated: epoch / 1000 - 60, forecast_fetched: epoch / 1000 - 120,
  stale: false, provider: 'jevmodel', warning: null,
  judgments: {
    ...Object.fromEntries(groups.map((g, j) => [g, [
      { label: options[j][i % 2], probability: 0.75 },
      { label: options[j][1 - i % 2], probability: 0.25 },
    ]])), umbrella: 0.2, beanie: 0.1, gloves: 0.05,
  },
  state: {
    assumptions: { bottoms: 'Other layers are appropriate.' },
    forecast: { local_hours: ['06–11', '11–17', '17–23'][i],
      temperature_f: [50, 68], apparent_temperature_f: [50, 68],
      precipitation_probability_max_percent: 20, wind_speed_max_mph: 10 },
    current_observation: { temperature_f: 68, humidity_percent: 50, age_seconds: 60 },
  },
}))
const fresh = () => ({
  provider: 'jevmodel', configured: true, busy: false, results: structuredClone(results),
  error: null, weather_error: null, usage: { input_tokens: 120, output_tokens: 30 },
  unknown_usage_requests: 0, retry_after: 0,
})
let status = fresh()
let failRead = false
let appTimezone = 'UTC'
const requests = []
const unexpected = []
const server = createServer(async (req, res) => {
  try {
    const path = new URL(req.url, 'http://localhost').pathname
    if (path.startsWith('/api/')) {
      let body = ''
      for await (const chunk of req) body += chunk
      requests.push({ path, method: req.method, body: body ? JSON.parse(body) : null })
      let data
      if (path === '/api/clothing' || path === '/api/clothing/generate') {
        if (req.method === 'PUT') status.provider = JSON.parse(body).provider
        if (req.method === 'GET' && failRead) {
          res.writeHead(503, { 'Content-Type': 'application/json' }).end('{"detail":"Synthetic read failure"}')
          return
        }
        data = status
      } else if (path === '/api/settings') data = { UNITS: 'metric' }
      else if (path === '/api/latest') data = { reading: null, stale: true, server_time: epoch / 1000 }
      else if (path === '/api/status') data = { version: 'test', timezone: appTimezone, jobs: [], readings: { n: 0, first: null, last: null }, database_bytes: 0, backup_scope: 'test' }
      else if (path === '/api/history') data = { points: [], forecasts: [], step: 60, stats: { samples: 0 } }
      else if (path === '/api/forecast') data = { points: [], daily: [] }
      else if (path === '/api/astronomy') data = { available: false }
      else { unexpected.push(`${req.method} ${path}`); res.writeHead(404).end(); return }
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify(data))
      return
    }
    const file = resolve(dist, `.${path === '/' ? '/index.html' : path}`)
    if (!file.startsWith(resolve(dist) + sep)) { res.writeHead(403).end(); return }
    const content = await readFile(file)
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.webmanifest': 'application/manifest+json' }
    res.writeHead(200, { 'Content-Type': types[extname(file)] || 'application/octet-stream' })
    res.end(content)
  } catch { res.writeHead(404).end() }
})

let chrome, socket, profile
let nextId = 0
const pending = new Map()
const exceptions = []
const blocked = []
function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 5000)
    pending.set(id, { resolve, reject, timer })
    socket.send(JSON.stringify({ id, method, params }))
  })
}
async function evaluate(expression) {
  const response = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  assert.ok(!response.exceptionDetails, JSON.stringify(response.exceptionDetails))
  return response.result.value
}
async function wait(expression, label = expression) {
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    if (await evaluate(`Boolean(${expression})`)) return
    await sleep(50)
  }
  throw new Error(`Timed out: ${label}`)
}
const panel = `document.querySelector('.clothing-panel')`
const button = text => `[...${panel}.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)})`
const tab = name => `[...${panel}.querySelectorAll('[role=tab]')].find(b => b.textContent.trim().startsWith(${JSON.stringify(name)}))`
async function click(expression) { await evaluate(`${expression}.click()`); await sleep(30) }
async function selected(name) { await wait(`${tab(name)}?.getAttribute('aria-selected') === 'true'`) }
async function refresh() {
  await click(button('Refresh saved results'))
  await wait(`${panel}.getAttribute('aria-busy') === 'false'`)
}
async function key(key) {
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key })
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key })
}

async function screenshot(name) {
  if (!process.env.CLOTHING_SCREENSHOT_DIR) return
  // Keep the app's sticky mobile navigation outside the panel crop.
  await evaluate(`window.scrollTo(0, ${panel}.getBoundingClientRect().top + scrollY - 100)`)
  await sleep(400)
  const clip = await evaluate(`(() => { const r = ${panel}.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height, scale: 1 }; })()`)
  const { data } = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip })
  await mkdir(process.env.CLOTHING_SCREENSHOT_DIR, { recursive: true })
  await writeFile(join(process.env.CLOTHING_SCREENSHOT_DIR, `${name}.png`), Buffer.from(data, 'base64'))
}

try {
  await readFile(join(dist, 'index.html')) // Fail before launching if build is missing.
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  profile = await mkdtemp(join(tmpdir(), 'indigo-clothing-browser-'))
  let chromeStderr = ''
  chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
    '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update',
    '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] })
  chrome.stderr?.on('data', chunk => { chromeStderr += chunk })
  let launchError
  chrome.on('error', error => { launchError = error })
  let port
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    if (launchError) throw launchError
    if (chrome.exitCode !== null) throw new Error(`Chrome exited: ${chrome.exitCode}\\n${chromeStderr}`)
    try { port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break } catch {}
    await sleep(100)
  }
  assert.ok(port, `Chrome CDP startup exceeded 30 seconds; set CHROME_BIN to a local Chrome binary\\n${chromeStderr}`)
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(10000) })).json()
  socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('CDP WebSocket startup timeout')), 10000)
    socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
    socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP WebSocket error')) }, { once: true })
  })
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (message.id) {
      const task = pending.get(message.id)
      if (!task) return
      clearTimeout(task.timer); pending.delete(message.id)
      if (message.error) task.reject(new Error(JSON.stringify(message.error)))
      else task.resolve(message.result)
    } else if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails)
    else if (message.method === 'Fetch.requestPaused') {
      const { requestId, request } = message.params
      if (request.url.startsWith(origin + '/')) void command('Fetch.continueRequest', { requestId }).catch(() => {})
      else {
        blocked.push(request.url)
        void command('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }).catch(() => {})
      }
    }
  })
  await command('Runtime.enable')
  await command('Page.enable')
  await command('Network.enable')
  await command('Network.setBypassServiceWorker', { bypass: true })
  await command('Fetch.enable', { patterns: [{ urlPattern: '*' }] })
  // Browser local time differs from the application's configured UTC timezone.
  await command('Emulation.setTimezoneOverride', { timezoneId: 'America/Los_Angeles' })
  await command('Emulation.setDeviceMetricsOverride', { width: 1200, height: 900, deviceScaleFactor: 1, mobile: false })
  await command('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.__testNow = ${epoch};
    const RealDate = Date;
    window.Date = class extends RealDate {
      constructor(...args) { super(...(args.length ? args : [window.__testNow])); }
      static now() { return window.__testNow; }
    };
  ` })
  await command('Page.navigate', { url: origin })
  await wait(`${panel}?.querySelector('[role=tab]')`)
  await wait(`${panel}.getAttribute('aria-busy') === 'false'`)
  assert.equal(await evaluate(`${panel}.querySelector('h2').textContent`), 'What to wear today')
  assert.equal(await evaluate(`${panel}.querySelectorAll('[role=tablist]').length`), 1)
  assert.equal(await evaluate(`${panel}.querySelectorAll('[role=tabpanel]').length`), 1)
  await selected('Morning')
  const networkBefore = requests.length
  assert.equal(await evaluate(`${panel}.querySelectorAll('details.clothing-ranking').length`), 5)
  assert.match(await evaluate(`${panel}.querySelector('details.clothing-ranking summary').innerText`), /Warm trousers/)
  assert.match(await evaluate(`${panel}.querySelector('details.clothing-ranking summary').innerText`), /75%/)
  assert.equal(await evaluate(`${panel}.querySelector('details.clothing-ranking .clothing-bar > span').style.width`), '75%')
  assert.equal(await evaluate(`${panel}.querySelectorAll('details.clothing-ranking[open]').length`), 0)
  await screenshot('clothing-desktop')
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false })
  await screenshot('clothing-mobile')
  await command('Emulation.setDeviceMetricsOverride', { width: 1200, height: 900, deviceScaleFactor: 1, mobile: false })
  await click(`${panel}.querySelector('details.clothing-ranking summary')`)
  assert.ok(await evaluate(`${panel}.querySelector('details.clothing-ranking').open`))
  assert.match(await evaluate(`${panel}.querySelector('details.clothing-ranking').innerText`), /Lightweight trousers/)
  await click(tab('Afternoon'))
  await selected('Afternoon')
  assert.equal(await evaluate(`${panel}.querySelectorAll('details.clothing-ranking[open]').length`), 0)
  await evaluate(`${tab('Afternoon')}.focus()`)
  for (const [pressed, expected] of [['ArrowRight', 'Evening'], ['ArrowRight', 'Morning'], ['ArrowLeft', 'Evening'], ['Home', 'Morning'], ['End', 'Evening']]) {
    await key(pressed); await selected(expected)
    assert.ok(await evaluate(`document.activeElement === ${tab(expected)}`), `${pressed} moves focus`)
  }
  await click(tab('Morning'))
  const methods = `[...${panel}.querySelectorAll('details')].find(d => /method|source/i.test(d.querySelector('summary')?.textContent))`
  assert.equal(await evaluate(`${methods}.open`), false)
  await click(`${methods}.querySelector('summary')`)
  assert.match(await evaluate(`${panel}.innerText`), /10°C/)
  assert.match(await evaluate(`${panel}.innerText`), /20°C/)
  assert.match(await evaluate(`${panel}.innerText`), /16 km\/h/)
  assert.equal(requests.length, networkBefore, 'Tabs, keyboard and disclosures must be network-free')

  const setup = `[...${panel}.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent.trim() === 'Setup / settings')`
  assert.equal(await evaluate(`${setup}.open`), false)
  await click(`${setup}.querySelector('summary')`)
  failRead = true
  await evaluate(`(() => { const s = ${panel}.querySelector('select'); s.value = 'typesafe'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`)
  await wait(`${panel}.getAttribute('aria-busy') === 'false' && ${panel}.innerText.includes('Could not refresh')`)
  assert.equal(await evaluate(`${panel}.querySelector('select').value`), 'typesafe')
  assert.ok(await evaluate(`${button('Generate')}.disabled`), 'Unverified provider mutation locks Generate')
  assert.equal(requests.filter(r => r.method === 'POST').length, 0, 'Provider change must not generate')
  assert.deepEqual(requests.find(r => r.method === 'PUT').body, { provider: 'typesafe' })
  failRead = false
  await refresh()
  await click(tab('Evening'))
  await click(button('Generate'))
  await wait(`${panel}.getAttribute('aria-busy') === 'false'`)
  assert.deepEqual(requests.filter(r => r.method === 'POST'), [
    { path: '/api/clothing/generate', method: 'POST', body: { provider: 'typesafe' } },
  ], 'Generate retains provider and does not scope request to selected period')
  await click(`${setup}.querySelector('summary')`)
  assert.equal(await evaluate(`${setup}.open`), false)

  status.results = [structuredClone(results[0])]
  status.results[0].stale = true
  status.error = 'Synthetic partial generation failure'
  status.unknown_usage_requests = 2
  await refresh()
  await click(tab('Afternoon'))
  assert.match(await evaluate(`${panel}.querySelector('[role=tabpanel]').innerText`), /no .*recommendation|no .*result|not .*generated/i)
  await click(tab('Morning'))
  assert.match(await evaluate(`${panel}.innerText`), /stale/i)
  assert.match(await evaluate(`${panel}.innerText`), /Synthetic partial generation failure/)
  assert.match(await evaluate(`${panel}.innerText`), /unknown|unreported|missing.*usage|incomplete.*usage/i)
  status.configured = false
  await refresh()
  assert.ok(await evaluate(`${button('Generate')}.disabled`))
  assert.match(await evaluate(`${panel}.innerText`), /API key|CLOTHING_TYPESAFE_API_KEY/)
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false })
  await evaluate(`${panel}.querySelectorAll('details').forEach(d => { d.open = true })`)
  await sleep(100)
  assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), '390px viewport has no horizontal overflow')
  await click(tab('Morning'))
  await evaluate(`${tab('Morning')}.focus()`)
  await evaluate(`window.__testNow = ${hour(12) * 1000}`)
  await wait(`![...${panel}.querySelectorAll('[role=tab]')].some(b => b.textContent.includes('Morning'))`, 'Completed morning tab disappears')
  await selected('Afternoon')
  assert.ok(await evaluate(`document.activeElement === ${tab('Afternoon')}`), 'Expired focused tab moves focus to remaining selection')
  assert.equal(await evaluate(`${panel}.querySelectorAll('[role=tabpanel]').length`), 1)
  await evaluate(`${panel}.querySelector('select').focus()`)
  await evaluate(`window.__testNow = ${hour(17) * 1000}`)
  await selected('Evening')
  assert.ok(await evaluate(`document.activeElement === ${panel}.querySelector('select')`), 'Completion must not steal focus outside tabs')
  status = fresh()
  await refresh()
  await evaluate(`${tab('Evening')}.focus()`)
  await evaluate(`window.__testNow = ${hour(23) * 1000}`)
  await wait(`${panel}.querySelector('h2').textContent === 'What to wear tomorrow'`)
  assert.equal(await evaluate(`${panel}.querySelectorAll('[role=tab]').length`), 3)
  await selected('Morning')
  assert.ok(await evaluate(`document.activeElement === ${tab('Morning')}`), 'Overnight rollover moves focused evening tab to tomorrow morning')
  assert.match(await evaluate(`${panel}.querySelector('.clothing-day').textContent`), /Sep 21/)
  assert.equal(await evaluate(`${panel}.querySelectorAll('details.clothing-ranking').length`), 0, 'Never relabel today’s results as tomorrow')
  assert.equal(await evaluate(`${button('Generate')}.disabled`), false, 'Overnight generation remains available')

  status.results = results.map(r => ({
    ...structuredClone(r), start: r.start + 86400, end: r.end + 86400,
    generated: hour(23), forecast_fetched: hour(23),
  }))
  await refresh()
  assert.match(await evaluate(`${panel}.querySelector('[role=tabpanel]').innerText`), /Warm trousers/)
  await evaluate(`window.__testNow = ${(hour(23) + 3600) * 1000}`)
  await wait(`${panel}.querySelector('h2').textContent === 'What to wear today'`)
  assert.match(await evaluate(`${panel}.querySelector('.clothing-day').textContent`), /Sep 21/)
  assert.match(await evaluate(`${panel}.querySelector('[role=tabpanel]').innerText`), /Warm trousers/, 'Midnight keeps the upcoming day, not the day after it')

  // Reproduce the reported October 5, 23:44 Los Angeles case with another browser timezone.
  appTimezone = 'America/Los_Angeles'
  await command('Emulation.setTimezoneOverride', { timezoneId: 'Asia/Tokyo' })
  await command('Page.reload', { ignoreCache: true })
  await wait(`${panel}?.getAttribute('aria-busy') === 'false' && ${panel}.innerText.includes('America/Los_Angeles')`)
  await evaluate(`window.__testNow = ${Date.parse('2026-10-06T06:44:00Z')}`)
  await wait(`${panel}.querySelector('h2').textContent === 'What to wear tomorrow'`)
  assert.match(await evaluate(`${panel}.querySelector('.clothing-day').textContent`), /Oct 6/)
  assert.equal(await evaluate(`${panel}.querySelectorAll('[role=tab]').length`), 3)
  await evaluate(`window.__testNow = ${Date.parse('2026-10-06T07:01:00Z')}`)
  await wait(`${panel}.querySelector('h2').textContent === 'What to wear today'`)
  assert.match(await evaluate(`${panel}.querySelector('.clothing-day').textContent`), /Oct 6/)
  status.weather_error = 'Fresh, complete Open-Meteo forecasts are required for morning.'
  await refresh()
  assert.ok(await evaluate(`${button('Generate')}.disabled`), 'Missing upcoming-day weather still blocks spending')
  assert.match(await evaluate(`${panel}.innerText`), /Fresh, complete Open-Meteo/)
  assert.equal(requests.filter(r => r.method === 'POST').length, 1, 'Only explicit Generate posts')
  assert.deepEqual(exceptions, [], 'No runtime JavaScript exceptions')
  assert.deepEqual(unexpected, [], 'No unmocked API endpoints')
  assert.deepEqual(blocked, [], 'App makes no external network requests')
  console.log('Clothing browser regression checks passed (desktop and 390px).')
} finally {
  if (socket?.readyState === WebSocket.OPEN) await command('Browser.close').catch(() => {})
  socket?.close()
  if (chrome?.pid && chrome.exitCode === null && !chrome.signalCode) {
    await Promise.race([new Promise(resolve => chrome.once('exit', resolve)), sleep(1500)])
    if (chrome.exitCode === null && !chrome.signalCode) {
      chrome.kill('SIGKILL')
      await new Promise(resolve => chrome.once('exit', resolve))
    }
  }
  for (const task of pending.values()) clearTimeout(task.timer)
  await new Promise(resolve => server.close(resolve))
  if (profile) await rm(profile, { recursive: true, force: true })
}
