import React, { useEffect, useState } from 'react'

type BackupStatus = {
  provider: 'disabled'|'filesystem'|'s3'|'google_drive'
  enabled: boolean
  local_retention: number
  remote_retention: number
  credentials_configured: boolean
  google_linked: boolean
  oauth_configured: boolean
  mount_detected: boolean
  last_attempt: number|null
  last_success: number|null
  error: string|null
  bucket_configured?: boolean
  prefix_configured?: boolean
  region_configured?: boolean
  endpoint_configured?: boolean
  encryption?: string
}

const when = (value:number|null) => value ? new Date(value*1000).toLocaleString() : 'Not yet'

export function BackupSettings() {
  const [status,setStatus]=useState<BackupStatus|null>(null)
  const [provider,setProvider]=useState<BackupStatus['provider']>('disabled')
  const [s3,setS3]=useState({bucket:'',prefix:'indigo-stats',region:'',endpoint:'',encryption:'default'})
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState({text:'',type:''})
  const load=async()=>{
    const r=await fetch('/api/backups',{cache:'no-store'}); if(!r.ok) throw new Error('Unable to load backup status')
    const data=await r.json(); setStatus(data); setProvider(data.provider); setS3(x=>({...x,encryption:data.encryption||x.encryption}))
  }
  useEffect(()=>{load().catch(e=>setMessage({text:e.message,type:'error'}))},[])
  const action=async(name:string,path:string,init?:RequestInit)=>{
    setBusy(name); setMessage({text:'',type:''})
    try { const r=await fetch(path,init); const data=await r.json().catch(()=>({})); if(!r.ok) throw new Error(data.detail||'Request failed'); await load(); setMessage({text:name==='run'?'Backup started. Status will update in the background.':'Backup destination updated.',type:'success'}) }
    catch(e:any){setMessage({text:e.message,type:'error'})} finally{setBusy('')}
  }
  const save=()=>{
    const body:any={provider}
    if(provider==='s3') Object.assign(body,s3)
    action('save','/api/backups',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  }
  if(!status) return <p className="muted">Loading backup configuration…</p>
  return <div className="backup-settings">
    <div className="backup-health">
      <div><strong>Local snapshots</strong><span>Daily · latest {status.local_retention} retained</span></div>
      <div><strong>Off-server copies</strong><span>{status.enabled ? `${status.provider.replace('_',' ')} · latest ${status.remote_retention} completed retained` : 'Disabled'}</span></div>
      <div><strong>Last remote success</strong><span>{when(status.last_success)}</span></div>
    </div>
    {status.error && <div className="notice error">{status.error}</div>}
    <div className="form-group"><label>Destination</label><select value={provider} onChange={e=>setProvider(e.target.value as BackupStatus['provider'])}>
      <option value="disabled">Disabled</option><option value="filesystem">Mounted filesystem (/offsite)</option><option value="s3">S3 / S3-compatible</option><option value="google_drive">Google Drive</option>
    </select></div>
    {provider==='filesystem' && <div className={'notice '+(status.mount_detected?'success':'error')}>{status.mount_detected?'A distinct /offsite mount was detected.':'No distinct /offsite mount is detected. Mount host-managed storage before testing.'}</div>}
    {provider==='s3' && <div className="backup-fields">
      <p className="muted">Stored values remain redacted. Leave nothing to inference: enter all destination fields when saving changes.</p>
      <div className="form-group"><label>Bucket {status.bucket_configured?'(configured)':''}</label><input value={s3.bucket} onChange={e=>setS3({...s3,bucket:e.target.value})}/></div>
      <div className="form-group"><label>Prefix {status.prefix_configured?'(configured)':''}</label><input value={s3.prefix} onChange={e=>setS3({...s3,prefix:e.target.value})}/></div>
      <div className="form-group"><label>Region {status.region_configured?'(configured)':''}</label><input value={s3.region} onChange={e=>setS3({...s3,region:e.target.value})}/></div>
      <div className="form-group"><label>Custom HTTPS endpoint {status.endpoint_configured?'(configured)':''}</label><input value={s3.endpoint} onChange={e=>setS3({...s3,endpoint:e.target.value})}/></div>
      <div className="form-group"><label>Server-side encryption</label><select value={s3.encryption} onChange={e=>setS3({...s3,encryption:e.target.value})}><option value="default">Provider default</option><option value="sse-s3">SSE-S3</option><option value="sse-kms">SSE-KMS</option></select></div>
      <p className="muted">Credentials come only from container secrets. Status: {status.credentials_configured?'configured':'not configured'}.</p>
    </div>}
    {provider==='google_drive' && <div>
      <p className="muted">OAuth setup: {status.oauth_configured?'configured':'not configured'} · account: {status.google_linked?'linked':'not linked'}.</p>
      {status.google_linked ? <button className="secondary" onClick={()=>action('unlink','/api/backups/google/unlink',{method:'POST'})}>Unlink Google Drive</button> : <a className="secondary" href="/api/backups/google/connect" target="_blank" rel="noreferrer">Link Google Drive</a>}
    </div>}
    <div className="backup-actions"><button onClick={save} disabled={!!busy}>{busy==='save'?'Saving…':'Save destination'}</button><button className="secondary" onClick={()=>action('test','/api/backups/test',{method:'POST'})} disabled={!!busy||provider==='disabled'}>Test connection</button><button className="secondary" onClick={()=>action('run','/api/backups/run',{method:'POST'})} disabled={!!busy||provider==='disabled'}>Back up now</button></div>
    {message.text && <div className={`notice ${message.type}`}>{message.text}</div>}
    <p className="muted small">Remote snapshots contain private settings and raw sensor metadata. Indigo Stats does not add client-side encryption; protect the destination with provider/filesystem access controls and encryption.</p>
  </div>
}
