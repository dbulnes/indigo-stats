import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  Activity, ArrowDownToLine, ArrowUpRight, ChevronLeft, ZoomIn, ZoomOut,
  CalendarDays, Check, ChevronRight, CircleHelp, Clock3, Database, Droplets,
  Gauge, History, LayoutDashboard, RefreshCw, Settings2, ShieldCheck,
  Thermometer, Waves, Wind, Sun, Cloud, Moon
} from 'lucide-react'
import { SystemSettings } from './SystemSettings'
import { BackupSettings } from './BackupSettings'
import { weatherCondition, uvLabel, toC, toKmh } from './weather'
import './style.css'

type Metric = 'pm25' | 'aqi' | 'temperature' | 'humidex'
type Point = { ts: number; temperature: number | null; humidity: number | null; pm25: number | null; aqi: number | null; samples: number; pm_min: number | null; pm_max: number | null; flagged: number; above_threshold?: boolean }
type Forecast = {
  valid: number
  fetched: number
  kind: string
  temperature: number | null
  humidity: number | null
  pm25: number | null
  aqi?: number | null
  uv_index: number | null
  precipitation_probability: number | null
  weather_code?: number | null
  wind_speed?: number | null
  apparent_temperature?: number | null
  cloud_cover?: number | null
  sunrise?: number | null
  sunset?: number | null
  temp_min?: number | null
}
type Reading = { ts: number; temperature: number | null; humidity: number | null; pm25: number | null; aqi: number | null; pm_a: number | null; pm_b: number | null; method: string; quality: string; beyond_scale: boolean; temperature_raw: number | null; humidity_raw: number | null; environment_mode: string }
type Latest = { reading: Reading | null; nowcast_aqi: number | null; nowcast_pm25: number | null; nowcast_beyond_scale: boolean; stale: boolean; server_time: number }
type Stats = { samples: number; temp_min: number | null; temp_max: number | null; temp_mean: number | null; humidity_mean: number | null; pm_mean: number | null; pm_max: number | null; pm_min: number | null }
type HistoryData = { points: Point[]; forecasts: Forecast[]; step: number; stats: Stats }
type Status = { version: string; jobs: { name: string; last_attempt: number | null; last_success: number | null; error: string | null }[]; readings: { n: number; first: number | null; last: number | null }; database_bytes: number; timezone: string; backup_scope: string }

const ranges = [
  { label: '1 hour', seconds: 3600 },
  { label: '24 hours', seconds: 86400 },
  { label: '7 days', seconds: 7 * 86400 },
  { label: '30 days', seconds: 30 * 86400 },
  { label: '1 year', seconds: 365 * 86400 },
]

const fmt = (v: number | null | undefined, digits = 1) =>
  v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits })

function aq(pm: number | null) {
  if (pm == null || pm < 0 || !Number.isFinite(pm)) return null
  const c = Math.floor(pm * 10) / 10
  const bands = [
    [0, 9, 0, 50],
    [9.1, 35.4, 51, 100],
    [35.5, 55.4, 101, 150],
    [55.5, 125.4, 151, 200],
    [125.5, 225.4, 201, 300],
    [225.5, 325.4, 301, 500],
  ]
  for (const [lo, hi, a, b] of bands) if (c >= lo && c <= hi) return Math.round(((c - lo) * (b - a)) / (hi - lo) + a)
  return 500
}

function category(a: number | null | undefined) {
  return a == null
    ? 'Waiting for data'
    : a <= 50
    ? 'Good'
    : a <= 100
    ? 'Moderate'
    : a <= 150
    ? 'Unhealthy for sensitive groups'
    : a <= 200
    ? 'Unhealthy'
    : a <= 300
    ? 'Very unhealthy'
    : 'Hazardous'
}

function aqiClass(a: number | null | undefined) {
  if (a == null) return ''
  if (a <= 50) return 'aqi-good'
  if (a <= 100) return 'aqi-moderate'
  if (a <= 150) return 'aqi-usg'
  if (a <= 200) return 'aqi-unhealthy'
  if (a <= 300) return 'aqi-very-unhealthy'
  return 'aqi-hazardous'
}

function aqiDotColor(a: number | null | undefined) {
  if (a == null) return '#4f6074'
  if (a <= 50) return '#70d8c2'
  if (a <= 100) return '#e8d174'
  if (a <= 150) return '#f0a85d'
  if (a <= 200) return '#f27d88'
  if (a <= 300) return '#c88df2'
  return '#a84860'
}

function relative(ts: number | null | undefined) {
  if (!ts) return 'Not yet'
  const s = Math.max(0, Date.now() / 1000 - ts)
  return s < 90
    ? 'Just now'
    : s < 3600
    ? `${Math.floor(s / 60)} min ago`
    : s < 86400
    ? `${Math.floor(s / 3600)} hours ago`
    : `${Math.floor(s / 86400)} days ago`
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, { signal, cache: 'no-store' })
  if (!r.ok) throw new Error('The server could not load this view.')
  return r.json()
}

function humidex(tempF: number | null | undefined, rh: number | null | undefined) {
  if (tempF == null || rh == null) return null
  const tC = ((tempF - 32) * 5) / 9
  const e = (rh / 100) * 6.105 * Math.exp((17.27 * tC) / (237.7 + tC))
  const hC = tC + 0.5555 * (e - 10.0)
  return (hC * 9) / 5 + 32
}

function formatRemaining(sec: number) {
  if (sec <= 0) return 'now'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

const EMPTY: HistoryData = {
  points: [],
  forecasts: [],
  step: 60,
  stats: { samples: 0, temp_min: null, temp_max: null, temp_mean: null, humidity_mean: null, pm_mean: null, pm_max: null, pm_min: null },
}

function App() {
  const [tab, setTab] = useState('Overview')
  const [metric, setMetric] = useState<Metric>('pm25')
  const [range, setRange] = useState(86400)
  const [custom, setCustom] = useState(false)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [customBounds, setCustomBounds] = useState<[number, number] | null>(null)
  const [units, setUnits] = useState<'imperial' | 'metric'>('imperial')

  const [latest, setLatest] = useState<Latest | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [history, setHistory] = useState<HistoryData>(EMPTY)
  const [previous, setPrevious] = useState<HistoryData>(EMPTY)
  const [forecast, setForecast] = useState<Forecast[]>([])
  const [dailyForecast, setDailyForecast] = useState<Forecast[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const [comparison, setComparison] = useState(false)
  const [showForecast, setShowForecast] = useState(true)
  const [threshold, setThreshold] = useState('')
  const [offline, setOffline] = useState(!navigator.onLine)
  const [environmentMode, setEnvironmentMode] = useState('')
  const [anchor, setAnchor] = useState(Math.ceil(Date.now() / 60000) * 60)

  const end = customBounds?.[1] ?? anchor
  const start = customBounds?.[0] ?? end - range

  useEffect(() => {
    get<{ UNITS?: string }>('/api/settings').then(s => {
      if (s.UNITS === 'metric' || s.UNITS === 'imperial') setUnits(s.UNITS)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      setAnchor(Math.ceil(Date.now() / 60000) * 60)
      setTick(t => t + 1)
    }, 60000)
    const on = () => setOffline(false)
    const off = () => setOffline(true)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      clearInterval(id)
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError('')
    const params = `start=${start}&end=${end}&step=60${environmentMode ? `&environment=${environmentMode}` : ''}${threshold !== '' ? `&threshold=${encodeURIComponent(threshold)}` : ''}`
    Promise.all([
      get<Latest>(`/api/latest${environmentMode ? `?environment=${environmentMode}` : ''}`, ctrl.signal),
      get<Status>('/api/status', ctrl.signal),
      get<HistoryData>(`/api/history?${params}`, ctrl.signal),
      get<{ points: Forecast[]; daily?: Forecast[] }>('/api/forecast', ctrl.signal),
      comparison
        ? get<HistoryData>(`/api/history?start=${Math.max(0, start - (end - start))}&end=${start}&step=60${environmentMode ? `&environment=${environmentMode}` : ''}`, ctrl.signal)
        : Promise.resolve(EMPTY),
    ])
      .then(([l, s, h, f, p]) => {
        setLatest(l)
        setStatus(s)
        setHistory(h)
        setForecast(f.points)
        setDailyForecast(f.daily || [])
        setPrevious(p)
      })
      .catch(e => {
        if (e.name !== 'AbortError') setError('Unable to refresh. Check your Tailscale connection; displayed readings may be out of date.')
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false)
      })
    return () => ctrl.abort()
  }, [start, end, tick, comparison, threshold, environmentMode])

  const toggleUnits = async () => {
    const next = units === 'imperial' ? 'metric' : 'imperial'
    setUnits(next)
    try {
      await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Indigo-Request': '1' },
        body: JSON.stringify({ UNITS: next }),
      })
    } catch {
      // Best-effort setting sync
    }
  }

  const tz = status?.timezone ?? 'America/Los_Angeles'
  const time = (ts: number, full = false) =>
    new Date(ts * 1000).toLocaleString(undefined, {
      timeZone: tz,
      ...(full ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' } : { hour: 'numeric', minute: '2-digit' }),
    })
  const date = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })
  const dayName = (ts: number) => {
    const d = new Date(ts * 1000)
    const todayStr = new Date().toLocaleDateString(undefined, { timeZone: tz })
    const dayStr = d.toLocaleDateString(undefined, { timeZone: tz })
    if (todayStr === dayStr) return 'Today'
    return d.toLocaleDateString(undefined, { timeZone: tz, weekday: 'short' })
  }

  const isDaytime = (ts: number) => {
    const d = dailyForecast.find(df => Math.abs(df.valid - ts) < 86400)
    if (d?.sunrise && d?.sunset) {
      return ts >= d.sunrise && ts < d.sunset
    }
    const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23' }).format(new Date(ts * 1000)))
    return hour >= 6 && hour < 20
  }

  const tempUnit = units === 'metric' ? '°C' : '°F'
  const windUnit = units === 'metric' ? 'km/h' : 'mph'
  const formatTemp = (valF: number | null | undefined, digits = 1) => {
    if (valF == null) return '—'
    const val = units === 'metric' ? toC(valF) : valF
    return fmt(val, digits)
  }
  const formatWind = (valMph: number | null | undefined, digits = 1) => {
    if (valMph == null) return '—'
    const val = units === 'metric' ? toKmh(valMph) : valMph
    return fmt(val, digits)
  }

  const metrics: Record<Metric, { title: string; unit: string; color: string; icon: typeof Wind }> = {
    pm25: { title: 'Fine particles', unit: 'µg/m³', color: '#70d8c2', icon: Wind },
    aqi: { title: 'US AQI', unit: 'AQI', color: '#b0a2ff', icon: Gauge },
    temperature: { title: 'Temperature', unit: tempUnit, color: '#f1b686', icon: Thermometer },
    humidex: { title: 'Estimated Humidex', unit: tempUnit, color: '#8db8f9', icon: Droplets },
  }

  const reading = latest?.reading
  const meta = metrics[metric]
  const liveAqi = reading?.aqi ?? (reading?.pm25 != null ? aq(reading.pm25) : null)
  const currentAqi = liveAqi ?? latest?.nowcast_aqi

  function getMetric(obj: any, m: Metric) {
    if (!obj) return null
    if (m === 'aqi') return obj.aqi ?? aq(obj.pm25)
    if (m === 'humidex') {
      const hF = humidex(obj.temperature, obj.humidity)
      return units === 'metric' ? toC(hF) : hF
    }
    if (m === 'temperature') {
      return units === 'metric' ? toC(obj.temperature) : obj.temperature
    }
    return obj[m]
  }

  // Active current weather & forecast lookup
  const currentForecast: any = {}
  const nowHour = Math.floor((reading?.ts ?? Date.now() / 1000) / 3600) * 3600
  for (const f of forecast.filter(f => f.valid === nowHour)) {
    for (const k of ['temperature', 'humidity', 'pm25', 'uv_index', 'precipitation_probability', 'weather_code', 'wind_speed', 'apparent_temperature', 'cloud_cover'] as const) {
      if (f[k] != null) currentForecast[k] = f[k]
    }
    if (f.pm25 != null) currentForecast.aqi = aq(f.pm25)
  }
  const fallbackWeather = forecast.find(f => f.kind === 'weather' && Math.abs(f.valid - nowHour) <= 3600) || forecast.find(f => f.kind === 'weather')
  const activeWeather = { ...fallbackWeather, ...currentForecast }

  const maxUv = forecast.length ? Math.max(...forecast.map(f => f.uv_index || 0)) : 0
  const maxPrecip = forecast.length ? Math.max(...forecast.map(f => f.precipitation_probability || 0)) : 0

  // Merge hourly points by valid timestamp
  const hourlyStripData = useMemo(() => {
    const map = new Map<number, Forecast>()
    for (const f of forecast) {
      if (!map.has(f.valid)) {
        map.set(f.valid, { ...f })
      } else {
        const item = map.get(f.valid)!
        for (const [k, v] of Object.entries(f)) {
          if (v != null) (item as any)[k] = v
        }
        if (f.pm25 != null && item.aqi == null) item.aqi = aq(f.pm25)
      }
    }
    const currentTs = Date.now() / 1000 - 3600
    return [...map.values()]
      .filter(f => f.valid >= currentTs)
      .sort((a, b) => a.valid - b.valid)
      .slice(0, 36)
  }, [forecast])

  // Daily high/low temperature bounds for range bars
  const { dailyMinAll, dailyMaxAll } = useMemo(() => {
    if (!dailyForecast.length) return { dailyMinAll: 40, dailyMaxAll: 80 }
    let min = Infinity
    let max = -Infinity
    for (const d of dailyForecast) {
      if (d.temp_min != null && d.temp_min < min) min = d.temp_min
      if (d.temperature != null && d.temperature > max) max = d.temperature
    }
    return {
      dailyMinAll: Number.isFinite(min) ? min : 40,
      dailyMaxAll: Number.isFinite(max) ? max : 80,
    }
  }, [dailyForecast])

  // Overview 24h PM2.5 trend points
  const recentTrend = useMemo(() => {
    const past24 = Date.now() / 1000 - 86400
    return history.points
      .filter(p => p.ts >= past24)
      .map(p => ({ ts: p.ts, pm25: p.pm25 }))
  }, [history.points])

  // History main chart preparation
  const chart = useMemo(() => {
    const map = new Map<number, Record<string, number | null>>()
    const step = history.step
    const getRow = (t: number) => {
      const ts = Math.floor(t / step) * step
      if (!map.has(ts)) map.set(ts, { ts, sensor: null, forecast: null, previous: null })
      return map.get(ts)!
    }
    for (let t = Math.floor(start / step) * step; t < end; t += step) getRow(t)
    for (const p of history.points) getRow(p.ts).sensor = getMetric(p, metric)
    if (showForecast) {
      const grouped = new Map<number, number[]>()
      for (const f of history.forecasts) {
        const v = getMetric(f, metric)
        if (v != null) {
          const t = Math.floor(f.valid / step) * step
          grouped.set(t, [...(grouped.get(t) ?? []), v])
        }
      }
      for (const [t, vs] of grouped) getRow(t).forecast = vs.reduce((a, b) => a + b, 0) / vs.length
    }
    if (comparison) for (const p of previous.points) getRow(p.ts + end - start).previous = getMetric(p, metric)
    return [...map.values()].sort((a, b) => a.ts! - b.ts!)
  }, [history, previous, metric, showForecast, comparison, start, end, units])

  const hourly = useMemo(() => {
    const values: Array<number[]> = Array.from({ length: 24 }, () => [])
    for (const p of history.points) {
      const v = getMetric(p, metric)
      if (v != null) {
        const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23' }).format(new Date(p.ts * 1000)))
        values[hour].push(v)
      }
    }
    return values.map((vs, h) => ({
      hour: `${h.toString().padStart(2, '0')}:00`,
      value: vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null,
    }))
  }, [history, metric, tz, units])

  const stats = history.stats
  const coverage = Math.min(100, (stats.samples / Math.max(1, (Math.min(end, Date.now() / 1000) - start) / 60)) * 100)
  const hasData = history.points.length > 0

  function applyCustom() {
    const s = Date.parse(startDate) / 1000
    const e = Date.parse(endDate) / 1000
    if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) {
      setError('Choose an end date after the start date.')
      return
    }
    setCustomBounds([s, e])
  }

  function selectRange(seconds: number) {
    setRange(seconds)
    setCustomBounds(null)
    setCustom(false)
  }

  // Weather condition details for Hero
  const nowTs = Date.now() / 1000
  const currentWeatherCond = weatherCondition(activeWeather.weather_code, isDaytime(nowTs))
  const WeatherIcon = currentWeatherCond.icon

  // Find daily sun cycle (matching today's date in local tz)
  const todayDateStr = useMemo(() => new Date(nowTs * 1000).toLocaleDateString('en-CA', { timeZone: tz }), [nowTs, tz])
  const todayDaily = useMemo(() => {
    if (!dailyForecast.length) return null
    return dailyForecast.find(d => {
      const day = new Date((d.sunrise ?? d.valid) * 1000).toLocaleDateString('en-CA', { timeZone: tz })
      return day === todayDateStr
    }) ?? dailyForecast[0]
  }, [dailyForecast, todayDateStr, tz])

  // Sun / Night cycle calculation
  const sunInfo = useMemo(() => {
    if (!dailyForecast.length) return null
    const allSunrises = dailyForecast.map(d => d.sunrise).filter((s): s is number => s != null).sort((a, b) => a - b)
    const allSunsets = dailyForecast.map(d => d.sunset).filter((s): s is number => s != null).sort((a, b) => a - b)

    // Check if we are currently in daytime
    const currentDay = dailyForecast.find(d => d.sunrise && d.sunset && nowTs >= d.sunrise && nowTs < d.sunset)
    if (currentDay && currentDay.sunrise && currentDay.sunset) {
      const progress = Math.min(100, Math.max(0, ((nowTs - currentDay.sunrise) / (currentDay.sunset - currentDay.sunrise)) * 100))
      return {
        isDay: true as const,
        progress,
        sunrise: currentDay.sunrise,
        sunset: currentDay.sunset,
        remainingSec: currentDay.sunset - nowTs,
      }
    }

    // Nighttime: find most recent sunset and upcoming sunrise
    const lastSunset = allSunsets.filter(s => s <= nowTs).pop() ?? todayDaily?.sunset
    const nextSunrise = allSunrises.find(s => s > nowTs) ?? (todayDaily?.sunrise ? todayDaily.sunrise + 86400 : null)

    return {
      isDay: false as const,
      progress: null,
      lastSunset,
      nextSunrise,
      untilSunriseSec: nextSunrise ? Math.max(0, nextSunrise - nowTs) : null,
    }
  }, [dailyForecast, nowTs, todayDaily])

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Indigo home">
          <span className="brand-mark"><Waves size={24} /></span>
          <span>indigo<span className="brand-dot">.</span></span>
        </a>
        <div className="workspace"><span className="status-dot" /> PERSONAL OBSERVATORY</div>
        <nav>
          {[
            { name: 'Overview', icon: LayoutDashboard },
            { name: 'History', icon: History },
            { name: 'System', icon: Settings2 },
          ].map(({ name, icon: Icon }) => (
            <button key={name} className={tab === name ? 'nav active' : 'nav'} onClick={() => setTab(name)}>
              <Icon size={18} />
              {name}
              {tab === name && <ChevronRight size={15} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="orbit"><Wind size={27} /></div>
          <h3>Local monitoring</h3>
          <p>Air quality and weather observatory.</p>
        </div>
        <div className="private">
          <ShieldCheck size={16} />
          <div>Private by design<small>Hosted on your homelab</small></div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <span>YOUR ENVIRONMENT <ChevronRight size={12} /> {tab.toUpperCase()}</span>
          <div className="top-actions">
            <button className="unit-toggle" onClick={toggleUnits} title="Toggle between Imperial and Metric units">
              {units === 'imperial' ? '°F · mph' : '°C · km/h'}
            </button>
            <span className={'live ' + (latest?.stale || error || offline ? 'warning' : '')}>
              <i />
              {offline ? 'Offline' : error ? 'Connection lost' : !latest ? 'Loading' : latest.stale ? 'Awaiting sensor' : 'Collecting every minute'}
            </span>
            <button className="icon-button" onClick={() => setTick(t => t + 1)} aria-label="Refresh readings">
              <RefreshCw size={16} className={loading ? 'spinning' : ''} />
            </button>
          </div>
        </header>

        <section className="page-heading">
          <div>
            <span className="eyebrow">{tab === 'System' ? 'SYSTEM' : 'OBSERVATORY'}</span>
            <h1>{tab === 'Overview' ? 'Overview' : tab === 'History' ? 'History' : 'Settings'}</h1>
            <p>
              {tab === 'System'
                ? 'Manage database, background jobs, and application settings.'
                : tab === 'Overview'
                ? 'Current weather conditions, forecasts, and live sensor air quality.'
                : 'Historical telemetry analysis and reading explorer.'}
            </p>
          </div>
          <div className="heading-date"><CalendarDays size={16} />{date(nowTs)}</div>
        </section>

        {(error || offline) && (
          <div className="notice">{offline ? 'You’re offline. Reconnect to your tailnet for fresh readings.' : error}</div>
        )}
        {latest?.stale && !error && (
          <div className="notice">
            {reading ? `Sensor readings are stale. Last received ${time(reading.ts, true)}.` : 'Waiting for the first sensor reading. The collector will retry automatically.'}
          </div>
        )}

        {tab === 'Overview' && (
          <>
            {/* Dual-Focus Hero Grid: Weather LEFT, Air Quality RIGHT */}
            <section className="hero-grid">
              {/* Weather Hero Card (LEFT) */}
              <article className="hero-card">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">CONDITIONS</span>
                    <h2>Current Weather</h2>
                  </div>
                  <span className="pill">{reading?.temperature != null ? 'Live Sensor' : 'Regional Model'}</span>
                </div>
                <div className="hero-condition">
                  <WeatherIcon size={20} />
                  <span>{currentWeatherCond.label}</span>
                </div>
                <div className="hero-primary">
                  <div className="hero-temp">
                    {formatTemp(reading?.temperature ?? activeWeather.temperature)}
                    <small>{tempUnit}</small>
                  </div>
                </div>
                <div className="hero-feels">
                  {reading?.temperature != null && activeWeather.temperature != null ? (
                    <>
                      Feels like {formatTemp(activeWeather.apparent_temperature ?? reading.temperature)}{tempUnit} · Regional model {formatTemp(activeWeather.temperature)}{tempUnit}
                    </>
                  ) : (
                    <>Feels like {formatTemp(activeWeather.apparent_temperature ?? reading?.temperature)}{tempUnit}</>
                  )}
                </div>

                <div className="hero-details">
                  <span><Wind size={14} /> {formatWind(activeWeather.wind_speed)} {windUnit}</span>
                  <span><Droplets size={14} /> {fmt(reading?.humidity ?? activeWeather.humidity, 0)}% RH</span>
                  <span><Cloud size={14} /> {activeWeather.cloud_cover != null ? fmt(activeWeather.cloud_cover, 0) + '%' : '—'}</span>
                  <span><Sun size={14} /> UV {fmt(activeWeather.uv_index, 0)} ({uvLabel(activeWeather.uv_index)})</span>
                </div>

                {sunInfo ? (
                  <div className="sun-bar-wrap">
                    {sunInfo.isDay ? (
                      <>
                        <div className="sun-header">
                          <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                            <Sun size={12} style={{ color: '#f3cb7c' }} /> Daylight
                          </span>
                          <span>Sunset in {formatRemaining(sunInfo.remainingSec)}</span>
                        </div>
                        <div className="sun-bar">
                          <div className="sun-bar-progress" style={{ width: `${sunInfo.progress}%` }} />
                          <div className="sun-bar-marker" style={{ left: `${sunInfo.progress}%` }} />
                        </div>
                        <div className="sun-times">
                          <span>↑ {time(sunInfo.sunrise)} Sunrise</span>
                          <span>↓ {time(sunInfo.sunset)} Sunset</span>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="sun-header">
                          <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                            <Moon size={12} style={{ color: '#8db8f9' }} /> Night
                          </span>
                          {sunInfo.untilSunriseSec != null && <span>Sunrise in {formatRemaining(sunInfo.untilSunriseSec)}</span>}
                        </div>
                        <div className="sun-times" style={{ marginTop: '6px' }}>
                          <span>{sunInfo.lastSunset ? `Sunset was ${time(sunInfo.lastSunset)}` : 'Sun has set'}</span>
                          <span>{sunInfo.nextSunrise ? `↑ ${time(sunInfo.nextSunrise)} Sunrise` : ''}</span>
                        </div>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="hero-sub">Daily sunrise and sunset will appear once forecasts are refreshed.</div>
                )}
              </article>

              {/* Air Quality Hero Card (RIGHT) */}
              <article className="hero-card">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">AIR QUALITY</span>
                    <h2>Local Sensor</h2>
                  </div>
                  <span className={`aqi-pill ${aqiClass(liveAqi ?? currentAqi)}`}>{category(liveAqi ?? currentAqi)}</span>
                </div>
                <div className="hero-condition">
                  <Wind size={20} />
                  <span>Fine Particulate Matter (PM2.5)</span>
                </div>

                <div className="hero-primary">
                  <div className="hero-temp" style={{ color: aqiDotColor(liveAqi ?? currentAqi) }}>
                    {fmt(reading?.pm25)}
                    <small style={{ color: '#92a2b7' }}>µg/m³</small>
                  </div>
                </div>
                <div className="hero-feels">
                  US AQI: <strong style={{ color: '#e8edf5' }}>{fmt(liveAqi, 0)}</strong> (Live)
                  {latest?.nowcast_aqi != null && (
                    <> · <strong>{fmt(latest.nowcast_aqi, 0)}</strong> (NowCast 12h)</>
                  )}
                  {' '}· {reading?.method === 'epa2021' ? 'EPA 2021 correction' : 'Raw CF=1'}
                </div>

                {/* AQI Continuous Scale Bar */}
                <div className="aqi-scale-bar">
                  <div
                    className="aqi-scale-marker"
                    style={{ left: `${Math.min(100, Math.max(0, (((liveAqi ?? currentAqi) ?? 0) / 300) * 100))}%` }}
                  />
                </div>
                <div className="aqi-scale-labels">
                  <span>0 Good</span>
                  <span>50 Mod</span>
                  <span>100 USG</span>
                  <span>150 Unhealthy</span>
                  <span>300+</span>
                </div>

                <div className="hero-details">
                  <span>Channel A: {fmt(reading?.pm_a)} µg/m³</span>
                  <span>Channel B: {fmt(reading?.pm_b)} µg/m³</span>
                  <span>Flag: {reading?.quality ? reading.quality.replaceAll('_', ' ') : 'Clean'}</span>
                </div>

                <div className="hero-sub">
                  {currentForecast.pm25 != null ? (
                    <span>
                      Regional model {fmt(currentForecast.pm25)} µg/m³ ({fmt(currentForecast.aqi, 0)} AQI)
                      {reading?.pm25 != null && (
                        <span> · Δ {reading.pm25 - currentForecast.pm25 >= 0 ? '+' : ''}{fmt(reading.pm25 - currentForecast.pm25)}</span>
                      )}
                    </span>
                  ) : (
                    <span>Collecting from local sensor every minute.</span>
                  )}
                </div>
              </article>
            </section>

            {/* Middle: Horizontal Hourly Forecast Strip with AQI dots */}
            <section className="hourly-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">FORECAST</span>
                  <h2>Hourly Outlook</h2>
                </div>
                <span className="pill">Next 36 Hours · ● AQI</span>
              </div>
              <div className="hourly-strip">
                {hourlyStripData.map(h => {
                  const isCurrent = Math.abs(h.valid - nowTs) < 1800
                  const cond = weatherCondition(h.weather_code, isDaytime(h.valid))
                  const Icon = cond.icon
                  const hourAqi = h.aqi ?? aq(h.pm25)
                  return (
                    <div className="hourly-item" key={h.valid}>
                      <span className="hourly-time">{isCurrent ? 'Now' : time(h.valid)}</span>
                      <Icon size={20} className="hourly-icon" />
                      <span className="hourly-temp">{formatTemp(h.temperature, 0)}°</span>
                      <span className="hourly-pop">
                        {h.precipitation_probability != null && h.precipitation_probability > 0
                          ? `${Math.round(h.precipitation_probability)}%`
                          : ''}
                      </span>
                      <div
                        className="hourly-aqi-badge"
                        title={hourAqi != null ? `Forecast AQI: ${hourAqi} (${category(hourAqi)})` : 'No AQI model'}
                      >
                        <span
                          className="hourly-aqi-dot"
                          style={{ background: aqiDotColor(hourAqi) }}
                        />
                        <span className="hourly-aqi-num">{hourAqi ?? '—'}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* Bottom 3-Column Grid: 10-Day Forecast, 24h PM2.5 Trend, Weather Details */}
            <section className="overview-bottom-grid">
              {/* Column 1: 10-Day Outlook */}
              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">10-DAY OUTLOOK</span>
                    <h2>Daily Forecast</h2>
                  </div>
                </div>
                <div className="daily-list">
                  {dailyForecast.length > 0 ? (
                    dailyForecast.map(day => {
                      const cond = weatherCondition(day.weather_code, true)
                      const Icon = cond.icon
                      const span = Math.max(1, dailyMaxAll - dailyMinAll)
                      const leftPct = Math.max(0, Math.min(100, (((day.temp_min ?? dailyMinAll) - dailyMinAll) / span) * 100))
                      const widthPct = Math.max(
                        8,
                        Math.min(100 - leftPct, ((((day.temperature ?? dailyMaxAll) - (day.temp_min ?? dailyMinAll))) / span) * 100)
                      )
                      return (
                        <div className="daily-row" key={day.valid}>
                          <span className="daily-day">{dayName(day.valid)}</span>
                          <span className="daily-icon" title={cond.label}><Icon size={18} /></span>
                          <span className="daily-min">{formatTemp(day.temp_min, 0)}°</span>
                          <div className="daily-bar-track">
                            <div className="daily-bar-range" style={{ left: `${leftPct}%`, width: `${widthPct}%` }} />
                          </div>
                          <span className="daily-max">{formatTemp(day.temperature, 0)}°</span>
                        </div>
                      )
                    })
                  ) : (
                    <p className="muted">Daily forecasts will populate after coordinates are saved.</p>
                  )}
                </div>
              </article>

              {/* Column 2: 24-Hour PM2.5 Trend */}
              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">TREND</span>
                    <h2>24-Hour PM2.5</h2>
                  </div>
                  <button
                    className="icon-button"
                    onClick={() => { setTab('History'); setMetric('pm25'); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
                    title="Open History tab"
                  >
                    <ArrowUpRight size={15} />
                  </button>
                </div>
                <div className="trend-summary">
                  <span className="trend-stat">Current: <strong>{fmt(reading?.pm25)}</strong></span>
                  <span className="trend-stat">Avg: <strong>{fmt(stats.pm_mean)}</strong></span>
                  <span className="trend-stat">Peak: <strong>{fmt(stats.pm_max)}</strong></span>
                </div>
                <div className="trend-chart">
                  {recentTrend.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={recentTrend} margin={{ top: 8, right: 4, bottom: 0, left: -25 }}>
                        <defs>
                          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#70d8c2" stopOpacity={0.3} />
                            <stop offset="100%" stopColor="#70d8c2" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid stroke="#26313e" strokeDasharray="3 5" vertical={false} />
                        <XAxis dataKey="ts" tickFormatter={t => time(t)} stroke="#7d8a9a" tickLine={false} axisLine={false} minTickGap={50} />
                        <YAxis stroke="#7d8a9a" tickLine={false} axisLine={false} domain={[0, 'auto']} />
                        <Tooltip contentStyle={{ background: '#1a2533', border: '1px solid #3c4c60', borderRadius: 8 }} labelFormatter={v => time(Number(v), true)} formatter={v => [fmt(Number(v)), 'µg/m³']} />
                        <Area type="monotone" dataKey="pm25" name="PM2.5" stroke="#70d8c2" fill="url(#trendFill)" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="empty-small">Waiting for recent readings to plot trend.</div>
                  )}
                </div>
              </article>

              {/* Column 3: Weather Details */}
              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">METRICS</span>
                    <h2>Weather Details</h2>
                  </div>
                </div>
                <div className="detail-stack">
                  <div className="detail-item">
                    <div className="detail-item-left">
                      <span className="detail-item-label">UV Index</span>
                      <span className="detail-item-sub">Peak today: {fmt(maxUv, 1)}</span>
                    </div>
                    <span className="detail-item-value">{fmt(activeWeather.uv_index, 0)} <small>({uvLabel(activeWeather.uv_index)})</small></span>
                  </div>

                  <div className="detail-item">
                    <div className="detail-item-left">
                      <span className="detail-item-label">Precipitation</span>
                      <span className="detail-item-sub">Max probability: {fmt(maxPrecip, 0)}%</span>
                    </div>
                    <span className="detail-item-value">{fmt(activeWeather.precipitation_probability, 0)}<small>%</small></span>
                  </div>

                  <div className="detail-item">
                    <div className="detail-item-left">
                      <span className="detail-item-label">Wind</span>
                      <span className="detail-item-sub">10m elevation</span>
                    </div>
                    <span className="detail-item-value">{formatWind(activeWeather.wind_speed)} <small>{windUnit}</small></span>
                  </div>

                  <div className="detail-item">
                    <div className="detail-item-left">
                      <span className="detail-item-label">Humidex</span>
                      <span className="detail-item-sub">Derived heat index</span>
                    </div>
                    <span className="detail-item-value">
                      {formatTemp(humidex(reading?.temperature ?? activeWeather.temperature, reading?.humidity ?? activeWeather.humidity))}
                      <small>{tempUnit}</small>
                    </span>
                  </div>

                  <div className="detail-item">
                    <div className="detail-item-left">
                      <span className="detail-item-label">Sun Cycle</span>
                      <span className="detail-item-sub">Daylight duration</span>
                    </div>
                    <span className="detail-item-value" style={{ fontSize: '13px' }}>
                      {todayDaily?.sunrise && todayDaily?.sunset
                        ? `${Math.round(((todayDaily.sunset - todayDaily.sunrise) / 3600) * 10) / 10} hrs`
                        : '—'}
                    </span>
                  </div>
                </div>
              </article>
            </section>
          </>
        )}

        {tab === 'History' && (
          <>
            {/* Metric-selector cards preserved on History tab */}
            <section className="metric-grid">
              {(Object.keys(metrics) as Metric[]).map(key => {
                const m = metrics[key]
                const Icon = m.icon
                const rawVal = key === 'aqi' ? currentAqi : key === 'humidex' ? humidex(reading?.temperature, reading?.humidity) : (reading as any)?.[key]
                const displayVal = key === 'temperature' || key === 'humidex' ? formatTemp(rawVal) : fmt(rawVal, key === 'aqi' ? 0 : 1)
                const predicted = currentForecast[key]
                const displayPred = key === 'temperature' || key === 'humidex' ? formatTemp(predicted) : fmt(predicted, key === 'aqi' ? 0 : 1)
                return (
                  <button
                    key={key}
                    className={'metric-card ' + (metric === key ? 'selected' : '')}
                    onClick={() => setMetric(key)}
                    style={{ '--accent': m.color } as React.CSSProperties}
                  >
                    <div className="metric-top">
                      <span>{m.title}</span>
                      <Icon size={19} />
                    </div>
                    <div className="metric-value">
                      {displayVal}
                      {key === 'aqi' && (latest?.nowcast_beyond_scale || reading?.beyond_scale) ? '+' : ''}
                      <small>{m.unit}</small>
                    </div>
                    <div className="metric-foot">
                      {key === 'pm25' ? (
                        <>
                          <span className="tiny-dot" /> {reading?.method === 'epa2021' ? 'EPA 2021 correction' : 'Raw CF=1 average'}
                        </>
                      ) : key === 'aqi' ? (
                        <>{category(currentAqi)} · {latest?.nowcast_aqi != null ? 'NowCast' : 'interval estimate'}</>
                      ) : key === 'temperature' ? (
                        `${reading?.environment_mode === 'raw' ? 'Operating sensor' : 'Estimated ambient'} · raw ${formatTemp(reading?.temperature_raw)}${tempUnit}`
                      ) : (
                        `Derived from ${formatTemp(reading?.temperature)}${tempUnit} & ${fmt(reading?.humidity)}%`
                      )}
                    </div>
                    {predicted != null && (
                      <div className="forecast-delta">
                        Regional model {displayPred} {m.unit}
                      </div>
                    )}
                  </button>
                )
              })}
            </section>

            {/* Main Telemetry Time-Series Chart */}
            <section className="panel history-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">HISTORY</span>
                  <h2>
                    {meta.title} over time <span className="unit-label">{meta.unit}</span>
                  </h2>
                </div>
                <div className="range-group">
                  {ranges.map(r => (
                    <button
                      className={!customBounds && range === r.seconds ? 'chosen' : ''}
                      key={r.seconds}
                      onClick={() => selectRange(r.seconds)}
                    >
                      {r.label}
                    </button>
                  ))}
                  <button
                    className={custom ? 'chosen' : ''}
                    onClick={() => setCustom(!custom)}
                    aria-label="Choose custom dates"
                  >
                    <CalendarDays size={15} />
                  </button>
                </div>
              </div>

              {custom && (
                <div className="custom-dates">
                  <label>
                    From <input type="datetime-local" value={startDate} onChange={e => setStartDate(e.target.value)} />
                  </label>
                  <label>
                    To <input type="datetime-local" value={endDate} onChange={e => setEndDate(e.target.value)} />
                  </label>
                  <button className="secondary" onClick={applyCustom}>
                    Apply dates
                  </button>
                  <small>Entered in this device’s timezone.</small>
                </div>
              )}

              <div className="chart-navigation">
                <button
                  aria-label="Previous time window"
                  onClick={() => setCustomBounds([Math.max(0, start - (end - start)), start])}
                >
                  <ChevronLeft size={15} />
                </button>
                <span>{date(start)} – {date(end)}</span>
                <button
                  aria-label="Next time window"
                  disabled={end >= Date.now() / 1000}
                  onClick={() => setCustomBounds([end, end + (end - start)])}
                >
                  <ChevronRight size={15} />
                </button>
                <button
                  aria-label="Zoom in"
                  disabled={end - start <= 3600}
                  onClick={() => setCustomBounds([Math.floor(start + (end - start) / 4), Math.floor(end - (end - start) / 4)])}
                >
                  <ZoomIn size={15} />
                </button>
                <button
                  aria-label="Zoom out"
                  disabled={end - start >= 365 * 86400}
                  onClick={() => setCustomBounds([Math.max(0, Math.floor(start - (end - start) / 2)), Math.floor(end + (end - start) / 2)])}
                >
                  <ZoomOut size={15} />
                </button>
                <button onClick={() => selectRange(86400)}>Live</button>
              </div>

              <div className="chart-toolbar">
                <div className="legend">
                  <span>
                    <i style={{ background: meta.color }} />
                    Your sensor
                  </span>
                  {history.forecasts.length > 0 && (
                    <label>
                      <input
                        type="checkbox"
                        checked={showForecast}
                        onChange={e => setShowForecast(e.target.checked)}
                      />
                      Regional forecast
                    </label>
                  )}
                  <label>
                    <input
                      type="checkbox"
                      checked={comparison}
                      onChange={e => setComparison(e.target.checked)}
                    />
                    Previous period
                  </label>
                </div>
                <span className="muted small">
                  {history.step < 3600 ? `${history.step / 60} min` : `${history.step / 3600} hour`} intervals ·{' '}
                  {tz.split('/').pop()?.replaceAll('_', ' ')}
                </span>
              </div>

              <div className="main-chart">
                {hasData ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={chart} margin={{ top: 14, right: 8, bottom: 0, left: -22 }}>
                      <defs>
                        <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={meta.color} stopOpacity={0.2} />
                          <stop offset="100%" stopColor={meta.color} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="#26313e" strokeDasharray="3 5" vertical={false} />
                      <XAxis
                        dataKey="ts"
                        type="number"
                        domain={[start, end]}
                        tickFormatter={t =>
                          end - start > 172800
                            ? new Date(t * 1000).toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric' })
                            : time(t)
                        }
                        stroke="#7d8a9a"
                        tickLine={false}
                        axisLine={false}
                        minTickGap={60}
                      />
                      <YAxis
                        stroke="#7d8a9a"
                        tickLine={false}
                        axisLine={false}
                        domain={metric === 'temperature' || metric === 'humidex' ? ['auto', 'auto'] : [0, 'auto']}
                      />
                      <Tooltip
                        contentStyle={{ background: '#1a2533', border: '1px solid #3c4c60', borderRadius: 12 }}
                        labelFormatter={v => time(Number(v), true)}
                        formatter={v => [fmt(Number(v)), meta.unit]}
                      />
                      <Area
                        type="linear"
                        dataKey="sensor"
                        name="Your sensor"
                        stroke={meta.color}
                        fill="url(#chartFill)"
                        strokeWidth={2}
                        dot={{ r: 1.8, strokeWidth: 0 }}
                        activeDot={{ r: 4 }}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                      <Line
                        type="linear"
                        dataKey="forecast"
                        name="Regional forecast"
                        stroke="#99a7bd"
                        strokeDasharray="5 5"
                        strokeWidth={1.5}
                        dot={false}
                        connectNulls
                        isAnimationActive={false}
                      />
                      <Line
                        dataKey="previous"
                        name="Previous period"
                        stroke="#8876bd"
                        strokeWidth={1.3}
                        dot={false}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="empty-chart">
                    <Waves size={42} />
                    <h3>{loading ? 'Loading...' : 'No data available.'}</h3>
                    <p>Readings will appear as your sensor reports. Choose another date range to explore earlier data.</p>
                  </div>
                )}
              </div>

              <div className="chart-bottom">
                <span><Clock3 size={14} />{stats.samples.toLocaleString()} readings in this window</span>
                <button onClick={() => { document.getElementById('explore-readings')?.scrollIntoView({ behavior: 'smooth' }) }}>
                  Inspect readings <ArrowDownToLine size={14} />
                </button>
              </div>
            </section>

            {/* Diurnal Hourly Average & Exploration */}
            <section className="lower-grid">
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">HOURLY AVERAGE</span>
                    <h2>Average by hour of day</h2>
                  </div>
                  <span className="pill">{meta.unit}</span>
                </div>
                <div className="mini-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={hourly} margin={{ left: -25 }}>
                      <XAxis dataKey="hour" interval={3} stroke="#7d8a9a" axisLine={false} tickLine={false} />
                      <YAxis stroke="#7d8a9a" axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={{ background: '#1a2533', border: '1px solid #3c4c60' }} formatter={v => [fmt(Number(v)), meta.unit]} />
                      <Bar dataKey="value" fill={meta.color} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <p className="muted small">Mean of displayed intervals, grouped by local hour.</p>
              </section>

              <article className="panel summary-panel">
                <span className="eyebrow">SUMMARY</span>
                <h2>Statistics</h2>
                <div className="stat-row"><span>Average PM2.5</span><strong>{fmt(stats.pm_mean)} <small>µg/m³</small></strong></div>
                <div className="stat-row"><span>Peak PM2.5</span><strong>{fmt(stats.pm_max)} <small>µg/m³</small></strong></div>
                <div className="stat-row">
                  <span>Temperature range</span>
                  <strong>{formatTemp(stats.temp_min)}–{formatTemp(stats.temp_max)} <small>{tempUnit}</small></strong>
                </div>
                <div className="stat-row"><span>Collection coverage</span><strong>{fmt(coverage, 1)}<small>%</small></strong></div>
                <div className="coverage"><i style={{ width: coverage + '%' }} /></div>
                <p className="muted small">Coverage includes time before collection began. Missing samples stay visible as gaps.</p>
              </article>
            </section>

            <section className="panel" id="explore-readings">
              <div className="panel-heading">
                <h2>Explore readings</h2>
                <a className="secondary" href={`/api/export?start=${start}&end=${end}`}>
                  <ArrowDownToLine size={15} /> Export all minutes
                </a>
              </div>
              <div className="filter">
                <label>
                  Highlight PM2.5 at or above{' '}
                  <input
                    type="number"
                    min="0"
                    placeholder="e.g. 35.5"
                    value={threshold}
                    onChange={e => setThreshold(e.target.value)}
                  />{' '}
                  µg/m³
                </label>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>PM2.5</th>
                      <th>AQI estimate</th>
                      <th>Temperature</th>
                      <th>Humidity</th>
                      <th>Samples</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...history.points]
                      .reverse()
                      .filter(p => threshold === '' || p.above_threshold)
                      .slice(0, 100)
                      .map(p => (
                        <tr key={p.ts} className={p.above_threshold ? 'highlight' : ''}>
                          <td>{time(p.ts, true)}{p.flagged > 0 && <span title="One or more samples has a quality flag"> *</span>}</td>
                          <td>{fmt(p.pm25)}</td>
                          <td>{fmt(p.aqi, 0)}</td>
                          <td>{formatTemp(p.temperature)}{tempUnit}</td>
                          <td>{fmt(p.humidity)}%</td>
                          <td>{p.samples}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">Showing up to 100 recent intervals matching the filter. CSV includes every minute in the selected range. * Channel quality flag.</p>
            </section>
          </>
        )}

        {tab === 'System' && (
          <>
            <section className="system-grid">
              <article className="panel">
                <h2><Database size={19} /> Persistent storage</h2>
                <div className="system-value">{fmt((status?.database_bytes ?? 0) / 1048576)} <small>MB</small></div>
                <p>{fmt(status?.readings.n, 0)} minute records · SQLite WAL</p>
                <dl>
                  <dt>First reading</dt>
                  <dd>{status?.readings.first ? time(status.readings.first, true) : 'Not yet'}</dd>
                  <dt>Last reading</dt>
                  <dd>{relative(status?.readings.last)}</dd>
                  <dt>Application</dt>
                  <dd>v{status?.version ?? '0.5.2'}</dd>
                </dl>
              </article>
              <article className="panel">
                <h2><ShieldCheck size={19} /> Backups & access</h2>
                <p className="body-copy">Daily consistent local snapshots are retained independently of off-server transfer health.</p>
                <div className="notice">{status?.backup_scope ?? 'Loading backup status…'}</div>
                <p className="muted">Private access is managed by your tailnet. Address and coordinates are never included in the dashboard API.</p>
              </article>
            </section>
            <section className="panel">
              <div className="panel-heading"><h2>Backup destination</h2><span className="pill">Verified SHA-256 copies</span></div>
              <BackupSettings />
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>Collection health</h2>
                <span className="pill">Automatic retries</span>
              </div>
              <div className="job-list">
                {status?.jobs.map(j => (
                  <div className="job" key={j.name}>
                    <span className={'job-icon ' + (j.error ? 'bad' : '')}><Activity size={18} /></span>
                    <div>
                      <strong>
                        {j.name === 'sensor'
                          ? 'PurpleAir collector'
                          : j.name === 'weather'
                          ? 'Weather & air forecasts'
                          : j.name === 'backup'
                          ? 'Daily local backup'
                          : j.name === 'offsite_backup'
                          ? 'Off-server backup'
                          : 'Database maintenance'}
                      </strong>
                      <p>{j.error ?? 'Running normally'}</p>
                    </div>
                    <span>{relative(j.last_success)}</span>
                  </div>
                ))}
              </div>
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>System Settings</h2>
              </div>
              <SystemSettings />
            </section>
          </>
        )}

        <div className="environment-choice">
          <label>
            Temperature &amp; humidity view{' '}
            <select
              value={environmentMode || reading?.environment_mode || 'purpleair'}
              onChange={e => setEnvironmentMode(e.target.value)}
            >
              <option value="purpleair">PurpleAir estimated ambient</option>
              <option value="raw">Raw operating readings</option>
              <option value="simple">Simple correction</option>
            </select>
          </label>
        </div>

        <section className="method-note">
          <CircleHelp size={17} />
          <p>
            PM2.5 {reading?.method === 'epa2021' ? 'uses the EPA 2021 correction (not the extended wildfire formula).' : 'shows the raw CF=1 channel average.'}{' '}
            US AQI uses EPA 2024 breakpoints. NowCast appears after at least two of the latest three completed hours have 45 valid samples each; otherwise the current AQI is an interval estimate.
            Temperature and humidity use the selected view; raw operating readings remain stored.
            Estimated ambient values use PurpleAir’s Wallace formulas.
            {reading?.quality && ` Latest quality flag: ${reading.quality.replaceAll('_', ' ')}.`}
          </p>
        </section>

        <footer>
          <span><Waves size={15} /> INDIGO STATS</span>
          <span><span className="footer-dot">·</span> {reading ? `Updated ${relative(reading.ts)}` : 'Awaiting first reading'}</span>
          <span><Check size={13} /> Stored on your homelab</span>
        </footer>
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
