import React, { useMemo } from 'react'
import {
  Moon,
  Sparkles,
  Compass,
  Telescope,
  Clock,
  Eye,
  EyeOff,
  ChevronRight,
  Orbit,
  Star,
} from 'lucide-react'

export interface MoonData {
  timestamp: number
  altitude_deg: number
  azimuth_deg: number
  azimuth_cardinal: string
  is_visible: boolean
  illumination_pct: number
  phase_name: string
  phase_code: string
  is_waxing: boolean
  age_days: number
  distance_km: number
  is_supermoon: boolean
  constellation: string
  ra_deg: number
  dec_deg: number
  moonrise: number | null
  moonset: number | null
  transit_time: number | null
  transit_altitude_deg: number | null
}

export interface TwilightData {
  civil_dusk: number | null
  nautical_dusk: number | null
  astronomical_dusk: number | null
  astronomical_dawn: number | null
  darkness_hours: number
}

export interface PlanetData {
  name: string
  magnitude: number
  altitude_deg: number
  azimuth_deg: number
  azimuth_cardinal: string
  constellation: string
  elongation_deg: number
  visible_tonight: boolean
  is_above_horizon: boolean
  viewing_window: string
}

export interface CelestialEvent {
  type: 'meteor_shower' | 'full_moon' | 'new_moon'
  name: string
  peak_date?: string
  date?: string
  peak_timestamp?: number
  timestamp?: number
  days_until: number
  is_active?: boolean
  zhr?: number
  dark_sky_rating?: 'Excellent' | 'Good' | 'Fair' | 'Poor'
  is_supermoon?: boolean
  summary: string
}

export interface StargazingData {
  score: number
  rating: 'Excellent' | 'Good' | 'Fair' | 'Poor'
  moon_interference: string
  cloud_cover_pct: number | null
  dark_hours: number
}

export interface AstronomyData {
  available: boolean
  reason?: string
  server_time?: number
  moon?: MoonData
  twilight?: TwilightData
  planets?: PlanetData[]
  events?: CelestialEvent[]
  stargazing?: StargazingData
}

interface AstronomyPanelProps {
  data: AstronomyData | null
  loading?: boolean
  timezone?: string
  units?: 'imperial' | 'metric'
  onConfigureClick?: () => void
}

/**
 * Realistic mathematical SVG lunar phase disc.
 * Accurately models the illuminated portion and terminator curvature based on illumination % and waxing/waning limb.
 */
function RealisticMoonSvg({
  illumination,
  isWaxing,
  size = 130,
}: {
  illumination: number
  isWaxing: boolean
  size?: number
}) {
  const k = Math.min(1, Math.max(0, illumination / 100))
  const R = 50
  const cx = 60
  const cy = 60

  // Calculate terminator path
  // North pole (cx, cy - R) -> South pole (cx, cy + R)
  // For illumination k:
  // rx = R * |2k - 1|
  const rx = Math.max(0.5, R * Math.abs(2 * k - 1))

  const litPath = useMemo(() => {
    if (k <= 0.01) return ''
    if (k >= 0.99) {
      return `M ${cx} ${cy - R} A ${R} ${R} 0 1 1 ${cx} ${cy + R} A ${R} ${R} 0 1 1 ${cx} ${cy - R} Z`
    }

    if (isWaxing) {
      // Lit side is the right half (from N to S along right rim: sweep=1)
      const rightRim = `M ${cx} ${cy - R} A ${R} ${R} 0 0 1 ${cx} ${cy + R}`
      if (k < 0.5) {
        // Crescent: terminator curves into the right side
        return `${rightRim} A ${rx} ${R} 0 0 1 ${cx} ${cy - R} Z`
      } else {
        // Gibbous: terminator bulges into the left side
        return `${rightRim} A ${rx} ${R} 0 0 0 ${cx} ${cy - R} Z`
      }
    } else {
      // Waning: lit side is the left half (from N to S along left rim: sweep=0)
      const leftRim = `M ${cx} ${cy - R} A ${R} ${R} 0 0 0 ${cx} ${cy + R}`
      if (k < 0.5) {
        // Crescent: terminator curves into the left side
        return `${leftRim} A ${rx} ${R} 0 0 0 ${cx} ${cy - R} Z`
      } else {
        // Gibbous: terminator bulges into the right side
        return `${leftRim} A ${rx} ${R} 0 0 1 ${cx} ${cy - R} Z`
      }
    }
  }, [k, isWaxing, rx, cx, cy, R])

  return (
    <div className="moon-visual-wrap" style={{ width: size, height: size }}>
      <svg
        viewBox="0 0 120 120"
        className="moon-svg"
        style={{
          filter:
            k > 0.4
              ? `drop-shadow(0 0 ${Math.round(k * 18)}px rgba(243, 203, 124, ${0.15 + k * 0.25}))`
              : 'drop-shadow(0 0 8px rgba(141, 184, 249, 0.15))',
        }}
      >
        <defs>
          {/* Unlit dark surface background */}
          <radialGradient id="darkLimbGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#1e2736" />
            <stop offset="85%" stopColor="#141a24" />
            <stop offset="100%" stopColor="#0d1219" />
          </radialGradient>

          {/* Lit lunar surface texture gradient */}
          <radialGradient id="litSurfaceGrad" cx="35%" cy="35%" r="65%">
            <stop offset="0%" stopColor="#fffaf0" />
            <stop offset="60%" stopColor="#f4ebd9" />
            <stop offset="85%" stopColor="#ded1be" />
            <stop offset="100%" stopColor="#baa992" />
          </radialGradient>

          {/* Clip path for the whole circular moon disc */}
          <clipPath id="discClip">
            <circle cx={cx} cy={cy} r={R} />
          </clipPath>

          {/* Clip path for the illuminated region */}
          <clipPath id="litClip">
            {litPath ? <path d={litPath} /> : null}
          </clipPath>
        </defs>

        {/* Base dark lunar sphere */}
        <circle cx={cx} cy={cy} r={R} fill="url(#darkLimbGrad)" />

        {/* Craters / Maria on unlit dark side */}
        <g clipPath="discClip" opacity="0.35">
          <circle cx="45" cy="45" r="14" fill="#0f141d" />
          <circle cx="70" cy="40" r="11" fill="#0f141d" />
          <circle cx="58" cy="72" r="17" fill="#0f141d" />
          <circle cx="82" cy="65" r="9" fill="#0f141d" />
          <circle cx="38" cy="75" r="7" fill="#0f141d" />
        </g>

        {/* Illuminated portion */}
        {litPath && (
          <path d={litPath} fill="url(#litSurfaceGrad)" />
        )}

        {/* Craters / Maria texture inside illuminated area */}
        {litPath && (
          <g clipPath="url(#litClip)" opacity="0.28">
            <circle cx="45" cy="45" r="14" fill="#938470" />
            <circle cx="70" cy="40" r="11" fill="#938470" />
            <circle cx="58" cy="72" r="17" fill="#887864" />
            <circle cx="82" cy="65" r="9" fill="#938470" />
            <circle cx="38" cy="75" r="7" fill="#887864" />
            {/* Tycho crater rays */}
            <circle cx="62" cy="94" r="3.5" fill="#ffffff" opacity="0.8" />
            <line x1="62" y1="94" x2="45" y2="70" stroke="#ffffff" strokeWidth="0.75" opacity="0.4" />
            <line x1="62" y1="94" x2="80" y2="75" stroke="#ffffff" strokeWidth="0.75" opacity="0.4" />
            <line x1="62" y1="94" x2="62" y2="60" stroke="#ffffff" strokeWidth="0.75" opacity="0.4" />
          </g>
        )}

        {/* Subtle limb glow ring */}
        <circle
          cx={cx}
          cy={cy}
          r={R}
          fill="none"
          stroke={k > 0.5 ? '#f3cb7c' : '#70d8c2'}
          strokeWidth="0.75"
          opacity="0.35"
        />
      </svg>
    </div>
  )
}

/**
 * Celestial Sky Dome Arc: Horizon-to-zenith trajectory arc showing the Moon's
 * real-time topocentric position across the local sky from East to West.
 */
function CelestialArc({
  moon,
}: {
  moon: MoonData
}) {
  const isAbove = moon.altitude_deg > 0
  const alt = Math.max(0, Math.min(90, moon.altitude_deg))
  const az = moon.azimuth_deg

  // Horizontal trajectory parameter u in [0, 1]
  // East is ~90°, South is 180°, West is ~270°
  // Map azimuth to East (u=0.08) -> South/Transit (u=0.5) -> West (u=0.92)
  const u = useMemo(() => {
    if (az >= 45 && az <= 315) {
      return Math.max(0.05, Math.min(0.95, (az - 45) / 270))
    }
    // Around North
    return az > 315 ? 0.95 : 0.05
  }, [az])

  // Dimensions of SVG arc
  const w = 260
  const h = 130
  const leftX = 25
  const rightX = 235
  const horizonY = 105

  // Arc path: parabolic dome from (leftX, horizonY) peaking at (130, 20) to (rightX, horizonY)
  const markerX = leftX + u * (rightX - leftX)
  // Height on arc based on altitude
  const peakY = 20
  const arcY = horizonY - (alt / 90) * (horizonY - peakY)
  const markerY = isAbove ? Math.max(peakY, Math.min(horizonY, arcY)) : horizonY + 8

  return (
    <div className="celestial-arc-wrap">
      <div className="celestial-arc-header">
        <span>SKY DOME TRAJECTORY</span>
        <span className="celestial-transit-sub">
          {moon.transit_altitude_deg != null && `Peak: ${moon.transit_altitude_deg}°`}
        </span>
      </div>

      <svg viewBox={`0 0 ${w} ${h}`} className="celestial-arc-svg">
        <defs>
          <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1e2c40" stopOpacity="0.45" />
            <stop offset="100%" stopColor="#141c28" stopOpacity="0.0" />
          </linearGradient>
          <linearGradient id="arcGlow" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#70d8c2" stopOpacity="0.3" />
            <stop offset="50%" stopColor="#f3cb7c" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#8db8f9" stopOpacity="0.3" />
          </linearGradient>
        </defs>

        {/* Sky dome background shade */}
        <path
          d={`M ${leftX} ${horizonY} Q 130 ${peakY} ${rightX} ${horizonY} Z`}
          fill="url(#skyGrad)"
        />

        {/* Reference altitude dashed arcs (30°, 60°) */}
        <path
          d={`M ${leftX + 25} ${horizonY - 26} Q 130 ${peakY + 54} ${rightX - 25} ${horizonY - 26}`}
          fill="none"
          stroke="#26364a"
          strokeDasharray="2 4"
          strokeWidth="1"
        />
        <path
          d={`M ${leftX + 48} ${horizonY - 54} Q 130 ${peakY + 28} ${rightX - 48} ${horizonY - 54}`}
          fill="none"
          stroke="#26364a"
          strokeDasharray="2 4"
          strokeWidth="1"
        />

        {/* Trajectory Arc */}
        <path
          d={`M ${leftX} ${horizonY} Q 130 ${peakY} ${rightX} ${horizonY}`}
          fill="none"
          stroke="url(#arcGlow)"
          strokeWidth="2.5"
          strokeLinecap="round"
        />

        {/* Base Horizon Line */}
        <line
          x1={leftX - 10}
          y1={horizonY}
          x2={rightX + 10}
          y2={horizonY}
          stroke="#38495f"
          strokeWidth="1.5"
        />

        {/* Cardinal labels */}
        <text x={leftX - 8} y={horizonY + 16} fill="#7e93aa" fontSize="11" fontWeight="600">
          E
        </text>
        <text x="130" y={horizonY + 16} fill="#5c7188" fontSize="10" textAnchor="middle">
          S (Transit)
        </text>
        <text x={rightX + 8} y={horizonY + 16} fill="#7e93aa" fontSize="11" fontWeight="600" textAnchor="end">
          W
        </text>

        {/* Moon Position Marker */}
        {isAbove ? (
          <g>
            {/* Outer pulse glow */}
            <circle
              cx={markerX}
              cy={markerY}
              r="10"
              fill="#f3cb7c"
              opacity="0.25"
              className="moon-marker-pulse"
            />
            {/* Inner disc */}
            <circle
              cx={markerX}
              cy={markerY}
              r="5"
              fill="#f9e6b6"
              stroke="#131923"
              strokeWidth="2"
            />
          </g>
        ) : (
          <g>
            {/* Below horizon marker */}
            <circle
              cx={markerX}
              cy={horizonY + 10}
              r="4"
              fill="#526478"
              stroke="#1a2533"
              strokeWidth="1.5"
            />
          </g>
        )}
      </svg>

      {/* Dynamic Telemetry Tag below arc */}
      <div className="celestial-arc-tag">
        {isAbove ? (
          <>
            <span className="arc-tag-pill active">
              <Eye size={12} /> Above Horizon
            </span>
            <span className="arc-coords">
              Alt: <strong>{moon.altitude_deg}°</strong> · Az:{' '}
              <strong>{moon.azimuth_deg}° {moon.azimuth_cardinal}</strong>
            </span>
          </>
        ) : (
          <>
            <span className="arc-tag-pill muted">
              <EyeOff size={12} /> Below Horizon
            </span>
            <span className="arc-coords muted">
              {moon.moonrise
                ? `Rises in ${formatRemaining(moon.moonrise - moon.timestamp)}`
                : 'Below local horizon'}
            </span>
          </>
        )}
      </div>
    </div>
  )
}

function formatRemaining(sec: number): string {
  if (sec <= 0) return 'now'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function AstronomyPanel({
  data,
  loading,
  timezone = 'America/Los_Angeles',
  units = 'imperial',
  onConfigureClick,
}: AstronomyPanelProps) {
  const fmtTime = (ts: number | null | undefined) => {
    if (!ts) return '—'
    return new Date(ts * 1000).toLocaleTimeString(undefined, {
      timeZone: timezone,
      hour: 'numeric',
      minute: '2-digit',
    })
  }

  if (!data || !data.available || !data.moon) {
    return (
      <section className="astro-panel empty">
        <div className="astro-panel-heading">
          <div className="panel-title-wrap">
            <span className="eyebrow">ASTRONOMY & NIGHT SKY</span>
            <h2>Celestial Observatory</h2>
          </div>
        </div>
        <div className="astro-empty-content">
          <Telescope size={32} className="muted-icon" />
          <div>
            <h3>Observation Location Required</h3>
            <p>
              Moon stats, topocentric coordinates, visible planets, and meteor showers require
              latitude and longitude coordinates in your settings.
            </p>
          </div>
          {onConfigureClick && (
            <button className="astro-setup-btn" onClick={onConfigureClick}>
              Open Settings <ChevronRight size={14} />
            </button>
          )}
        </div>
      </section>
    )
  }

  const { moon, twilight, planets = [], events = [], stargazing } = data
  const distFormatted =
    units === 'metric'
      ? `${moon.distance_km.toLocaleString()} km`
      : `${Math.round(moon.distance_km * 0.621371).toLocaleString()} mi`

  // Stargazing rating color
  const ratingColor =
    stargazing?.rating === 'Excellent'
      ? '#56dfb2'
      : stargazing?.rating === 'Good'
      ? '#70d8c2'
      : stargazing?.rating === 'Fair'
      ? '#f3cb7c'
      : '#f17676'

  return (
    <section className="astro-panel">
      {/* Panel Header */}
      <div className="astro-panel-heading">
        <div className="panel-title-wrap">
          <span className="eyebrow">OBSERVATORY</span>
          <h2>Night Sky & Celestial Events</h2>
        </div>

        <div className="astro-header-actions">
          {stargazing && (
            <div
              className="stargazing-badge"
              title={`Darkness: ${stargazing.dark_hours}h · ${stargazing.moon_interference}${
                stargazing.cloud_cover_pct != null ? ` · ${Math.round(stargazing.cloud_cover_pct)}% clouds` : ''
              }`}
            >
              <Sparkles size={13} style={{ color: ratingColor }} />
              <span>Stargazing:</span>
              <strong style={{ color: ratingColor }}>
                {stargazing.rating} ({stargazing.score}/10)
              </strong>
            </div>
          )}
          <span className={`astro-live-pill ${moon.is_visible ? 'visible' : ''}`}>
            {moon.is_visible ? (
              <>
                <span className="dot" /> Moon Visible
              </>
            ) : (
              <>
                <span className="dot off" /> Moon Set
              </>
            )}
          </span>
        </div>
      </div>

      {/* Option 4: 3-Wing Hybrid Observatory Grid */}
      <div className="astro-hybrid-grid">
        {/* Wing 1: Moon Telemetry */}
        <div className="astro-card moon-telemetry-card">
          <div className="card-subheading">
            <Moon size={14} /> LUNAR TELEMETRY
          </div>

          <div className="moon-main-row">
            <RealisticMoonSvg
              illumination={moon.illumination_pct}
              isWaxing={moon.is_waxing}
              size={110}
            />

            <div className="moon-headline-stats">
              <div className="moon-phase-title">
                <span className="moon-pct">{moon.illumination_pct}%</span>
                <span className="moon-phase-name">{moon.phase_name}</span>
              </div>
              <div className="moon-sub-traits">
                <span>Age: <strong>{moon.age_days}d</strong></span>
                <span>In: <strong>{moon.constellation}</strong></span>
              </div>
              {moon.is_supermoon && (
                <span className="supermoon-badge">
                  <Star size={11} fill="#f3cb7c" /> Supermoon
                </span>
              )}
            </div>
          </div>

          <div className="moon-stats-grid">
            <div className="stat-box">
              <span className="stat-label">Altitude</span>
              <span className="stat-val">{moon.altitude_deg > 0 ? `+${moon.altitude_deg}` : moon.altitude_deg}°</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Azimuth</span>
              <span className="stat-val">{moon.azimuth_deg}° {moon.azimuth_cardinal}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Moonrise</span>
              <span className="stat-val">{fmtTime(moon.moonrise)}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Moonset</span>
              <span className="stat-val">{fmtTime(moon.moonset)}</span>
            </div>
          </div>
          <div className="moon-distance-foot">
            <span>Distance: <strong>{distFormatted}</strong></span>
            {moon.transit_time && (
              <span>Transit: <strong>{fmtTime(moon.transit_time)}</strong></span>
            )}
          </div>
        </div>

        {/* Wing 2: Celestial Sky Dome Arc */}
        <div className="astro-card celestial-arc-card">
          <div className="card-subheading">
            <Compass size={14} /> LOCAL SKY TRAJECTORY
          </div>
          <CelestialArc moon={moon} />
          {twilight && (
            <div className="twilight-strip">
              <span title="Sun at -18° (true darkness)">
                <Clock size={11} /> Darkness: <strong>{twilight.darkness_hours}h</strong>
              </span>
              <span>Astro Dusk: <strong>{fmtTime(twilight.astronomical_dusk)}</strong></span>
            </div>
          )}
        </div>

        {/* Wing 3: Visible Planets Tonight & Upcoming Events */}
        <div className="astro-card planets-events-card">
          <div className="card-subheading">
            <Orbit size={14} /> PLANETS & CELESTIAL EVENTS
          </div>

          {/* Planets Section */}
          <div className="planets-section">
            <span className="section-label">Visible Planets Tonight</span>
            <div className="planets-list">
              {planets.filter(p => p.visible_tonight).slice(0, 3).map(p => (
                <div className="planet-pill" key={p.name} title={p.viewing_window}>
                  <div className="planet-dot-wrap">
                    <span
                      className="planet-color-dot"
                      style={{
                        background:
                          p.name === 'Venus'
                            ? '#fcebbb'
                            : p.name === 'Jupiter'
                            ? '#e3a968'
                            : p.name === 'Saturn'
                            ? '#deb887'
                            : p.name === 'Mars'
                            ? '#e26d5c'
                            : '#9ec5e8',
                      }}
                    />
                    <strong className="planet-name">{p.name}</strong>
                  </div>
                  <span className="planet-mag">{p.magnitude > 0 ? `+${p.magnitude}` : p.magnitude} mag</span>
                  <span className="planet-pos">{p.altitude_deg > 0 ? `+${Math.round(p.altitude_deg)}°` : 'Set'} {p.azimuth_cardinal}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Upcoming Events Carousel/Cards */}
          <div className="events-section">
            <span className="section-label">Upcoming Sky Highlights</span>
            <div className="events-cards-wrap">
              {events.slice(0, 2).map((ev, i) => (
                <div className="astro-event-card" key={i}>
                  <div className="event-top">
                    <span className="event-title">
                      {ev.type === 'meteor_shower' ? (
                        <Sparkles size={13} style={{ color: '#70d8c2' }} />
                      ) : (
                        <Moon size={13} style={{ color: '#f3cb7c' }} />
                      )}
                      {ev.name}
                    </span>
                    <span className="event-days">
                      {ev.days_until === 0 ? 'Today' : ev.days_until === 1 ? 'Tomorrow' : `In ${ev.days_until}d`}
                    </span>
                  </div>
                  <div className="event-desc">{ev.summary}</div>
                  {ev.dark_sky_rating && (
                    <div className="event-foot">
                      <span className={`rating-pill ${ev.dark_sky_rating.toLowerCase()}`}>
                        Viewing: {ev.dark_sky_rating}
                      </span>
                      {ev.zhr && <span className="zhr-label">ZHR: ~{ev.zhr}/hr</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
