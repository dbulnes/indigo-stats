import React, { useEffect, useState, useRef } from 'react'

type Suggestion = { text: string; magicKey: string }

export function SystemSettings() {
  const [settings, setSettings] = useState<Record<string,string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState({text:'', type:''})
  const [address, setAddress] = useState('')
  const [lookupState, setLookupState] = useState('')
  const [results, setResults] = useState<Suggestion[]>([])
  const dropdownRef = useRef<HTMLDivElement>(null)
  
  const timezones = typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : ['America/Los_Angeles', 'America/New_York', 'UTC'];
  
  useEffect(() => {
    fetch('/api/settings').then(r=>r.json()).then(s=>{
       const stringified = Object.fromEntries(Object.entries(s).map(([k,v])=>[k, v==null?'':String(v)]))
       setSettings(stringified)
       setLoading(false)
    })
  }, [])
  
  useEffect(() => {
    if (!address.trim()) { setResults([]); return; }
    const ctrl = new AbortController()
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest?text=${encodeURIComponent(address)}&f=json`, { signal: ctrl.signal })
        const data = await res.json()
        setResults(data.suggestions || [])
      } catch (e: unknown) {
        if ((e as Error)?.name !== 'AbortError') setResults([])
      }
    }, 300)
    return () => {
      clearTimeout(t)
      ctrl.abort()
    }
  }, [address])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setResults([])
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const selectResult = async (r: Suggestion) => {
    setAddress('')
    setResults([])
    setLookupState('Fetching coordinates...')
    
    try {
      const coordRes = await fetch(`https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates?SingleLine=${encodeURIComponent(r.text)}&magicKey=${r.magicKey}&f=json`)
      const coordData = await coordRes.json()
      
      if (!coordData.candidates || coordData.candidates.length === 0) {
        throw new Error('Coordinates not found')
      }
      
      const lat = coordData.candidates[0].location.y
      const lon = coordData.candidates[0].location.x
      
      let tz = settings.TZ
      try {
        const tzRes = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&timezone=auto`)
        const tzData = await tzRes.json()
        if (tzData.timezone) {
          tz = tzData.timezone
        }
      } catch {
        // Fallback to existing or browser
      }

      setSettings(s => ({
        ...s,
        FORECAST_LATITUDE: lat.toFixed(4),
        FORECAST_LONGITUDE: lon.toFixed(4),
        TZ: tz,
        FORECAST_ENABLED: 'true'
      }))
      setLookupState(`Found: ${lat.toFixed(4)}, ${lon.toFixed(4)} (${tz})`)
    } catch {
      setLookupState('Error resolving address candidates.')
    }
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setMsg({text:'', type:''})
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Indigo-Request': '1' },
        body: JSON.stringify(settings)
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to save settings')
      }
      setMsg({text: 'Settings updated successfully.', type: 'success'})
    } catch (err: unknown) {
      setMsg({text: (err as Error).message || 'Failed to save settings.', type: 'error'})
    } finally {
      setSaving(false)
    }
  }

  const updateTimezone = async (latStr: string, lonStr: string) => {
    const lat = parseFloat(latStr)
    const lon = parseFloat(lonStr)
    if (isNaN(lat) || isNaN(lon)) return
    try {
      const tzRes = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&timezone=auto`)
      const tzData = await tzRes.json()
      if (tzData.timezone) {
        setSettings(s => ({ ...s, TZ: tzData.timezone }))
      }
    } catch {
      // Ignore background errors
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setSettings(s => ({...s, [e.target.name]: e.target.value}))
  const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    if (e.target.name === 'FORECAST_LATITUDE' || e.target.name === 'FORECAST_LONGITUDE') {
      const lat = e.target.name === 'FORECAST_LATITUDE' ? e.target.value : settings.FORECAST_LATITUDE;
      const lon = e.target.name === 'FORECAST_LONGITUDE' ? e.target.value : settings.FORECAST_LONGITUDE;
      updateTimezone(lat, lon);
    }
  }
  
  if (loading) return <div className="settings-skeleton" role="status" aria-label="Loading system settings">
    <span className="skeleton-line wide" />
    <span className="skeleton-line" />
    <span className="skeleton-line short" />
  </div>
  return <form onSubmit={save} className="settings-form">
    <div className="form-group">
      <label>Sensor Source</label>
      <select name="SENSOR_SOURCE" value={settings.SENSOR_SOURCE || 'local'} onChange={handleChange}>
        <option value="local">Local network (Sensor IP)</option>
        <option value="purpleair_api">PurpleAir API (Remote sensor)</option>
      </select>
    </div>

    {(settings.SENSOR_SOURCE || 'local') === 'local' ? (
      <div className="form-group">
        <label>Sensor Host / IP</label>
        <p className="settings-location-copy">
          {settings.HAS_SENSOR_HOST === 'true' && !settings.SENSOR_HOST
            ? 'Sensor IP is currently configured (hidden for privacy).'
            : 'Set the private IPv4 address of your local PurpleAir sensor.'}
        </p>
        <input name="SENSOR_HOST" value={settings.SENSOR_HOST||''} onChange={handleChange} placeholder="Update private IP..."/>
      </div>
    ) : (
      <>
        <div className="form-group">
          <label>PurpleAir Sensor Index</label>
          <p className="settings-location-copy">
            The numeric ID of the sensor from the PurpleAir map (e.g. 175253).
          </p>
          <input name="PURPLEAIR_SENSOR_INDEX" value={settings.PURPLEAIR_SENSOR_INDEX || ''} onChange={handleChange} placeholder="e.g. 175253" />
        </div>
        <div className="form-group">
          <label>PurpleAir API Read Key</label>
          <p className="settings-location-copy">
            {settings.HAS_PURPLEAIR_API_KEY === 'true' && !settings.PURPLEAIR_API_KEY
              ? 'API key is configured (hidden for privacy).'
              : 'Required to access sensors via the PurpleAir API.'}
          </p>
          <input type="password" name="PURPLEAIR_API_KEY" value={settings.PURPLEAIR_API_KEY || ''} onChange={handleChange} placeholder="Enter PurpleAir API Read Key..." />
        </div>
        <div className="form-group">
          <label>PurpleAir Sensor Read Key (Optional)</label>
          <p className="settings-location-copy">
            {settings.HAS_PURPLEAIR_READ_KEY === 'true' && !settings.PURPLEAIR_READ_KEY
              ? 'Sensor read key is configured (hidden for privacy).'
              : 'Only required if the sensor is configured as private on PurpleAir.'}
          </p>
          <input type="password" name="PURPLEAIR_READ_KEY" value={settings.PURPLEAIR_READ_KEY || ''} onChange={handleChange} placeholder="Optional private read key..." />
        </div>
      </>
    )}

    <div className="form-group">
      <label>Timezone</label>
      <input list="timezones" name="TZ" value={settings.TZ||''} onChange={handleChange} placeholder="e.g. America/Los_Angeles"/>
      <datalist id="timezones">
        {timezones.map(tz => <option key={tz} value={tz} />)}
      </datalist>
    </div>
    <div className="form-group"><label>Forecast Enabled</label><select name="FORECAST_ENABLED" value={settings.FORECAST_ENABLED||'false'} onChange={handleChange}><option value="true">True</option><option value="false">False</option></select></div>
    
    <div className="form-group settings-location">
      <label>Forecast Location (Lat/Lon)</label>
      <p className="settings-location-copy">
        {settings.HAS_FORECAST_LOCATION === 'true' && !settings.FORECAST_LATITUDE 
          ? 'Coordinates are currently configured (hidden for privacy).' 
          : 'Set coordinates to enable regional forecasting.'}
      </p>
      
      <div className="address-lookup" ref={dropdownRef}>
        <input 
          value={address} 
          onChange={e=>setAddress(e.target.value)} 
          placeholder="Type any full street address or city..." 
          className="address-input"
        />
        {results.length > 0 && (
          <div className="suggestion-list">
            {results.map(r => (
              <button
                type="button"
                key={r.magicKey}
                onClick={() => selectResult(r)} 
                className="suggestion-item"
              >
                {r.text}
              </button>
            ))}
          </div>
        )}
      </div>

      {lookupState && <div className={`lookup-state ${lookupState.startsWith('Error') ? 'error' : ''}`} role="status">{lookupState}</div>}
      
      <div className="coordinate-grid">
        <div>
          <input name="FORECAST_LATITUDE" value={settings.FORECAST_LATITUDE||''} onChange={handleChange} onBlur={handleBlur} placeholder="Latitude"/>
        </div>
        <div>
          <input name="FORECAST_LONGITUDE" value={settings.FORECAST_LONGITUDE||''} onChange={handleChange} onBlur={handleBlur} placeholder="Longitude"/>
        </div>
      </div>
    </div>
    
    <div className="form-group"><label>PM Method</label><select name="PM_METHOD" value={settings.PM_METHOD||'cf1'} onChange={handleChange}><option value="cf1">Raw CF=1</option><option value="epa2021">EPA 2021</option></select></div>
    <div className="form-group"><label>Environment Mode</label><select name="ENVIRONMENT_MODE" value={settings.ENVIRONMENT_MODE||'purpleair'} onChange={handleChange}><option value="purpleair">PurpleAir estimated ambient</option><option value="raw">Raw operating readings</option><option value="simple">Simple correction</option></select></div>
    <div className="form-group"><label>Placement</label><select name="SENSOR_PLACEMENT" value={settings.SENSOR_PLACEMENT||'outdoors'} onChange={handleChange}><option value="outdoors">Outdoors</option><option value="indoors">Indoors</option></select></div>
    <div className="form-group"><label>Units</label><select name="UNITS" value={settings.UNITS||'imperial'} onChange={handleChange}><option value="imperial">Imperial (°F, mph)</option><option value="metric">Metric (°C, km/h)</option></select></div>
    <div className="form-actions"><button type="submit" disabled={saving} aria-busy={saving}>{saving?'Saving…':'Save Settings'}</button></div>
    {msg.text && <div className={`notice ${msg.type}`} role={msg.type === 'error' ? 'alert' : 'status'} aria-live="polite">{msg.text}</div>}
  </form>
}
