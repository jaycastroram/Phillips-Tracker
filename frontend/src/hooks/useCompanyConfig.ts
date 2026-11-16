import { useEffect, useState } from 'react'
import { API_BASE } from '../constants'
import type { CompanyInfo } from '../types'

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

function toConfig(source: CompanyInfo | CompanyConfig | null | undefined): CompanyConfig | null {
  if (!source) return null
  const company_slug = 'company_slug' in source ? source.company_slug : ''
  const company_name = 'company_name' in source ? source.company_name : ''
  if (!company_slug && !company_name) return null
  return {
    company_slug,
    company_name,
    logo_url: source.logo_url || '',
    banner_url: source.banner_url || '',
  }
}

export function useCompanyConfig(
  userCompany?: CompanyInfo | null,
  companySlug?: string | null,
) {
  const [config, setConfig] = useState<CompanyConfig>(() => toConfig(userCompany) || DEFAULT_CONFIG)

  useEffect(() => {
    const slug = companySlug?.trim()
    if (slug) {
      fetch(`${API_BASE}/public/${encodeURIComponent(slug)}/config`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          const next = toConfig(data)
          if (next) setConfig(next)
        })
        .catch(() => {/* keep current config */})
      return
    }

    const fromUser = toConfig(userCompany)
    if (fromUser) {
      setConfig(fromUser)
      return
    }

    fetch(`${API_BASE}/public/config`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && typeof data.company_name === 'string') {
          setConfig(data as CompanyConfig)
        }
      })
      .catch(() => {/* silently fall back to defaults */})
  }, [companySlug, userCompany])

  return config
}
