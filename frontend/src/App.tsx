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
import { SurveyPage, ViewerPage } from './publicPages'
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
import { companyPublicApiPath, parsePublicRoute, publicPagePath } from './utils'
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

  const publicRoute = useMemo(() => parsePublicRoute(currentPath), [currentPath])
  const { companySlug, page: publicPage, isKanban: isPublicKanbanPath } = publicRoute
  const isPublicViewerPath = publicPage === 'viewer'
  const isPublicSurveyPath = publicPage === 'survey'
  const isPublicSurface = isPublicViewerPath || isPublicSurveyPath

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

  // --- Survey items ---
  // The survey link carries its period token (?t=). It is the only credential
  // the public surface accepts; a link without one gets a clean closed state.
  useEffect(() => {
    if (!isPublicSurveyPath) return
    const controller = new AbortController()
    setSurveyLoading(true)
    const token = new URLSearchParams(window.location.search).get('t') ?? ''
    const params = new URLSearchParams({ surface: 'survey' })
    if (token) params.set('t', token)
    if (surveyQuery.trim()) params.set('q', surveyQuery.trim())
    fetch(`${API_BASE}${companyPublicApiPath(companySlug, 'survey-items')}?${params}`, { signal: controller.signal })
      .then((res) => {
        if (res.status === 401 || res.status === 404) {
          setSurveyItems([])
          setSurveyQuestions(DEFAULT_SURVEY_QUESTIONS)
          setSurveyOpen(false)
          setSurveyClosedMessage('This survey link is not valid or has been replaced. Ask for a current link.')
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
        setSurveyLoading(false)
      })
      .catch(() => setSurveyLoading(false))
    return () => controller.abort()
  }, [companySlug, isPublicSurveyPath, surveyQuery])

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
