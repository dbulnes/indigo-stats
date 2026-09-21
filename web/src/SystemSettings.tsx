import React, { useEffect, useState } from 'react'

export function SystemSettings() {
  const [settings, setSettings] = useState<Record<string,string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState({text:'', type:''})
  
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
  const handleChange = (e:any) => setSettings({...settings, [e.target.name]: e.target.value})
  
  if (loading) return <div>Loading settings...</div>
  return <form onSubmit={save} className="settings-form">
    <div className="form-group"><label>Sensor Host / IP</label><input name="SENSOR_HOST" value={settings.SENSOR_HOST||''} onChange={handleChange} placeholder="e.g. 192.168.1.50"/></div>
    <div className="form-group"><label>Timezone</label><input name="TZ" value={settings.TZ||''} onChange={handleChange} placeholder="e.g. America/Los_Angeles"/></div>
    <div className="form-group"><label>Forecast Enabled</label><select name="FORECAST_ENABLED" value={settings.FORECAST_ENABLED||'false'} onChange={handleChange}><option value="true">True</option><option value="false">False</option></select></div>
    <div className="form-group"><label>Forecast Latitude</label><input name="FORECAST_LATITUDE" value={settings.FORECAST_LATITUDE||''} onChange={handleChange} placeholder="e.g. 37.7749"/></div>
    <div className="form-group"><label>Forecast Longitude</label><input name="FORECAST_LONGITUDE" value={settings.FORECAST_LONGITUDE||''} onChange={handleChange} placeholder="e.g. -122.4194"/></div>
    <div className="form-group"><label>PM Method</label><select name="PM_METHOD" value={settings.PM_METHOD||'cf1'} onChange={handleChange}><option value="cf1">Raw CF=1</option><option value="epa2021">EPA 2021</option></select></div>
    <div className="form-group"><label>Environment Mode</label><select name="ENVIRONMENT_MODE" value={settings.ENVIRONMENT_MODE||'purpleair'} onChange={handleChange}><option value="purpleair">PurpleAir estimated ambient</option><option value="raw">Raw operating readings</option><option value="simple">Simple correction</option></select></div>
    <div className="form-group"><label>Placement</label><select name="SENSOR_PLACEMENT" value={settings.SENSOR_PLACEMENT||'outdoors'} onChange={handleChange}><option value="outdoors">Outdoors</option><option value="indoors">Indoors</option></select></div>
    <div className="form-actions"><button type="submit" disabled={saving}>{saving?'Saving...':'Save Settings'}</button></div>
    {msg.text && <div className={`notice ${msg.type}`}>{msg.text}</div>}
  </form>
}
