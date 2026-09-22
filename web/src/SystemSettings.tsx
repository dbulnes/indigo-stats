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
        const tzRes = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m&timezone=auto`)
        const tzData = await tzRes.json()
        tz = tzData.timezone
      } catch {
        // Fallback to configured timezone
      }
      
      setSettings(s => ({...s, FORECAST_LATITUDE: String(lat), FORECAST_LONGITUDE: String(lon), TZ: tz || s.TZ}))
      setLookupState(`Selected ${r.text}. Auto-selected timezone: ${tz || 'None'}`)
    } catch (e: unknown) {
      setLookupState(`Error: ${e instanceof Error ? e.message : 'Lookup failed'}`)
    }
  }

  const save = async (e:React.FormEvent) => {
    e.preventDefault()
    setSaving(true); setMsg({text:'', type:''})
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(settings)
      })
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to save')
      setMsg({text:'Settings saved successfully. Changes may take a minute to apply.', type:'success'})
    } catch (e: unknown) {
      setMsg({text: e instanceof Error ? e.message : 'Failed to save', type:'error'})
    }
    setSaving(false)
  }
  
  const updateTimezone = async (lat: string, lon: string) => {
    if (!lat || !lon) return
    try {
      const tzRes = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m&timezone=auto`)
      const tzData = await tzRes.json()
      if (tzData.timezone) {
        setSettings(s => ({...s, TZ: tzData.timezone}))
        setLookupState(`Auto-selected timezone: ${tzData.timezone}`)
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
  
  if (loading) return <div>Loading settings...</div>
  return <form onSubmit={save} className="settings-form">
    <div className="form-group"><label>Sensor Host / IP</label><input name="SENSOR_HOST" value={settings.SENSOR_HOST||''} onChange={handleChange} placeholder="Update private IP..."/></div>
    <div className="form-group">
      <label>Timezone</label>
      <input list="timezones" name="TZ" value={settings.TZ||''} onChange={handleChange} placeholder="e.g. America/Los_Angeles"/>
      <datalist id="timezones">
        {timezones.map(tz => <option key={tz} value={tz} />)}
      </datalist>
    </div>
    <div className="form-group"><label>Forecast Enabled</label><select name="FORECAST_ENABLED" value={settings.FORECAST_ENABLED||'false'} onChange={handleChange}><option value="true">True</option><option value="false">False</option></select></div>
    
    <div className="form-group" style={{padding: '16px', background: '#172130', borderRadius: '8px', position: 'relative', border: '1px solid #2b3646'}}>
      <label style={{marginBottom: '4px'}}>Forecast Location (Lat/Lon)</label>
      <p style={{fontSize: '0.85em', opacity: 0.7, margin: '0 0 12px 0'}}>
        {settings.HAS_FORECAST_LOCATION === 'true' && !settings.FORECAST_LATITUDE 
          ? 'Coordinates are currently configured (hidden for privacy).' 
          : 'Set coordinates to enable regional forecasting.'}
      </p>
      
      <div style={{position: 'relative'}} ref={dropdownRef}>
        <input 
          value={address} 
          onChange={e=>setAddress(e.target.value)} 
          placeholder="Type any full street address or city..." 
          style={{width: '100%', marginBottom: '12px'}}
        />
        {results.length > 0 && (
          <div style={{
            position: 'absolute', 
            top: '100%', 
            left: 0, 
            right: 0,
            zIndex: 50, 
            background: '#1a2332', 
            border: '1px solid #3b4a60', 
            borderRadius: '6px', 
            marginTop: '4px', 
            maxHeight: '220px', 
            overflowY: 'auto',
            boxShadow: '0 10px 25px rgba(0,0,0,0.5)'
          }}>
            {results.map((r, i) => (
              <div 
                key={i} 
                onClick={() => selectResult(r)} 
                style={{
                  padding: '12px 16px', 
                  cursor: 'pointer', 
                  borderBottom: i === results.length - 1 ? 'none' : '1px solid #2b3646', 
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'background 0.15s ease'
                }}
                onMouseOver={(e) => (e.currentTarget.style.background = '#26354a')}
                onMouseOut={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                <span style={{fontSize: '0.95em', color: '#d2dbea'}}>{r.text}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {lookupState && <div style={{fontSize: '0.85em', color: '#70d8c2', marginBottom: '12px'}}>{lookupState}</div>}
      
      <div style={{display: 'flex', gap: '12px'}}>
        <div style={{flex: 1}}>
          <input name="FORECAST_LATITUDE" value={settings.FORECAST_LATITUDE||''} onChange={handleChange} onBlur={handleBlur} placeholder="Latitude"/>
        </div>
        <div style={{flex: 1}}>
          <input name="FORECAST_LONGITUDE" value={settings.FORECAST_LONGITUDE||''} onChange={handleChange} onBlur={handleBlur} placeholder="Longitude"/>
        </div>
      </div>
    </div>
    
    <div className="form-group"><label>PM Method</label><select name="PM_METHOD" value={settings.PM_METHOD||'cf1'} onChange={handleChange}><option value="cf1">Raw CF=1</option><option value="epa2021">EPA 2021</option></select></div>
    <div className="form-group"><label>Environment Mode</label><select name="ENVIRONMENT_MODE" value={settings.ENVIRONMENT_MODE||'purpleair'} onChange={handleChange}><option value="purpleair">PurpleAir estimated ambient</option><option value="raw">Raw operating readings</option><option value="simple">Simple correction</option></select></div>
    <div className="form-group"><label>Placement</label><select name="SENSOR_PLACEMENT" value={settings.SENSOR_PLACEMENT||'outdoors'} onChange={handleChange}><option value="outdoors">Outdoors</option><option value="indoors">Indoors</option></select></div>
    <div className="form-group"><label>Units</label><select name="UNITS" value={settings.UNITS||'imperial'} onChange={handleChange}><option value="imperial">Imperial (°F, mph)</option><option value="metric">Metric (°C, km/h)</option></select></div>
    <div className="form-actions"><button type="submit" disabled={saving}>{saving?'Saving...':'Save Settings'}</button></div>
    {msg.text && <div className={`notice ${msg.type}`}>{msg.text}</div>}
  </form>
}
