import { useCallback, useEffect, useState } from 'react'

export type ZohoAnalyticsStatus = {
  ok: boolean
  configured: boolean
  message: string
  accounts_server_url: string
  analytics_server_url: string
  org_id_set: boolean
  workspace_id_set: boolean
  client_id_set: boolean
  client_secret_set: boolean
  refresh_token_set: boolean
}

type ApiFetch = (path: string, options?: RequestInit) => Promise<Response>

const EMPTY_STATUS: ZohoAnalyticsStatus = {
  ok: false,
  configured: false,
  message: 'Not checked yet',
  accounts_server_url: '',
  analytics_server_url: '',
  org_id_set: false,
  workspace_id_set: false,
  client_id_set: false,
  client_secret_set: false,
  refresh_token_set: false,
}

export function useZohoAnalytics(apiFetch: ApiFetch, { autoLoad = true } = {}) {
  const [status, setStatus] = useState<ZohoAnalyticsStatus>(EMPTY_STATUS)
  const [workspaces, setWorkspaces] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiFetch('/zoho-analytics/status')
      if (!response.ok) throw new Error('Unable to load Zoho Analytics status.')
      setStatus((await response.json()) as ZohoAnalyticsStatus)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load Zoho Analytics status.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  const loadWorkspaces = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiFetch('/zoho-analytics/workspaces')
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to load Zoho workspaces.')
      }
      const data = await response.json()
      setWorkspaces(data.workspaces)
      return data.workspaces
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load Zoho workspaces.')
      throw err
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    if (autoLoad) loadStatus()
  }, [autoLoad, loadStatus])

  return {
    status,
    workspaces,
    loading,
    error,
    loadStatus,
    loadWorkspaces,
  }
}
