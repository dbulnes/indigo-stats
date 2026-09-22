import React, { useEffect, useRef, useState } from 'react'

type Provider = 'disabled' | 'filesystem' | 's3' | 'google_drive'
type Encryption = 'default' | 'sse-s3' | 'sse-kms'

type BackupStatus = {
  provider: Provider
  enabled: boolean
  local_retention: number
  remote_retention: number
  credentials_configured: boolean
  google_linked: boolean
  oauth_configured: boolean
  mount_detected: boolean
  last_attempt: number | null
  last_success: number | null
  error: string | null
  bucket_configured?: boolean
  prefix_configured?: boolean
  region_configured?: boolean
  endpoint_configured?: boolean
  encryption?: Encryption
}

type S3Draft = {
  bucket: string
  prefix: string
  region: string
  endpoint: string
  encryption: Encryption
}

const emptyS3 = (encryption: Encryption = 'default'): S3Draft => ({
  bucket: '', prefix: 'indigo-stats', region: '', endpoint: '', encryption,
})
const when = (value: number | null) => value ? new Date(value * 1000).toLocaleString() : 'Not yet'
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Request failed'

export function BackupSettings() {
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [provider, setProvider] = useState<Provider>('disabled')
  const [s3, setS3] = useState<S3Draft>(emptyS3())
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState({ text: '', type: '' })
  const popupTimerRef = useRef<number | null>(null)

  const load = async (syncForm = false) => {
    const response = await fetch('/api/backups', { cache: 'no-store' })
    if (!response.ok) throw new Error('Unable to load backup status')
    const data = await response.json() as BackupStatus
    setStatus(data)
    if (syncForm) {
      setProvider(data.provider)
      setS3(emptyS3(data.encryption || 'default'))
      setDirty(false)
    }
    return data
  }

  useEffect(() => {
    void load(true).catch(error => setMessage({ text: errorText(error), type: 'error' }))
    const refresh = () => void load(false).catch(() => undefined)
    const handleAuthMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'indigo-google-auth-success') return
      refresh()
      setMessage({ text: 'Google Drive linked successfully.', type: 'success' })
    }
    window.addEventListener('message', handleAuthMessage)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('message', handleAuthMessage)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
      if (popupTimerRef.current !== null) window.clearInterval(popupTimerRef.current)
    }
  }, [])

  const linkGoogle = () => {
    if (!status?.oauth_configured) {
      setMessage({ text: 'Google OAuth secrets are not configured in container environment.', type: 'error' })
      return
    }
    setMessage({ text: '', type: '' })
    const width = 520
    const height = 680
    const left = window.screenX + Math.max(0, Math.round((window.outerWidth - width) / 2))
    const top = window.screenY + Math.max(0, Math.round((window.outerHeight - height) / 2))
    const popup = window.open(
      '/api/backups/google/connect',
      'indigoGoogleOAuth',
      `width=${width},height=${height},left=${left},top=${top},status=no,menubar=no,toolbar=no`,
    )
    if (!popup) {
      window.location.href = '/api/backups/google/connect'
      return
    }
    popup.focus()
    if (popupTimerRef.current !== null) window.clearInterval(popupTimerRef.current)
    let checks = 0
    popupTimerRef.current = window.setInterval(() => {
      checks += 1
      if (!popup || popup.closed || checks > 300) {
        if (popupTimerRef.current !== null) window.clearInterval(popupTimerRef.current)
        popupTimerRef.current = null
        if (!popup || popup.closed) {
          void load(false).then(data => {
            if (data.google_linked) setMessage({ text: 'Google Drive linked successfully.', type: 'success' })
          }).catch(() => undefined)
        }
      }
    }, 1000)
  }

  const action = async (name: string, path: string, init: RequestInit = {}) => {
    setBusy(name)
    setMessage({ text: '', type: '' })
    try {
      const response = await fetch(path, {
        ...init,
        headers: { 'X-Indigo-Request': '1', ...init.headers },
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Request failed')
      await load(name === 'save')
      const success = {
        save: 'Backup destination updated.',
        test: 'Connection test passed.',
        run: 'Backup started. Status will refresh when this window regains focus.',
        unlink: 'Google Drive unlinked.',
      }[name] || 'Request completed.'
      setMessage({ text: success, type: 'success' })
    } catch (error: unknown) {
      setMessage({ text: errorText(error), type: 'error' })
    } finally {
      setBusy('')
    }
  }

  const save = () => {
    const body: Record<string, string> = { provider }
    if (provider === 's3') Object.assign(body, s3)
    void action('save', '/api/backups', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  }

  if (!status) return <p className="muted">Loading backup configuration…</p>
  const savedActionsDisabled = Boolean(busy) || dirty || !status.enabled

  return <div className="backup-settings">
    <div className="backup-health">
      <div><strong>Local snapshots</strong><span>Daily · latest {status.local_retention} retained</span></div>
      <div><strong>Off-server copies</strong><span>{status.enabled ? `${status.provider.replace('_', ' ')} · latest ${status.remote_retention} completed retained` : 'Disabled'}</span></div>
      <div><strong>Last remote success</strong><span>{status.enabled ? when(status.last_success) : 'Disabled'}</span></div>
    </div>
    {status.error && <div className="notice error">{status.error}</div>}
    <div className="form-group">
      <label>Destination</label>
      <select value={provider} onChange={event => { setProvider(event.target.value as Provider); setDirty(true) }}>
        <option value="disabled">Disabled</option>
        <option value="filesystem">Mounted filesystem (/offsite)</option>
        <option value="s3">S3 / S3-compatible</option>
        <option value="google_drive">Google Drive</option>
      </select>
    </div>
    {provider === 'filesystem' && <div className={'notice ' + (status.mount_detected ? 'success' : 'error')}>
      {status.mount_detected ? 'A distinct /offsite mount was detected.' : 'No distinct /offsite mount is detected. Mount host-managed storage before testing.'}
    </div>}
    {provider === 's3' && <div className="backup-fields">
      <p className="muted">Stored values remain redacted. Enter all destination fields when saving changes.</p>
      <div className="form-group"><label>Bucket {status.bucket_configured ? '(configured)' : ''}</label><input autoComplete="off" value={s3.bucket} onChange={event => { setS3({ ...s3, bucket: event.target.value }); setDirty(true) }}/></div>
      <div className="form-group"><label>Prefix {status.prefix_configured ? '(configured)' : ''}</label><input autoComplete="off" value={s3.prefix} onChange={event => { setS3({ ...s3, prefix: event.target.value }); setDirty(true) }}/></div>
      <div className="form-group"><label>Region {status.region_configured ? '(configured)' : ''}</label><input autoComplete="off" value={s3.region} onChange={event => { setS3({ ...s3, region: event.target.value }); setDirty(true) }}/></div>
      <div className="form-group"><label>Custom HTTPS endpoint {status.endpoint_configured ? '(configured)' : ''}</label><input autoComplete="off" value={s3.endpoint} onChange={event => { setS3({ ...s3, endpoint: event.target.value }); setDirty(true) }}/></div>
      <div className="form-group"><label>Server-side encryption</label><select value={s3.encryption} onChange={event => { setS3({ ...s3, encryption: event.target.value as Encryption }); setDirty(true) }}><option value="default">Provider default</option><option value="sse-s3">SSE-S3</option><option value="sse-kms">SSE-KMS</option></select></div>
      <p className="muted">Credentials come only from container secrets. Status: {status.credentials_configured ? 'configured' : 'not configured'}.</p>
    </div>}
    {provider === 'google_drive' && <div>
      <p className="muted">OAuth setup: {status.oauth_configured ? 'configured' : 'not configured'} · account: {status.google_linked ? 'linked' : 'not linked'}.</p>
      {!dirty && (status.google_linked
        ? <button className="secondary" onClick={() => void action('unlink', '/api/backups/google/unlink', { method: 'POST' })}>Unlink Google Drive</button>
        : <button className="secondary" onClick={linkGoogle}>Link Google Drive</button>)}
    </div>}
    {dirty && <p className="muted small">Save this destination before testing or starting a backup.</p>}
    <div className="backup-actions">
      <button onClick={save} disabled={Boolean(busy) || !dirty}>{busy === 'save' ? 'Saving…' : 'Save destination'}</button>
      <button className="secondary" onClick={() => void action('test', '/api/backups/test', { method: 'POST' })} disabled={savedActionsDisabled}>Test connection</button>
      <button className="secondary" onClick={() => void action('run', '/api/backups/run', { method: 'POST' })} disabled={savedActionsDisabled}>Back up now</button>
    </div>
    {message.text && <div className={`notice ${message.type}`}>{message.text}</div>}
    <p className="muted small">Remote snapshots contain private settings and raw sensor metadata. Indigo Stats does not add client-side encryption; protect the destination with provider/filesystem access controls and encryption.</p>
  </div>
}
