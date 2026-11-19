import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react'
import smartBuyLogo from './assets/SmartBuy Logo Transparent.png'
import {
  API_BASE,
  AUTH_STORAGE_KEY,
  DEFAULT_COMPANY_SLUG,
  DEFAULT_SURVEY_QUESTIONS,
  EMPTY_SURVEY_RESPONSE,
  SURVEY_FILTER_FIELDS,
  DEFAULT_SURVEY_FILTER_FIELDS,
} from './constants'
import { useApi } from './hooks/useApi'
import { useCompanyConfig } from './hooks/useCompanyConfig'
import { AdminPage } from './pages/AdminPage'
import { SurveysPage } from './pages/SurveysPage'
import { SystemLogPage } from './pages/SystemLogPage'
import { TrackerPage } from './pages/TrackerPage'
import { BuyBookPage, PublicBookPage, SurveyPage, ViewerPage } from './publicPages'
import type {
  AppUser,
  CompanyInfo,
  EditableField,
  KanbanColumn,
  Page,
  PendingUpdates,
  SheetKey,
  SheetMeta,
  SurveyFilterField,
  SurveyInterest,
  SurveyItem,
  SurveyQuestion,
  SurveyResponseDraft,
  TrackerItem,
} from './types'
import {
  readAdminCompanyFilter,
  resolveWorkingCompanyId,
  writeAdminCompanyFilter,
  type AdminCompanyFilter,
} from './utils/adminCompany'
import { companyPublicApiPath, escapePrintValue, isImageReference, parsePublicRoute, publicPagePath } from './utils'
import './App.css'

function ExternalLinkIcon() {
  return (
    <svg className="nav-external-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24">
      <path d="M14 3h7v7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 14 21 3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M21 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)
  const [token, setToken] = useState(() => window.localStorage.getItem(AUTH_STORAGE_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<AppUser | null>(null)
  const [page, setPage] = useState<Page>('tracker')
  const [authChecking, setAuthChecking] = useState(Boolean(token))
  const [sheets, setSheets] = useState<Record<SheetKey, SheetMeta> | null>(null)
  const [activeSheet, setActiveSheet] = useState<SheetKey>('ad-hoc')
  const [items, setItems] = useState<TrackerItem[]>([])
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [publicItems, setPublicItems] = useState<TrackerItem[]>([])
  const [surveyItems, setSurveyItems] = useState<SurveyItem[]>([])
  const [publicQuery, setPublicQuery] = useState('')
  const [publicStatusFilter, setPublicStatusFilter] = useState('')
  const [surveyQuery, setSurveyQuery] = useState('')
  const [surveyFilterFields, setSurveyFilterFields] = useState<SurveyFilterField[]>(DEFAULT_SURVEY_FILTER_FIELDS)
  const [surveyQuestions, setSurveyQuestions] = useState<SurveyQuestion[]>(DEFAULT_SURVEY_QUESTIONS)
  const [surveyOpen, setSurveyOpen] = useState(true)
  const [surveyClosedMessage, setSurveyClosedMessage] = useState('')
  const [selectedSurveyItemIds, setSelectedSurveyItemIds] = useState<number[]>([])
  const SURVEY_EMAIL_KEY = 'mrl-survey-email'
  const [surveyEmail, setSurveyEmailState] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('email') ?? window.localStorage.getItem(SURVEY_EMAIL_KEY) ?? ''
  })
  const setSurveyEmail = (email: string) => {
    setSurveyEmailState(email)
    if (email) window.localStorage.setItem(SURVEY_EMAIL_KEY, email)
    else window.localStorage.removeItem(SURVEY_EMAIL_KEY)
  }
  const [surveyResponses, setSurveyResponses] = useState<Record<number, SurveyResponseDraft>>({})
  const [kanbanColumns, setKanbanColumns] = useState<KanbanColumn[]>([])
  const [pendingCreates, setPendingCreates] = useState<TrackerItem[]>([])
  const [pendingUpdates, setPendingUpdates] = useState<PendingUpdates>({})
  const [pendingDeletes, setPendingDeletes] = useState<number[]>([])
  const [refreshItemsToken, setRefreshItemsToken] = useState(0)
  const [loading, setLoading] = useState(true)
  const [publicLoading, setPublicLoading] = useState(false)
  const [surveyLoading, setSurveyLoading] = useState(false)
  const [isSubmittingChanges, setIsSubmittingChanges] = useState(false)
  const [isSubmittingSurvey, setIsSubmittingSurvey] = useState(false)
  const [uploadingVisualReference, setUploadingVisualReference] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [publicError] = useState('')
  const [surveyError, setSurveyError] = useState('')
  const [authError, setAuthError] = useState('')
  const [surveyMessage, setSurveyMessage] = useState('')
  const [loginForm, setLoginForm] = useState({ email: '', password: '' })
  const [, setCurrentTablePage] = useState(1)
  const [adminCompanies, setAdminCompanies] = useState<CompanyInfo[]>([])
  const [adminCompanyFilter, setAdminCompanyFilter] = useState<AdminCompanyFilter>(() =>
    readAdminCompanyFilter('all'),
  )

  const publicRoute = useMemo(() => {
    const route = parsePublicRoute(currentPath)
    // One legacy embed opens a page from the root with ?surface= instead of a
    // path: /?surface=survey and /?surface=buy-book. The path decides first.
    if (route.page === null) {
      const surface = new URLSearchParams(window.location.search).get('surface')
      if (surface === 'survey') return { ...route, page: 'survey' as const }
      if (surface === 'buy-book' || surface === 'buybook') return { ...route, page: 'buy-book' as const }
    }
    return route
  }, [currentPath])
  const { companySlug, page: publicPage, isKanban: isPublicKanbanPath } = publicRoute
  const isPublicViewerPath = publicPage === 'viewer'
  const isPublicBookPath = publicPage === 'book'
  const isPublicBuyBookPath = publicPage === 'buy-book'
  const isPublicSurveyPath = publicPage === 'survey'
  const isPublicSurface = isPublicViewerPath || isPublicSurveyPath || isPublicBuyBookPath

  const companyConfig = useCompanyConfig(
    isPublicSurface || currentUser?.role === 'admin' ? null : currentUser?.company,
    isPublicSurface ? companySlug : null,
  )

  useEffect(() => {
    function handlePop() { setCurrentPath(window.location.pathname) }
    window.addEventListener('popstate', handlePop)
    return () => window.removeEventListener('popstate', handlePop)
  }, [])

  const handleLogout = useCallback(() => {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
    setToken('')
    setCurrentUser(null)
    setSheets(null)
    setItems([])
    setPendingCreates([])
    setPendingUpdates({})
    setPendingDeletes([])
    setPage('tracker')
  }, [])

  const { apiFetch } = useApi(token, handleLogout)

  // --- Auth ---
  useEffect(() => {
    if (!token) return
    setAuthChecking(true)
    fetch(`${API_BASE}/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => { if (!res.ok) throw new Error('expired'); return res.json() })
      .then((data) => setCurrentUser(data.user))
      .catch(() => handleLogout())
      .finally(() => setAuthChecking(false))
  }, [handleLogout, token])

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAuthError('')
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(loginForm),
    })
    if (!res.ok) { setAuthError('Invalid email or password.'); return }
    const data = await res.json()
    window.localStorage.setItem(AUTH_STORAGE_KEY, data.token)
    setToken(data.token)
    setCurrentUser(data.user)
    setPage('tracker')
  }

  // --- Sheets ---
  const loadSheets = useCallback(async () => {
    const params = new URLSearchParams()
    if (currentUser?.role === 'admin' && typeof adminCompanyFilter === 'number') {
      params.set('company_id', String(adminCompanyFilter))
    }
    const res = await apiFetch(`/sheets${params.toString() ? `?${params}` : ''}`)
    if (!res.ok) return
    const data = await res.json()
    setSheets(data.sheets)
  }, [adminCompanyFilter, apiFetch, currentUser?.role])

  useEffect(() => {
    if (!currentUser) return
    loadSheets()
  }, [currentUser, loadSheets])

  // --- Items ---
  useEffect(() => {
    if (!currentUser || page !== 'tracker') return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    const params = new URLSearchParams({ sheet: activeSheet })
    if (query.trim()) params.set('q', query.trim())
    if (statusFilter) params.set('status', statusFilter)
    if (currentUser.role === 'admin' && typeof adminCompanyFilter === 'number') {
      params.set('company_id', String(adminCompanyFilter))
    }
    apiFetch(`/items?${params}`, { signal: controller.signal })
      .then((res) => { if (!res.ok) throw new Error('Unable to load tracker rows.'); return res.json() })
      .then((data) => { setItems(data.items); setLoading(false) })
      .catch((err) => { if (err.name !== 'AbortError') { setError(err.message); setLoading(false) } })
    return () => controller.abort()
  }, [activeSheet, adminCompanyFilter, apiFetch, currentUser, page, query, refreshItemsToken, statusFilter])

  // --- Public items ---
  useEffect(() => {
    if (!isPublicViewerPath) return
    const controller = new AbortController()
    setPublicLoading(true)
    const params = new URLSearchParams()
    if (publicQuery.trim()) params.set('q', publicQuery.trim())
    fetch(`${API_BASE}${companyPublicApiPath(companySlug, 'items')}?${params}`, { signal: controller.signal })
      .then((res) => res.ok ? res.json() : Promise.reject(res.status))
      .then((data) => { setPublicItems(Array.isArray(data.items) ? data.items : []); setPublicLoading(false) })
      .catch(() => { setPublicItems([]); setPublicLoading(false) })
    return () => controller.abort()
  }, [companySlug, isPublicViewerPath, publicQuery])

  // --- Survey / buy-book items ---
  // The survey link may carry its period token (?t=). A token is a credential
  // and always wins on the server; without one the legacy open link answers.
  // The buy book reads the same endpoint with surface=buy-book, and a ?k=
  // some site embedded is simply not read (the API ignores it too).
  useEffect(() => {
    if (!isPublicSurveyPath && !isPublicBuyBookPath) return
    const controller = new AbortController()
    setSurveyLoading(true)
    const token = new URLSearchParams(window.location.search).get('t') ?? ''
    const params = new URLSearchParams({ surface: isPublicSurveyPath ? 'survey' : 'buy-book' })
    if (isPublicSurveyPath && token) params.set('t', token)
    if (surveyQuery.trim()) params.set('q', surveyQuery.trim())
    fetch(`${API_BASE}${companyPublicApiPath(companySlug, 'survey-items')}?${params}`, { signal: controller.signal })
      .then((res) => {
        if (res.status === 401 || res.status === 404) {
          setSurveyItems([])
          setSurveyQuestions(DEFAULT_SURVEY_QUESTIONS)
          setSurveyOpen(false)
          setSurveyClosedMessage(
            isPublicBuyBookPath
              ? 'This buy book is not available right now.'
              : 'This survey link is not valid or has been replaced. Ask for a current link.',
          )
          setSurveyLoading(false)
          return null
        }
        if (!res.ok) throw new Error('survey')
        return res.json()
      })
      .then((data) => {
        if (!data) return
        setSurveyItems(Array.isArray(data.items) ? data.items : [])
        if (Array.isArray(data.filter_fields)) {
          const allowed = new Set(SURVEY_FILTER_FIELDS.map((f) => f.field))
          setSurveyFilterFields(data.filter_fields.filter((f: string) => allowed.has(f as SurveyFilterField)))
        }
        if (isPublicSurveyPath) {
          setSurveyQuestions(
            Array.isArray(data.questions) && data.questions.length > 0
              ? data.questions
              : DEFAULT_SURVEY_QUESTIONS,
          )
          setSurveyOpen(data.survey_open !== false)
          setSurveyClosedMessage(
            typeof data.message === 'string' && data.message
              ? data.message
              : 'No survey is open right now.',
          )
        } else {
          setSurveyQuestions(DEFAULT_SURVEY_QUESTIONS)
          setSurveyOpen(true)
          setSurveyClosedMessage('')
        }
        setSurveyLoading(false)
      })
      .catch(() => setSurveyLoading(false))
    return () => controller.abort()
  }, [companySlug, isPublicBuyBookPath, isPublicSurveyPath, surveyQuery])

  // --- Kanban (public) ---
  useEffect(() => {
    if (!isPublicViewerPath) return
    fetch(`${API_BASE}${companyPublicApiPath(companySlug, 'kanban-columns')}`)
      .then((res) => res.json()).then((data) => setKanbanColumns(data.columns)).catch(() => {})
  }, [companySlug, isPublicViewerPath])

  // --- Tracker mutations ---
  function updateDraft(itemId: number, field: EditableField, value: string) {
    setItems((c) => c.map((item) => item.id === itemId ? { ...item, [field]: value } : item))
    if (itemId < 0) {
      setPendingCreates((c) => c.map((item) => item.id === itemId ? { ...item, [field]: value } : item))
      return
    }
    setPendingUpdates((c) => ({ ...c, [itemId]: { ...(c[itemId] ?? {}), [field]: value } }))
  }

  async function submitChanges() {
    const pendingUpdateCount = Object.keys(pendingUpdates).length
    if (pendingCreates.length + pendingUpdateCount + pendingDeletes.length === 0) return
    if (currentUser?.role === 'admin' && adminCompanyFilter === 'all' && pendingCreates.length > 0) {
      setError('Select a company dataset before adding tracker rows.')
      return
    }
    setIsSubmittingChanges(true)
    setError('')
    try {
      const params = new URLSearchParams()
      if (currentUser?.role === 'admin' && typeof adminCompanyFilter === 'number') {
        params.set('company_id', String(adminCompanyFilter))
      }
      const res = await apiFetch(`/items/changes${params.toString() ? `?${params}` : ''}`, {
        method: 'POST',
        body: JSON.stringify({
          creates: pendingCreates.map((item) => ({
            sheet: item.sheet, date_or_buy: item.date_or_buy, current_status: item.current_status,
            visual_reference: item.visual_reference, brand: item.brand, program_name: item.program_name,
            item_name: item.item_name, qty: item.qty, important_notes: item.important_notes,
            mrl_order_number: item.mrl_order_number, estimated_ship_date: item.estimated_ship_date,
            estimated_ihd: item.estimated_ihd, tracking: item.tracking,
          })),
          updates: Object.entries(pendingUpdates).map(([id, changes]) => ({ id: Number(id), changes })),
          deletes: pendingDeletes,
        }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail || 'Unable to submit changes.')
      }
      setPendingCreates([])
      setPendingUpdates({})
      setPendingDeletes([])
      setRefreshItemsToken((t) => t + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to submit changes.')
    } finally {
      setIsSubmittingChanges(false)
    }
  }

  // --- Survey ---
  function getSurveyResponse(itemId: number) {
    return surveyResponses[itemId] ?? EMPTY_SURVEY_RESPONSE
  }

  function updateSurveyResponse(itemId: number, updates: Partial<SurveyResponseDraft>) {
    setSurveyResponses((c) => ({ ...c, [itemId]: { ...(c[itemId] ?? EMPTY_SURVEY_RESPONSE), ...updates } }))
  }

  function toggleSurveyItem(itemId: number) {
    setSelectedSurveyItemIds((c) => c.includes(itemId) ? c.filter((id) => id !== itemId) : [...c, itemId])
  }

  function bulkUpdateSurveyInterest(interest: Exclude<SurveyInterest, ''>) {
    if (!selectedSurveyItemIds.length) return
    const interestQuestion =
      surveyQuestions.find((question) => question.type === 'yes_no' && question.id === 'interest') ??
      surveyQuestions.find((question) => question.type === 'yes_no')
    if (!interestQuestion) return
    const yes = interestQuestion.options[0] || 'Yes'
    const no = interestQuestion.options[1] || 'No'
    const answer = interest === 'Interested' ? yes : no
    setSurveyResponses((c) => {
      const next = { ...c }
      selectedSurveyItemIds.forEach((id) => {
        const current = next[id] ?? EMPTY_SURVEY_RESPONSE
        next[id] = {
          ...current,
          interest,
          answers: { ...current.answers, [interestQuestion.id]: answer },
        }
      })
      return next
    })
  }

  async function submitSurveyResponse() {
    const selected = surveyItems.filter((item) => selectedSurveyItemIds.includes(item.id))
    if (!selected.length) return
    if (!surveyEmail.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(surveyEmail.trim())) {
      setSurveyError('Please enter a valid email address before submitting.')
      return
    }
    const questions = surveyQuestions.length > 0 ? surveyQuestions : DEFAULT_SURVEY_QUESTIONS
    for (const item of selected) {
      const draft = getSurveyResponse(item.id)
      for (const question of questions) {
        if (!question.required) continue
        const value = draft.answers[question.id]
        if (value === undefined || value === null || String(value).trim() === '') {
          setSurveyError(`Please answer “${question.label}” for each selected item.`)
          return
        }
      }
    }
    setIsSubmittingSurvey(true); setSurveyError(''); setSurveyMessage('')
    try {
      const surveyToken = new URLSearchParams(window.location.search).get('t') ?? ''
      for (const item of selected) {
        const draft = getSurveyResponse(item.id)
        const submitUrl = `${API_BASE}${companyPublicApiPath(companySlug, 'survey-responses')}` +
          (surveyToken ? `?t=${encodeURIComponent(surveyToken)}` : '')
        const res = await fetch(submitUrl, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            survey_item_id: item.id,
            email: surveyEmail.trim().toLowerCase(),
            answers: draft.answers,
            feedback: draft.notes,
          }),
        })
        if (!res.ok) {
          const detail = await res.json().catch(() => null)
          throw new Error(detail?.detail || 'Unable to submit survey response.')
        }
      }
      setSurveyEmail(surveyEmail.trim().toLowerCase())
      setSurveyMessage(selected.length === 1
        ? `Thanks ${surveyEmail}. Your response for ${selected[0].item_name} was submitted.`
        : `Thanks ${surveyEmail}. Your response was submitted for ${selected.length} items.`)
      setSelectedSurveyItemIds([])
    } catch (err) {
      setSurveyError(err instanceof Error ? err.message : 'Unable to submit.')
    } finally {
      setIsSubmittingSurvey(false)
    }
  }

  // --- Buy book print ---
  function printSurveyBuyBook() {
    const selected = surveyItems.filter((item) => selectedSurveyItemIds.includes(item.id))
    if (!selected.length) return
    const printFrame = document.createElement('iframe')
    printFrame.title = 'SmartBuy Survey Buy Book'
    Object.assign(printFrame.style, { position: 'fixed', right: '0', bottom: '0', width: '0', height: '0', border: '0' })
    document.body.appendChild(printFrame)
    const pw = printFrame.contentWindow
    const pd = pw?.document
    if (!pw || !pd) { printFrame.remove(); return }

    const pages: typeof selected[] = []
    let currentPage: typeof selected = []
    selected.forEach((item, index) => {
      if (item.page_break_before && index > 0 && currentPage.length > 0) {
        pages.push(currentPage)
        currentPage = []
      }
      currentPage.push(item)
    })
    if (currentPage.length) pages.push(currentPage)

    const pagesMarkup = pages.map((pageItems, pageIndex) => {
      // Print the whole layer path a page belongs to, the same headings the
      // on-screen book draws: first layer as the section, deeper layers below.
      const first = pageItems[0]
      const headers = first?.group_headers?.length
        ? first.group_headers
        : first?.group_label
          ? [{ field: first.group_field ?? 'brand', label: first.group_label, level: 0 }]
          : []
      const facetName = (field: string) =>
        field === 'key_program' ? 'Program' : field === 'product_type' ? 'Type' : field === 'channel' ? 'Channel' : 'Brand'
      const groupHeading = headers.length
        ? `<header class="buy-book-group">${headers
            .map((header, depth) => {
              const level = header.level ?? depth
              const facet = escapePrintValue(facetName(header.field))
              const label = escapePrintValue(header.label)
              return level === 0
                ? `<p>${facet}</p><h1>${label}</h1>`
                : `<p class="buy-book-group-sub"><span>${facet}</span>${label}</p>`
            })
            .join('')}</header>`
        : ''
      const itemMarkup = pageItems.map((item, i) => {
        const img = isImageReference(item.image_url)
          ? `<img src="${escapePrintValue(item.image_url)}" alt="" />`
          : `<div class="image-placeholder">${escapePrintValue(item.brand.slice(0, 2).toUpperCase() || 'SB')}</div>`
        return `<article class="buy-book-item"><div class="item-image">${img}</div><div class="item-copy"><dl>
          <div><dt>Name:</dt><dd>${escapePrintValue(item.item_name || `Item ${i + 1}`)}</dd></div>
          <div><dt>Description:</dt><dd>${escapePrintValue(item.item_description || 'No description.')}</dd></div>
          <div><dt>UOM:</dt><dd>${escapePrintValue(item.uom || '-')}</dd></div>
          <div><dt>Price:</dt><dd>${escapePrintValue(item.price || '-')}</dd></div>
        </dl></div></article>`
      }).join('')
      return `<section class="buy-book-page${pageIndex > 0 ? ' buy-book-page-break' : ''}">${groupHeading}<div class="buy-book-grid">${itemMarkup}</div></section>`
    }).join('')

    pd.open()
    pd.write(`<!doctype html><html><head><title>SmartBuy Buy Book</title><style>
      *{box-sizing:border-box}body{margin:0;padding:30px 42px;font-family:Arial,sans-serif}
      .buy-book-page{padding-bottom:12px}
      .buy-book-page-break{break-before:page;page-break-before:always}
      .buy-book-group{margin:0 0 18px} .buy-book-group p{margin:0;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#666}
      .buy-book-group h1{margin:4px 0 0;font-size:22px}
      .buy-book-group-sub{margin:4px 0 0;font-size:13px;color:#33404f;font-weight:700}
      .buy-book-group-sub span{margin-right:6px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#666}
      .buy-book-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:34px 44px}
      .buy-book-item{break-inside:avoid;display:grid;grid-template-columns:116px 1fr;gap:16px;min-height:126px}
      .item-image{min-height:104px;display:grid;place-items:center}
      .item-image img{width:100%;height:104px;object-fit:contain}
      .image-placeholder{width:70px;height:70px;display:grid;place-items:center;border-radius:999px;background:#f4f4f4;color:#FD4338;font-size:20px;font-weight:800}
      dl{margin:0;display:grid;gap:3px}dt{display:inline;margin-right:4px;font-size:11px;font-weight:800}
      dd{display:inline;margin:0;font-size:11px}@media print{@page{size:landscape;margin:.35in}}
    </style></head><body>${pagesMarkup}</body></html>`)
    pd.close()
    setTimeout(() => { pw.focus(); pw.print(); setTimeout(() => printFrame.remove(), 1000) }, 250)
  }

  // --- Navigation helpers ---
  function goToPath(path: string) { window.history.pushState({}, '', path); setCurrentPath(path) }

  // --- Derived values ---
  const isAdmin = currentUser?.role === 'admin'

  const adminWorkingCompanyId = useMemo(
    () =>
      resolveWorkingCompanyId(
        adminCompanyFilter,
        adminCompanies.map((company) => company.id),
        currentUser?.company_id ?? null,
      ),
    [adminCompanyFilter, adminCompanies, currentUser?.company_id],
  )

  const adminWorkingCompany = useMemo(() => {
    if (adminCompanyFilter === 'all') return null
    return adminCompanies.find((company) => company.id === adminCompanyFilter) ?? null
  }, [adminCompanyFilter, adminCompanies])

  const adminDatasetLabel = adminCompanyFilter === 'all'
    ? 'all companies'
    : (adminWorkingCompany?.company_name || adminWorkingCompany?.name || 'selected company')

  useEffect(() => {
    if (!isAdmin || !token) {
      setAdminCompanies([])
      return
    }
    apiFetch('/admin/companies')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.companies) setAdminCompanies(data.companies)
      })
      .catch(() => setAdminCompanies([]))
  }, [apiFetch, isAdmin, token])

  function handleAdminCompanyFilterChange(value: AdminCompanyFilter) {
    setAdminCompanyFilter(value)
    writeAdminCompanyFilter(value)
    setPendingCreates([])
    setPendingUpdates({})
    setPendingDeletes([])
    setItems([])
    setError('')
    setStatusFilter('')
    setRefreshItemsToken((tokenValue) => tokenValue + 1)
  }

  function openPublicPath(path: string) {
    const slug =
      (isAdmin && adminWorkingCompany?.company_slug)
      || (isAdmin && adminCompanies[0]?.company_slug)
      || currentUser?.company?.company_slug
      || companyConfig.company_slug
      || DEFAULT_COMPANY_SLUG
    window.open(`/${slug}${path}`, '_blank', 'noopener')
  }

  const publicStatusOptions = useMemo(() =>
    [...new Set(publicItems.map((item) => item.current_status).filter(Boolean))].sort(), [publicItems])

  const publicGridItems = useMemo(() =>
    publicStatusFilter ? publicItems.filter((item) => item.current_status === publicStatusFilter) : publicItems,
    [publicItems, publicStatusFilter])

  const visibleKanbanColumns = useMemo(() =>
    [...kanbanColumns].filter((c) => c.is_visible).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id),
    [kanbanColumns])

  const publicKanbanColumns = useMemo(() => {
    const cols = visibleKanbanColumns.map((column) => ({
      column,
      items: publicItems.filter((item) => column.statuses.includes(item.current_status)),
    }))
    const matched = new Set(visibleKanbanColumns.flatMap((c) => c.statuses))
    const unmatched = publicItems.filter((item) => !matched.has(item.current_status))
    if (unmatched.length) {
      cols.push({ column: { id: -1, title: 'Other', statuses: [], sort_order: 999, is_visible: true, created_at: '', updated_at: '' }, items: unmatched })
    }
    return cols
  }, [publicItems, visibleKanbanColumns])

  async function downloadSurveyResponses(periodId?: number | null) {
    try {
      const params = new URLSearchParams()
      if (periodId != null) params.set('period_id', String(periodId))
      if (isAdmin && adminWorkingCompanyId != null) params.set('company_id', String(adminWorkingCompanyId))
      const res = await apiFetch(
        `/admin/survey-responses/export${params.toString() ? `?${params}` : ''}`,
      )
      if (!res.ok) { setError('Unable to download survey responses.'); return }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `survey-responses-${new Date().toISOString().slice(0, 10)}.xlsx`
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to download survey responses.')
    }
  }

  // ---- Public pages ----
  const publicBrandProps = {
    companyName: companyConfig.company_name,
    companyLogoUrl: companyConfig.logo_url,
    companyBannerUrl: companyConfig.banner_url,
  }

  if (isPublicBookPath) {
    return <PublicBookPage token={publicRoute.bookToken ?? ''} />
  }

  if (isPublicBuyBookPath) {
    return <BuyBookPage {...publicBrandProps} surveyItems={surveyItems} surveyFilterFields={surveyFilterFields}
      surveyLoading={surveyLoading} surveyError={surveyError}
      surveyQuery={surveyQuery} selectedSurveyItemIds={selectedSurveyItemIds}
      setSurveyQuery={setSurveyQuery} toggleSurveyItem={toggleSurveyItem} printSurveyBuyBook={printSurveyBuyBook} />
  }

  if (isPublicSurveyPath) {
    return <SurveyPage {...publicBrandProps} surveyItems={surveyItems} surveyFilterFields={surveyFilterFields}
      surveyQuestions={surveyQuestions}
      surveyLoading={surveyLoading} surveyError={surveyError} surveyMessage={surveyMessage}
      surveyQuery={surveyQuery} selectedSurveyItemIds={selectedSurveyItemIds}
      isSubmittingSurvey={isSubmittingSurvey} setSurveyQuery={setSurveyQuery}
      surveyEmail={surveyEmail} setSurveyEmail={setSurveyEmail}
      surveyOpen={surveyOpen} surveyClosedMessage={surveyClosedMessage}
      toggleSurveyItem={toggleSurveyItem} submitSurveyResponse={submitSurveyResponse}
      bulkUpdateSurveyInterest={bulkUpdateSurveyInterest} getSurveyResponse={getSurveyResponse}
      updateSurveyResponse={updateSurveyResponse} />
  }
  if (isPublicViewerPath) {
    return <ViewerPage {...publicBrandProps} isPublicKanbanPath={isPublicKanbanPath}
      gridPath={publicPagePath(publicRoute, '/viewer')} kanbanPath={publicPagePath(publicRoute, '/viewer/kanban')}
      publicQuery={publicQuery} publicStatusFilter={publicStatusFilter} publicStatusOptions={publicStatusOptions}
      publicLoading={publicLoading} publicError={publicError} publicItems={publicItems}
      publicGridItems={publicGridItems} publicKanbanColumns={publicKanbanColumns}
      setPublicQuery={setPublicQuery} setPublicStatusFilter={setPublicStatusFilter} goToPath={goToPath} />
  }

  if (authChecking) {
    return <main className="auth-shell"><div className="auth-card">Checking your session...</div></main>
  }

  if (!currentUser) {
    return (
      <main className="auth-shell">
        <form className="auth-card" onSubmit={login}>
          <img className="auth-logo" src={smartBuyLogo} alt="SmartBuy" />
          <p className="eyebrow">Project Tracker</p>
          <h1>Sign In</h1>
          <p className="subtitle">Use your invited account to access the tracker.</p>
          <label>Email
            <input autoComplete="email" value={loginForm.email}
              onChange={(e) => setLoginForm((f) => ({ ...f, email: e.target.value }))} />
          </label>
          <label>Password
            <input autoComplete="current-password" type="password" value={loginForm.password}
              onChange={(e) => setLoginForm((f) => ({ ...f, password: e.target.value }))} />
          </label>
          {authError && <div className="error">{authError}</div>}
          <button className="primary-action" type="submit">Log In</button>
          <p className="subtitle" style={{ marginTop: '1rem', fontSize: '0.85rem' }}>
            Admin: <code>admin@example.com</code> / <code>Admin123</code>
            <br />
            Company editors (password <code>Demo1234</code>): phillips@example.com, demo@example.com, acme@example.com
          </p>
        </form>
      </main>
    )
  }

  return (
    <main className="app-shell">
      <header className="brand-header">
        <div className="brand-topline">
          {isAdmin ? (
            <>
              <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
              <div className="admin-brand-copy">
                <span>SmartBuy</span>
                <small>Powered by FSI</small>
              </div>
            </>
          ) : companyConfig.logo_url ? (
            <img className="company-logo" src={companyConfig.logo_url} alt={companyConfig.company_name} />
          ) : (
            <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
          )}
          {!isAdmin && <span>Analytics On Demand</span>}
        </div>
        <details className="nav-menu">
          <summary>Menu</summary>
          <nav className="app-nav" aria-label="Primary navigation">
            <button className={page === 'tracker' ? 'active' : ''} type="button" onClick={() => setPage('tracker')}>Tracker</button>
            <button className={page === 'surveys' ? 'active' : ''} type="button" onClick={() => setPage('surveys')}>Survey/Buy Book Edit</button>
            {isAdmin && <>
              <button className={page === 'admin-users' ? 'active' : ''} type="button" onClick={() => setPage('admin-users')}>Admin Settings</button>
              <button className={page === 'system-log' ? 'active' : ''} type="button" onClick={() => setPage('system-log')}>System Log</button>
            </>}
            <button className="nav-external" type="button" onClick={() => openPublicPath('/viewer')}>
              Viewer <ExternalLinkIcon />
            </button>
            <button type="button" onClick={handleLogout}>Log Out</button>
          </nav>
        </details>
      </header>

      <section className="client-title-row">
        {isAdmin ? (
          <div className="admin-brand-banner">
            <div className="admin-brand-banner-main">
              <img src={smartBuyLogo} alt="SmartBuy" />
              <div>
                <strong>SmartBuy</strong>
                <span>Powered by FSI</span>
                <em>Viewing {adminDatasetLabel}</em>
              </div>
            </div>
            <label className="admin-brand-company">
              Company dataset
              <select
                value={adminCompanyFilter === 'all' ? 'all' : String(adminCompanyFilter)}
                onChange={(e) => {
                  const value = e.target.value
                  handleAdminCompanyFilterChange(value === 'all' ? 'all' : Number(value))
                }}
              >
                <option value="all">All companies</option>
                {adminCompanies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.company_name || company.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : companyConfig.banner_url ? (
          <img className="tracker-brand-banner" src={companyConfig.banner_url} alt={companyConfig.company_name} />
        ) : (
          <img className="tracker-brand-banner" src="/SmartBuy Analytics - Status Tracker Logo.png" alt="SmartBuy Status Tracker" />
        )}
        <div className="tracker-page-title">
          <p className="eyebrow">
            {page === 'admin-users'
              ? 'Admin Settings'
              : page === 'system-log'
                ? 'System Log'
                : page === 'surveys'
                  ? 'Survey/Buy Book Edit'
                  : 'Project Tracker'}
          </p>
          <h1>
            {page === 'admin-users'
              ? 'Admin Settings'
              : page === 'system-log'
                ? 'System Log'
                : page === 'surveys'
                  ? 'Survey/Buy Book Edit'
                  : 'Tracker Dashboard'}
          </h1>
          <p className="subtitle">Signed in as {currentUser.name || currentUser.email} ({currentUser.role}).</p>
        </div>
      </section>

      {page === 'admin-users' && (
        <AdminPage
          currentUser={currentUser}
          sheets={sheets}
          companies={adminCompanies}
          companyFilter={adminCompanyFilter}
          onCompanyFilterChange={handleAdminCompanyFilterChange}
          apiFetch={apiFetch}
          onSheetStatusChanged={loadSheets}
        />
      )}

      {page === 'surveys' && (
        <SurveysPage
          currentUser={currentUser}
          companies={adminCompanies}
          companyFilter={adminCompanyFilter}
          apiFetch={apiFetch}
          onDownloadSurveyResponses={downloadSurveyResponses}
        />
      )}

      {page === 'system-log' && (
        <SystemLogPage
          companyFilter={adminCompanyFilter}
          apiFetch={apiFetch}
        />
      )}

      {page === 'tracker' && (
        <TrackerPage
          currentUser={currentUser}
          sheets={sheets}
          activeSheet={activeSheet}
          items={items}
          loading={loading}
          error={error}
          query={query}
          statusFilter={statusFilter}
          pendingCreates={pendingCreates}
          pendingUpdates={pendingUpdates}
          pendingDeletes={pendingDeletes}
          isSubmittingChanges={isSubmittingChanges}
          uploadingVisualReference={uploadingVisualReference}
          companyFilter={adminCompanyFilter}
          apiFetch={apiFetch}
          setActiveSheet={setActiveSheet}
          setQuery={setQuery}
          setStatusFilter={setStatusFilter}
          setCurrentTablePage={setCurrentTablePage}
          updateDraft={updateDraft}
          deleteRow={() => {/* handled inside TrackerPage via ConfirmModal */}}
          submitChanges={submitChanges}
          openPublicPath={openPublicPath}
          setUploadingVisualReference={setUploadingVisualReference}
          setError={setError}
          setPendingCreates={setPendingCreates}
          setPendingUpdates={setPendingUpdates}
          setPendingDeletes={setPendingDeletes}
          setItems={setItems}
          setRefreshItemsToken={setRefreshItemsToken}
        />
      )}
    </main>
  )
}

export default App
