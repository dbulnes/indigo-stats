import { useEffect, useRef, useState } from 'react'
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
const providerName = (provider: string) => provider === 'typesafe' ? 'TypeSafe (direct)' : 'jevmodel.org'

export function ClothingPanel({ timezone, units }: { timezone: string; units: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [pending, setPending] = useState<'read' | 'provider' | 'generate' | null>('read')
  const [error, setError] = useState('')
  const [clock, setClock] = useState(Date.now())
  const [retryAt, setRetryAt] = useState(0)
  const [synchronized, setSynchronized] = useState(false)
  // One request sequence per mounted panel, including its final cache read.
  const active = useRef<AbortController | null>(null)

  function acceptStatus(controller: AbortController, next: Status) {
    if (active.current !== controller || controller.signal.aborted) return
    setStatus(next)
    setSynchronized(true)
    setClock(Date.now())
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
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
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
  const time = (ts: number) => new Date(ts * 1000).toLocaleString([], { timeZone: timezone })
  const results = status?.results.filter(r => r.end * 1000 > clock) ?? []
  const retrySeconds = Math.max(0, Math.ceil((retryAt - clock) / 1000))
  const locked = pending !== null || status?.busy

  return <section className="panel clothing-panel" aria-labelledby="clothing-heading" aria-busy={pending !== null}>
    <h2 id="clothing-heading">What to wear today</h2>
    <p className="muted">Functional clothing for light outdoor activity and average temperature sensitivity. AI guidance, not a safety forecast.</p>
    <p className="muted">Morning 06–11 · Afternoon 11–17 · Evening 17–23 ({timezone}). Completed periods are skipped.</p>
    <div className="clothing-actions">
      <label>Provider
        <select value={status?.provider ?? 'jevmodel'} disabled={locked || !status}
          onChange={e => void request('provider', e.target.value)}>
          <option value="jevmodel">jevmodel.org (independent provider)</option>
          <option value="typesafe">TypeSafe (direct)</option>
        </select>
      </label>
      <button className="secondary" disabled={locked || !synchronized || !status?.configured || retrySeconds > 0 || !!status?.weather_error}
        onClick={() => void request('generate')}>
        {pending === 'generate' ? 'Generating…' : 'Generate'}
      </button>
      <button className="secondary" disabled={pending !== null} onClick={() => void request('read')}>
        {pending === 'read' ? 'Loading…' : 'Refresh saved results'}
      </button>
    </div>
    <p className="muted">Generate sends compact weather summaries to the selected external provider and can spend its credits: up to three requests, eight questions each. No coordinates or sensor identifiers are sent. Fresh matching cached periods are reused for up to one hour. Refreshing, opening this dashboard, and changing provider never generate recommendations.</p>
    <p role="status" className="muted">
      {pending === 'provider' ? 'Saving provider selection…' : pending === 'generate' ? 'Generation is running. Leaving this panel does not cancel server-side calls.' : status?.busy ? 'Generation is already running. Refresh saved results to check progress; do not submit again.' : ''}
    </p>
    {retrySeconds > 0 && <p className="muted">Generation cooldown: {retrySeconds}s remaining.</p>}
    {status && !status.configured && <p className="notice">Set the {providerName(status.provider)} API key in the masked Unraid container template field, then Apply. For other installations, set <code>{status.provider === 'typesafe' ? 'CLOTHING_TYPESAFE_API_KEY' : 'CLOTHING_JEVMODEL_API_KEY'}</code> in the container environment. Keys are never entered in this dashboard.</p>}
    {error && <p role="alert" className="notice">{error}</p>}
    {status?.error && !error.includes(status.error) && <p role="alert" className="notice">Last generation: {status.error}</p>}
    {status && !synchronized && <p className="notice">Refresh saved results successfully before generating again.</p>}
    {status?.weather_error && <p className="notice">Weather unavailable: {status.weather_error} Saved results below may no longer reflect current conditions.</p>}
    {!pending && status && !results.length && <p className="muted">No saved recommendations for remaining periods today.</p>}
    <div className="clothing-periods">
      {results.map(result => {
        const forecast = result.state.forecast
        const observation = result.state.current_observation
        const stale = result.stale || clock / 1000 - result.generated >= 3600
        return <article key={result.start} className="clothing-period">
          <h3>{result.period} · {forecast.local_hours}</h3>
          <p className="muted">Generated {time(result.generated)} · {providerName(result.provider)} · {stale ? 'Stale — generate to refresh' : 'Cached'}</p>
          <p>Open-Meteo forecast used: {forecast.temperature_f.map(temp).join('–')} (feels like {forecast.apparent_temperature_f.map(temp).join('–')}); rain chance up to {forecast.precipitation_probability_max_percent}%; wind up to {Math.round(forecast.wind_speed_max_mph * (units === 'metric' ? 1.609344 : 1))} {units === 'metric' ? 'km/h' : 'mph'}.</p>
          <p className="muted">Forecast fetched {time(result.forecast_fetched)}.</p>
          <p className="muted">{observation
            ? `PurpleAir observation at generation: ${temp(observation.temperature_f)}, ${observation.humidity_percent}% RH, ${observation.age_seconds}s old at generation. Not a forecast.`
            : result.warning || 'Current observation unavailable; forecast-only guidance.'}</p>
          <h4>Relative preference</h4>
          <p className="muted">Percentages compare alternatives within each group, not the chance of comfort. Each group assumes other layers are appropriate; the top choices are not a guaranteed coherent outfit.</p>
          {result.state.assumptions && <details>
            <summary>Activity and layer assumptions</summary>
            <ul>{Object.entries(result.state.assumptions).map(([key, assumption]) =>
              <li key={key}>{labels[key] ?? key.replaceAll('_', ' ')}: {assumption}</li>
            )}</ul>
          </details>}
          {Object.entries(labels).map(([key, label]) => {
            const value = result.judgments[key]
            const ranking = Array.isArray(value) ? value : []
            return ranking.length ? <details key={key}>
              <summary>{label}: {ranking[0].label} · {Math.round(ranking[0].probability * 100)}%</summary>
              <ol>{ranking.map(item => <li key={item.label}>{item.label}: {Math.round(item.probability * 100)}%</li>)}</ol>
            </details> : <p key={key}>{label}: unavailable</p>
          })}
          <h4>Estimated usefulness</h4>
          <p className="muted">Independent estimates that each accessory would be useful, not guarantees of comfort or protection.</p>
          <ul>{['umbrella', 'beanie', 'gloves'].map(item => {
            const value = result.judgments[item]
            return <li key={item}>{item}: {typeof value === 'number' ? `${Math.round(value * 100)}%` : 'unavailable'}</li>
          })}</ul>
        </article>
      })}
    </div>
    {status && <p className="muted">Provider-reported cumulative usage across providers: {status.usage.input_tokens.toLocaleString()} input / {status.usage.output_tokens.toLocaleString()} output tokens.
      {status.unknown_usage_requests > 0
        ? ` ${status.unknown_usage_requests} request(s) have missing or incomplete usage. Reported totals are incomplete; check the provider account for billing.`
        : ' These are reported token counts, not a cost estimate.'}
    </p>}
  </section>
}
