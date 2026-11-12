import { useEffect, useState } from 'react'
import { API_BASE } from '../constants'

export type CompanyConfig = {
  company_slug: string
  company_name: string
  logo_url: string
  banner_url: string
}

const DEFAULT_CONFIG: CompanyConfig = {
  company_slug: '',
  company_name: 'MRL SmartBuy',
  logo_url: '',
  banner_url: '',
}

export function useCompanyConfig() {
  const [config, setConfig] = useState<CompanyConfig>(DEFAULT_CONFIG)

  useEffect(() => {
    fetch(`${API_BASE}/public/config`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && typeof data.company_name === 'string') {
          setConfig(data as CompanyConfig)
        }
      })
      .catch(() => {/* silently fall back to defaults */})
  }, [])

  return config
}
