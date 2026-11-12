import { useCallback } from 'react'
import { API_BASE } from '../constants'

export function useApi(token: string, onUnauthorized: () => void) {
  const apiFetch = useCallback(
    async (path: string, options: RequestInit = {}) => {
      const headers = new Headers(options.headers)
      if (
        !headers.has('Content-Type') &&
        options.body &&
        !(options.body instanceof FormData)
      ) {
        headers.set('Content-Type', 'application/json')
      }
      if (token) headers.set('Authorization', `Bearer ${token}`)

      const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
      if (response.status === 401) {
        onUnauthorized()
        throw new Error('Please log in again.')
      }
      return response
    },
    [onUnauthorized, token],
  )

  return { apiFetch }
}
