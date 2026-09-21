import React, { useEffect, useState } from 'react'

export function SystemSettings() {
  const [settings, setSettings] = useState<Record<string,string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState({text:'', type:''})
  const [address, setAddress] = useState('')
  const [lookupState, setLookupState] = useState('')
  
  const timezones = typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : ['America/Los_Angeles', 'America/New_York', 'UTC'];
  
  useEffect(() => {
    fetch('/api/settings').then(r=>r.json()).then(s=>{
       const stringified = Object.fromEntries(Object.entries(s).map(([k,v])=>[k, v==null?'':String(v)]))
       setSettings(stringified)
       setLoading(false)
    })
  }, [])
  
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
    } catch (e:any) {
      setMsg({text: e.message, type:'error'})
    }
    setSaving(false)
  }
  
  const lookupAddress = async (e:React.MouseEvent) => {
    e.preventDefault()
    if (!address) return
    setLookupState('Looking up...')
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(address)}&format=json&limit=1`, {
        headers: {'User-Agent': 'IndigoStats/1.0'}
      })
      const data = await res.json()
      if (!data.length) throw new Error('Address not found')
      const lat = data[0].lat, lon = data[0].lon
      
      const tzRes = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m&timezone=auto`)
      const tzData = await tzRes.json()
      const tz = tzData.timezone || settings.TZ
      
      setSettings({...settings, FORECAST_LATITUDE: lat, FORECAST_LONGITUDE: lon, TZ: tz})
      setLookupState(`Found coordinates. Auto-selected timezone: ${tz}`)
      setAddress('') // Clear the address so it isn't "stored"
    } catch(e:any) {
      setLookupState('Error: ' + e.message)
    }
  }

  const handleChange = (e:any) => setSettings({...settings, [e.target.name]: e.target.value})
  
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
    
    <div className="form-group" style={{padding: '10px', background: 'rgba(255,255,255,0.05)', borderRadius: '6px'}}>
      <label>Forecast Location (Lat/Lon)</label>
      <p style={{fontSize: '0.85em', opacity: 0.7, margin: '0 0 10px 0'}}>
        {settings.HAS_FORECAST_LOCATION === 'true' && !settings.FORECAST_LATITUDE 
          ? 'Coordinates are currently configured (hidden for privacy).' 
          : 'Set coordinates to enable regional forecasting.'}
      </p>
      <div style={{display: 'flex', gap: '8px', marginBottom: '8px'}}>
        <input value={address} onChange={e=>setAddress(e.target.value)} placeholder="Type an address or city..." style={{flex: 1}}/>
        <button type="button" onClick={lookupAddress}>Lookup</button>
      </div>
      {lookupState && <div style={{fontSize: '0.85em', color: '#70d8c2', marginBottom: '8px'}}>{lookupState}</div>}
      <div style={{display: 'flex', gap: '8px'}}>
        <input name="FORECAST_LATITUDE" value={settings.FORECAST_LATITUDE||''} onChange={handleChange} placeholder="Latitude"/>
        <input name="FORECAST_LONGITUDE" value={settings.FORECAST_LONGITUDE||''} onChange={handleChange} placeholder="Longitude"/>
      </div>
    </div>
    
    <div className="form-group"><label>PM Method</label><select name="PM_METHOD" value={settings.PM_METHOD||'cf1'} onChange={handleChange}><option value="cf1">Raw CF=1</option><option value="epa2021">EPA 2021</option></select></div>
    <div className="form-group"><label>Environment Mode</label><select name="ENVIRONMENT_MODE" value={settings.ENVIRONMENT_MODE||'purpleair'} onChange={handleChange}><option value="purpleair">PurpleAir estimated ambient</option><option value="raw">Raw operating readings</option><option value="simple">Simple correction</option></select></div>
    <div className="form-group"><label>Placement</label><select name="SENSOR_PLACEMENT" value={settings.SENSOR_PLACEMENT||'outdoors'} onChange={handleChange}><option value="outdoors">Outdoors</option><option value="indoors">Indoors</option></select></div>
    <div className="form-actions"><button type="submit" disabled={saving}>{saving?'Saving...':'Save Settings'}</button></div>
    {msg.text && <div className={`notice ${msg.type}`}>{msg.text}</div>}
  </form>
}
