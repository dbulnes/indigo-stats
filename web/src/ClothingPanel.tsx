import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight, RefreshCw, SlidersHorizontal } from 'lucide-react'
import './clothing.css'

type Ranking = { label: string; probability: number }[]
type Result = {
  period: string; start: number; end: number; generated: number; stale: boolean
  provider: string; warning: string | null; forecast_fetched: number
  judgments: Record<string, Ranking | number>
  state: {
    assumptions?: Record<string, string>
    forecast: {
      local_hours: string; temperature_f: number[]; apparent_temperature_f: number[]
      precipitation_probability_max_percent: number; wind_speed_max_mph: number
    }
    current_observation: { temperature_f: number; humidity_percent: number; age_seconds: number } | null
  }
}
type Status = {
  provider: string; configured: boolean; busy: boolean; results: Result[]; error: string | null
  usage: { input_tokens: number; output_tokens: number }; unknown_usage_requests: number; retry_after: number
  weather_error?: string | null
}
const labels: Record<string, string> = {
  bottoms: 'Bottoms', base_tops: 'Base tops', midlayers: 'Midlayers', outerwear: 'Outerwear', footwear: 'Footwear',
}
const periods = [
  { id: 'morning', label: 'Morning', hours: '06–11', end: 11 },
  { id: 'afternoon', label: 'Afternoon', hours: '11–17', end: 17 },
  { id: 'evening', label: 'Evening', hours: '17–23', end: 23 },
]
const providerName = (provider: string) => provider === 'typesafe' ? 'TypeSafe (direct)' : 'jevmodel.org'

function PreferenceRanking({ label, ranking }: { label: string; ranking: Ranking }) {
  if (!ranking.length) return <p className="clothing-unavailable">{label}: unavailable</p>
  const top = ranking[0]
  const score = (item: Ranking[number]) => <span className="clothing-score">
    <span className="clothing-bar" aria-hidden="true"><span style={{ width: `${item.probability * 100}%` }} /></span>
    <span>{Math.round(item.probability * 100)}%</span>
  </span>
  return <details className="clothing-ranking">
    <summary>
      <span className="clothing-group-label">{label}</span>
      <span className="clothing-choice">{top.label}</span>
      {score(top)}
      <ChevronRight className="clothing-chevron" size={14} aria-hidden="true" />
    </summary>
    <ul aria-label={`${label} alternatives`}>
      {ranking.slice(1).map(item => <li key={item.label}>
        <span>{item.label}</span>{score(item)}
      </li>)}
    </ul>
  </details>
}

export function ClothingPanel({ timezone, units }: { timezone: string; units: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [pending, setPending] = useState<'read' | 'provider' | 'generate' | null>('read')
  const [error, setError] = useState('')
  const [clock, setClock] = useState(Date.now())
  const [retryAt, setRetryAt] = useState(0)
  const [synchronized, setSynchronized] = useState(false)
  const [selectedPeriod, setSelectedPeriod] = useState('morning')
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const tabFocusBeforeClock = useRef<HTMLButtonElement | null>(null)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  // One request sequence per mounted panel, including its final cache read.
  const active = useRef<AbortController | null>(null)

  function updateClock() {
    tabFocusBeforeClock.current = tabRefs.current.find(tab => tab === document.activeElement) ?? null
    setClock(Date.now())
  }

  // A completed tab disappears. Repair its focus only if the user was on that tab,
  // never when they were reading details or using another part of the dashboard.
  useLayoutEffect(() => {
    const previous = tabFocusBeforeClock.current
    tabFocusBeforeClock.current = null
    if (previous && !previous.isConnected && document.activeElement === document.body) {
      const next = tabRefs.current.find(tab => tab?.isConnected && tab.getAttribute('aria-selected') === 'true')
      const target = next ?? headingRef.current
      target?.focus()
    }
  })

  function acceptStatus(controller: AbortController, next: Status) {
    if (active.current !== controller || controller.signal.aborted) return
    setStatus(next)
    setSynchronized(true)
    updateClock()
    setRetryAt(Date.now() + next.retry_after * 1000)
  }

  async function load(controller: AbortController) {
    const response = await fetch('/api/clothing', { signal: controller.signal, cache: 'no-store' })
    if (!response.ok) throw new Error('Could not load saved clothing recommendations.')
    acceptStatus(controller, await response.json())
  }

  useEffect(() => {
    const controller = new AbortController()
    active.current = controller
    load(controller).catch(() => {
      if (!controller.signal.aborted) setError('Could not load clothing recommendations. Refresh saved results to try again.')
    }).finally(() => {
      if (active.current === controller) { active.current = null; setPending(null) }
    })
    // Local time only: no generation, provider calls, or network polling.
    const timer = window.setInterval(updateClock, 1000)
    return () => {
      active.current?.abort()
      active.current = null
      window.clearInterval(timer)
    }
  }, [])

  async function request(kind: 'read' | 'provider' | 'generate', provider?: string) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setPending(kind)
    setError('')
    let failure = ''
    try {
      if (kind !== 'read') {
        const response = await fetch(kind === 'generate' ? '/api/clothing/generate' : '/api/clothing', {
          method: kind === 'generate' ? 'POST' : 'PUT',
          headers: { 'X-Indigo-Request': '1', 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({ provider: kind === 'provider' ? provider : status?.provider }),
        })
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(typeof body?.detail === 'string' ? body.detail : 'Clothing request failed.')
        }
        acceptStatus(controller, await response.json())
      }
    } catch (e) {
      failure = `${e instanceof Error ? e.message : 'Clothing request failed.'} No automatic retry was made.${kind === 'generate' ? ' The server may have processed some calls; check saved results and usage before generating again.' : ''}`
    }
    if (controller.signal.aborted) return
    try {
      await load(controller)
    } catch {
      if (active.current === controller) setSynchronized(false)
      failure += `${failure ? ' ' : ''}Could not refresh saved results. Displayed data may be outdated.`
    } finally {
      if (active.current === controller) {
        setError(failure)
        active.current = null
        setPending(null)
      }
    }
  }

  const temp = (f: number) => `${Math.round(units === 'metric' ? (f - 32) * 5 / 9 : f)}°${units === 'metric' ? 'C' : 'F'}`
  const tempRange = (values: number[]) => [...new Set(values.map(temp))].join('–')
  const time = (ts: number) => new Date(ts * 1000).toLocaleString([], { timeZone: timezone })
  const shortTime = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], {
    timeZone: timezone, hour: '2-digit', minute: '2-digit',
  })
  const results = status?.results.filter(r => r.end * 1000 > clock) ?? []
  const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: 'numeric', hourCycle: 'h23' }).format(clock))
  const remaining = periods.filter(period => hour < period.end)
  const selected = remaining.find(period => period.id === selectedPeriod) ?? remaining[0]
  const result = results.find(item => item.period === selected?.id)
  const forecast = result?.state.forecast
  const observation = result?.state.current_observation
  const stale = !!result && (result.stale || clock / 1000 - result.generated >= 3600)
  const retrySeconds = Math.max(0, Math.ceil((retryAt - clock) / 1000))
  const locked = pending !== null || status?.busy
  const activity = pending === 'provider' ? 'Saving provider selection…'
    : pending === 'generate' ? 'Generating remaining periods. Leaving this panel does not cancel server-side calls.'
    : status?.busy ? 'Generation is already running. Refresh saved results to check progress.' : ''

  return <section className="panel clothing-panel" aria-labelledby="clothing-heading" aria-busy={pending !== null}>
    <div className="clothing-heading">
      <h2 id="clothing-heading" ref={headingRef} tabIndex={-1}>What to wear today</h2>
      <span className={`clothing-badge${stale ? ' is-stale' : ''}`}>
        {pending === 'read' ? 'Loading…' : result ? stale ? 'Stale' : 'Saved' : 'Not generated'}
      </span>
    </div>
    <p className="clothing-intro">One period at a time. Open a clothing group to compare alternatives.</p>
    {remaining.length > 0 && <div className="clothing-tabs" role="tablist" aria-label="Clothing recommendation period">
      {remaining.map((period, index) => {
        const cached = results.find(item => item.period === period.id)
        return <button key={period.id} type="button" role="tab"
          id={`clothing-tab-${period.id}`} aria-controls="clothing-period-panel"
          aria-selected={selected.id === period.id} tabIndex={selected.id === period.id ? 0 : -1}
          ref={node => { tabRefs.current[index] = node }}
          onClick={() => setSelectedPeriod(period.id)}
          onKeyDown={event => {
            const next = event.key === 'ArrowRight' ? (index + 1) % remaining.length
              : event.key === 'ArrowLeft' ? (index + remaining.length - 1) % remaining.length
              : event.key === 'Home' ? 0 : event.key === 'End' ? remaining.length - 1 : null
            if (next !== null) {
              event.preventDefault()
              setSelectedPeriod(remaining[next].id)
              tabRefs.current[next]?.focus()
            }
          }}>
          <span>{period.label}</span>
          <small>{period.hours}{cached ? ` · ${tempRange(cached.state.forecast.temperature_f)}` : ''}</small>
        </button>
      })}
    </div>}
    {error && <p role="alert" className="notice">{error}</p>}
    {status?.error && !error.includes(status.error) && <p role="alert" className="notice">Last generation: {status.error}</p>}
    {status && !synchronized && <p className="notice">Refresh saved results successfully before generating again.</p>}
    {status?.weather_error && <p className="notice">Weather unavailable: {status.weather_error} Saved results below may no longer reflect current conditions.</p>}
    {selected ? <div key={selected.id} id="clothing-period-panel" role="tabpanel"
      aria-labelledby={`clothing-tab-${selected.id}`} tabIndex={0}>
      {result && forecast ? <>
        <div className="clothing-weather">
          <div>
            <h3>{tempRange(forecast.temperature_f)}</h3>
            <p>Feels {tempRange(forecast.apparent_temperature_f)} · Rain up to {forecast.precipitation_probability_max_percent}% · Wind up to {Math.round(forecast.wind_speed_max_mph * (units === 'metric' ? 1.609344 : 1))} {units === 'metric' ? 'km/h' : 'mph'}</p>
          </div>
          <div className="clothing-snapshot">
            <span>Saved {shortTime(result.generated)} · {providerName(result.provider)}</span>
            <span>Forecast {shortTime(result.forecast_fetched)} · {forecast.local_hours}</span>
          </div>
        </div>
        {stale && <p className="clothing-context-note">These saved rankings are stale. Generate to update when weather is ready.</p>}
        {!observation && <p className="clothing-context-note">Forecast-only guidance · current outdoor observation unavailable.</p>}
        <div className="clothing-section-heading">
          <h4>Relative preference</h4><span>Open a row for alternatives</span>
        </div>
        <div className="clothing-rankings">
          {Object.entries(labels).map(([key, label]) => <PreferenceRanking key={key} label={label}
            ranking={Array.isArray(result.judgments[key]) ? result.judgments[key] as Ranking : []} />)}
        </div>
        <h4 className="clothing-accessory-heading">Accessories <span>· estimated usefulness</span></h4>
        <ul className="clothing-accessories">
          {['umbrella', 'beanie', 'gloves'].map(item => {
            const value = result.judgments[item]
            return <li key={item}><span>{item}</span> <strong>{typeof value === 'number' ? `${Math.round(value * 100)}%` : 'unavailable'}</strong></li>
          })}
        </ul>
        <p className="clothing-caveat">Not measured comfort. Top choices are not a guaranteed outfit.</p>
        <details className="clothing-methodology">
          <summary>Methodology, assumptions and weather sources</summary>
          <p>Choice percentages compare alternatives within each group, not the chance of comfort. Accessory values independently estimate usefulness, not guarantees of comfort or protection.</p>
          <p>Ordinary errands and light outdoor walking, with average temperature sensitivity. Each group assumes appropriate other layers; independently ranked groups do not guarantee a coherent outfit. AI guidance, not a safety forecast.</p>
          {result.state.assumptions && <ul>{Object.entries(result.state.assumptions).map(([key, assumption]) =>
            <li key={key}>{labels[key] ?? key.replaceAll('_', ' ')}: {assumption}</li>
          )}</ul>}
          <p>Generated {time(result.generated)} with {providerName(result.provider)}. Open-Meteo forecast fetched {time(result.forecast_fetched)}, covering {forecast.local_hours} ({timezone}).</p>
          <p>{observation
            ? `PurpleAir observation at generation: ${temp(observation.temperature_f)}, ${observation.humidity_percent}% RH, ${observation.age_seconds}s old at generation. Current context, not a forecast.`
            : result.warning || 'Current observation unavailable; forecast-only guidance.'}</p>
        </details>
      </> : <div className="clothing-empty">
        <h3>{pending === 'read' ? 'Loading saved recommendations…' : `No saved ${selected.label.toLowerCase()} recommendations`}</h3>
        <p>Generate prepares all remaining periods using available weather. Switching tabs never spends credits.</p>
      </div>}
    </div> : <div className="clothing-empty"><h3>Today’s periods are complete</h3><p>Recommendations cover 06–23 in {timezone}. Come back tomorrow.</p></div>}

    <div className="clothing-controls">
      {status && !status.configured && <p className="clothing-context-note">API key needed. Open Setup / settings below.</p>}
      {activity && <p className="clothing-activity" role="status">{activity}</p>}
      {retrySeconds > 0 && <p className="clothing-context-note">Generation cooldown: {retrySeconds}s remaining.</p>}
      <div className="clothing-actions">
        <button className="clothing-generate" disabled={locked || !synchronized || !status?.configured || retrySeconds > 0 || !!status?.weather_error || !remaining.length}
          onClick={() => void request('generate')}>{pending === 'generate' ? 'Generating…' : 'Generate'}</button>
        <button className="clothing-refresh" disabled={pending !== null} onClick={() => void request('read')}>
          <RefreshCw size={14} aria-hidden="true" />{pending === 'read' ? 'Loading…' : 'Refresh saved results'}
        </button>
      </div>
      <p className="clothing-consent">Generate sends compact weather summaries to <strong>{providerName(status?.provider ?? 'jevmodel')}</strong> and may spend credits for all remaining periods. Tabs and refresh never generate.</p>
      {status && <p className="clothing-usage">Reported tokens, all providers: <strong>{status.usage.input_tokens.toLocaleString()}</strong> input / <strong>{status.usage.output_tokens.toLocaleString()}</strong> output.</p>}
      {!!status?.unknown_usage_requests && <p className="clothing-context-note">{status.unknown_usage_requests} request(s) have missing or incomplete usage. Reported totals are incomplete; check the provider account for billing.</p>}
      <details className="clothing-settings">
        <summary><SlidersHorizontal size={14} aria-hidden="true" /> Setup / settings</summary>
        <label>Provider
          <select value={status?.provider ?? 'jevmodel'} disabled={locked || !status}
            onChange={e => void request('provider', e.target.value)}>
            <option value="jevmodel">jevmodel.org (independent provider)</option>
            <option value="typesafe">TypeSafe (direct)</option>
          </select>
        </label>
        <p>Set the {providerName(status?.provider ?? 'jevmodel')} API key in the masked Unraid container template field, then Apply. For other installations, set <code>{status?.provider === 'typesafe' ? 'CLOTHING_TYPESAFE_API_KEY' : 'CLOTHING_JEVMODEL_API_KEY'}</code> in the container environment. Keys are never entered in this dashboard.</p>
        <p>Local periods: morning 06–11, afternoon 11–17, evening 17–23 ({timezone}). Completed periods are skipped. Generate makes up to three requests, eight questions each; fresh matching cached periods are reused for up to one hour.</p>
        <p>No coordinates, addresses, sensor identifiers, IPs, or raw payloads are sent to the clothing provider. Reported token counts are not a price or spending limit. Opening the dashboard and changing provider never generate.</p>
      </details>
    </div>
  </section>
}
