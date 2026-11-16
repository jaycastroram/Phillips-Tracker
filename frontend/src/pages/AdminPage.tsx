import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { PromptModal } from '../components/PromptModal'
import { StatusMultiSelect } from '../components/StatusMultiSelect'
import { SHEET_ORDER } from '../constants'
import type {
  AdminSettingsTab,
  AppUser,
  CompanyInfo,
  KanbanColumn,
  Role,
  SheetKey,
  SheetMeta,
  SheetStatus,
  StatusTargetSheet,
} from '../types'
import {
  resolveWorkingCompanyId,
  withCompanyQuery,
  type AdminCompanyFilter,
} from '../utils/adminCompany'

type AdminPageProps = {
  currentUser: AppUser
  sheets: Record<SheetKey, SheetMeta> | null
  companies: CompanyInfo[]
  companyFilter: AdminCompanyFilter
  onCompanyFilterChange: (value: AdminCompanyFilter) => void
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onSheetStatusChanged: () => void
}

export function AdminPage({
  currentUser: _currentUser,
  sheets,
  companies: companiesProp,
  companyFilter,
  onCompanyFilterChange: _onCompanyFilterChange,
  apiFetch,
  onSheetStatusChanged,
}: AdminPageProps) {
  const [tab, setTab] = useState<AdminSettingsTab>('users')
  const [adminMessage, setAdminMessage] = useState('')
  const [kanbanMessage, setKanbanMessage] = useState('')
  const [error, setError] = useState('')

  // --- Companies ---
  const [companies, setCompanies] = useState<CompanyInfo[]>(companiesProp)
  const [companiesLoading, setCompaniesLoading] = useState(false)
  const [scopedSheets, setScopedSheets] = useState<Record<SheetKey, SheetMeta> | null>(sheets)

  // --- Users ---
  const [users, setUsers] = useState<AppUser[]>([])
  const [usersLoading, setUsersLoading] = useState(false)
  const [showNewUserPassword, setShowNewUserPassword] = useState(false)
  const [newUser, setNewUser] = useState({
    email: '',
    name: '',
    password: '',
    role: 'editor' as Role,
    is_active: true,
    company_id: _currentUser.company_id,
  })
  const [resetPasswordUserId, setResetPasswordUserId] = useState<number | null>(null)

  // --- Statuses ---
  const [sheetStatuses, setSheetStatuses] = useState<SheetStatus[]>([])
  const [statusesLoading, setStatusesLoading] = useState(false)
  const [newSheetStatus, setNewSheetStatus] = useState({ sheet: 'ad-hoc' as StatusTargetSheet, status: '' })

  // --- Kanban ---
  const [kanbanColumns, setKanbanColumns] = useState<KanbanColumn[]>([])
  const [kanbanColumnsLoading, setKanbanColumnsLoading] = useState(false)
  const [newKanbanColumn, setNewKanbanColumn] = useState({ title: '', statuses: [] as string[], sort_order: 0, is_visible: true })

  const workingCompanyId = useMemo(
    () =>
      resolveWorkingCompanyId(
        companyFilter,
        companies.map((company) => company.id),
        _currentUser.company_id,
      ),
    [companyFilter, companies, _currentUser.company_id],
  )

  const activeSheets = scopedSheets ?? sheets

  const allStatusOptions = (() => {
    const set = new Set<string>()
    if (activeSheets) SHEET_ORDER.forEach((k) => activeSheets[k]?.statuses.forEach((s) => set.add(s)))
    sheetStatuses.forEach((r) => set.add(r.status))
    return [...set].sort((a, b) => a.localeCompare(b))
  })()

  useEffect(() => {
    setCompanies(companiesProp)
  }, [companiesProp])

  // --- Loaders ---
  const loadCompanies = useCallback(async () => {
    setCompaniesLoading(true)
    try {
      const res = await apiFetch('/admin/companies')
      if (!res.ok) throw new Error('Unable to load companies.')
      const data = await res.json()
      setCompanies(data.companies)
      setNewUser((u) => ({
        ...u,
        company_id: u.company_id ?? data.companies[0]?.id ?? _currentUser.company_id,
      }))
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load companies.') }
    finally { setCompaniesLoading(false) }
  }, [apiFetch, _currentUser.company_id])

  const loadUsers = useCallback(async () => {
    setUsersLoading(true)
    try {
      const path = typeof companyFilter === 'number'
        ? `/admin/users?company_id=${companyFilter}`
        : '/admin/users'
      const res = await apiFetch(path)
      if (!res.ok) throw new Error('Unable to load users.')
      setUsers((await res.json()).users)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load users.') }
    finally { setUsersLoading(false) }
  }, [apiFetch, companyFilter])

  const loadScopedSheets = useCallback(async () => {
    if (!workingCompanyId) {
      setScopedSheets(sheets)
      return
    }
    try {
      const res = await apiFetch(`/sheets?company_id=${workingCompanyId}`)
      if (!res.ok) throw new Error('Unable to load sheet config.')
      setScopedSheets((await res.json()).sheets)
    } catch {
      setScopedSheets(sheets)
    }
  }, [apiFetch, sheets, workingCompanyId])

  const loadSheetStatuses = useCallback(async () => {
    if (!workingCompanyId) return
    setStatusesLoading(true)
    try {
      const res = await apiFetch(withCompanyQuery('/admin/sheet-statuses', workingCompanyId))
      if (!res.ok) throw new Error('Unable to load statuses.')
      setSheetStatuses((await res.json()).statuses)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load statuses.') }
    finally { setStatusesLoading(false) }
  }, [apiFetch, workingCompanyId])

  const loadKanbanColumns = useCallback(async () => {
    if (!workingCompanyId) return
    setKanbanColumnsLoading(true)
    try {
      const res = await apiFetch(withCompanyQuery('/admin/kanban-columns', workingCompanyId))
      if (!res.ok) throw new Error('Unable to load kanban columns.')
      setKanbanColumns((await res.json()).columns)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load kanban columns.') }
    finally { setKanbanColumnsLoading(false) }
  }, [apiFetch, workingCompanyId])

  useEffect(() => {
    loadCompanies()
  }, [loadCompanies])

  useEffect(() => {
    setError(''); setAdminMessage(''); setKanbanMessage('')
    if (tab === 'users') loadUsers()
    else if (tab === 'statuses') { loadScopedSheets(); loadSheetStatuses() }
    else if (tab === 'kanban') { loadScopedSheets(); loadKanbanColumns(); loadSheetStatuses() }
  }, [tab, companyFilter, loadUsers, loadScopedSheets, loadSheetStatuses, loadKanbanColumns])

  useEffect(() => {
    if (typeof companyFilter === 'number') {
      setNewUser((u) => ({ ...u, company_id: companyFilter }))
    }
  }, [companyFilter])

  // --- Company actions ---
  async function updateCompany(companyId: number, updates: Partial<CompanyInfo>) {
    setAdminMessage(''); setError('')
    const res = await apiFetch(`/admin/companies/${companyId}`, { method: 'PATCH', body: JSON.stringify(updates) })
    if (!res.ok) { setError('Unable to update company.'); return }
    const data = await res.json()
    setCompanies((rows) => rows.map((row) => row.id === companyId ? data.company : row))
    setAdminMessage('Company updated.')
  }

  // --- User actions ---
  async function createUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setAdminMessage(''); setError('')
    const res = await apiFetch('/admin/users', { method: 'POST', body: JSON.stringify(newUser) })
    if (!res.ok) { setError(res.status === 409 ? 'A user with that email already exists.' : 'Unable to create user.'); return }
    const data = await res.json()
    setUsers((u) => [data.user, ...u])
    setNewUser({
      email: '',
      name: '',
      password: '',
      role: 'editor',
      is_active: true,
      company_id: newUser.company_id ?? companies[0]?.id ?? _currentUser.company_id,
    })
    setAdminMessage('User created.')
  }

  async function updateUser(userId: number, updates: Partial<AppUser>) {
    setAdminMessage(''); setError('')
    const res = await apiFetch(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify(updates) })
    if (!res.ok) { setError('Unable to update user.'); return }
    const data = await res.json()
    setUsers((u) => u.map((row) => row.id === userId ? data.user : row))
    setAdminMessage('User updated.')
  }

  async function handleResetPassword(password: string) {
    if (!resetPasswordUserId) return
    const res = await apiFetch(`/admin/users/${resetPasswordUserId}/reset-password`, {
      method: 'POST', body: JSON.stringify({ password })
    })
    if (!res.ok) { setError('Unable to reset password.'); setResetPasswordUserId(null); return }
    setAdminMessage('Password reset.')
    setResetPasswordUserId(null)
  }

  // --- Status actions ---
  async function createSheetStatus(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setAdminMessage(''); setError('')
    if (!workingCompanyId) { setError('Select a company first.'); return }
    const targetSheets = newSheetStatus.sheet === 'all' ? SHEET_ORDER : [newSheetStatus.sheet as SheetKey]
    const created: SheetStatus[] = [], skipped: string[] = []
    for (const sheetKey of targetSheets) {
      const res = await apiFetch(withCompanyQuery('/admin/sheet-statuses', workingCompanyId), {
        method: 'POST', body: JSON.stringify({ sheet: sheetKey, status: newSheetStatus.status })
      })
      if (res.status === 409) { skipped.push(activeSheets?.[sheetKey]?.label ?? sheetKey); continue }
      if (!res.ok) { setError('Unable to create status.'); return }
      created.push((await res.json()).status)
    }
    if (created.length) setSheetStatuses((s) => [...s, ...created])
    setNewSheetStatus((s) => ({ ...s, status: '' }))
    onSheetStatusChanged()
    const msg = created.length === 1 ? 'Status created.' : `Status created for ${created.length} sheets.`
    setAdminMessage(skipped.length ? `${msg} Already existed for: ${skipped.join(', ')}.` : msg)
  }

  async function deleteSheetStatus(row: SheetStatus) {
    setAdminMessage(''); setError('')
    if (!workingCompanyId) { setError('Select a company first.'); return }
    const res = await apiFetch(withCompanyQuery(`/admin/sheet-statuses/${row.id}`, workingCompanyId), { method: 'DELETE' })
    if (!res.ok) { setError('Unable to remove status.'); return }
    setSheetStatuses((s) => s.filter((r) => r.id !== row.id))
    onSheetStatusChanged()
    setAdminMessage('Status removed.')
  }

  // --- Kanban actions ---
  async function createKanbanColumn(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setKanbanMessage(''); setError('')
    if (!workingCompanyId) { setError('Select a company first.'); return }
    const res = await apiFetch(withCompanyQuery('/admin/kanban-columns', workingCompanyId), {
      method: 'POST',
      body: JSON.stringify(newKanbanColumn),
    })
    if (!res.ok) { setError('Unable to create kanban column.'); return }
    const data = await res.json()
    setKanbanColumns((c) => [...c, data.column])
    setNewKanbanColumn({ title: '', statuses: [], sort_order: 0, is_visible: true })
    setKanbanMessage('Kanban column created.')
  }

  async function updateKanbanColumn(columnId: number, updates: Partial<KanbanColumn>) {
    setKanbanMessage(''); setError('')
    if (!workingCompanyId) { setError('Select a company first.'); return }
    const res = await apiFetch(withCompanyQuery(`/admin/kanban-columns/${columnId}`, workingCompanyId), {
      method: 'PATCH',
      body: JSON.stringify(updates),
    })
    if (!res.ok) { setError('Unable to update kanban column.'); return }
    const data = await res.json()
    setKanbanColumns((c) => c.map((col) => col.id === columnId ? data.column : col))
    setKanbanMessage('Kanban column updated.')
  }

  async function deleteKanbanColumn(columnId: number) {
    setKanbanMessage(''); setError('')
    if (!workingCompanyId) { setError('Select a company first.'); return }
    const res = await apiFetch(withCompanyQuery(`/admin/kanban-columns/${columnId}`, workingCompanyId), { method: 'DELETE' })
    if (!res.ok) { setError('Unable to delete kanban column.'); return }
    setKanbanColumns((c) => c.filter((col) => col.id !== columnId))
    setKanbanMessage('Kanban column deleted.')
  }

  return (
    <section className="admin-page">
      {error && <div className="error">{error}</div>}
      {adminMessage && <div className="success">{adminMessage}</div>}
      {kanbanMessage && <div className="success">{kanbanMessage}</div>}

      <div className="admin-tabs" role="tablist" aria-label="Admin settings sections">
        {(['users', 'companies', 'statuses', 'kanban'] as AdminSettingsTab[]).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} type="button" onClick={() => setTab(t)}>
            {t === 'users' ? 'Manage Users'
              : t === 'companies' ? 'Companies'
              : t === 'statuses' ? 'Manage Statuses'
              : 'Kanban Settings'}
          </button>
        ))}
      </div>

      {tab === 'companies' && (
        <section className="table-card admin-users-card">
          <div className="table-tools">
            <span>{companiesLoading ? 'Loading companies...' : `${companies.length} companies`}</span>
            <button type="button" onClick={loadCompanies}>Refresh</button>
          </div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Name</th><th>Slug</th><th>Logo URL</th><th>Banner URL</th><th>Active</th></tr></thead>
              <tbody>
                {companies.map((company) => (
                  <tr key={company.id}>
                    <td><input value={company.company_name || company.name || ''}
                      onBlur={(e) => updateCompany(company.id, { name: e.target.value })}
                      onChange={(e) => setCompanies((rows) => rows.map((r) => r.id === company.id
                        ? { ...r, company_name: e.target.value, name: e.target.value } : r))} /></td>
                    <td>{company.company_slug || company.slug}</td>
                    <td><input value={company.logo_url || ''}
                      onBlur={(e) => updateCompany(company.id, { logo_url: e.target.value })}
                      onChange={(e) => setCompanies((rows) => rows.map((r) => r.id === company.id
                        ? { ...r, logo_url: e.target.value } : r))} /></td>
                    <td><input value={company.banner_url || ''}
                      onBlur={(e) => updateCompany(company.id, { banner_url: e.target.value })}
                      onChange={(e) => setCompanies((rows) => rows.map((r) => r.id === company.id
                        ? { ...r, banner_url: e.target.value } : r))} /></td>
                    <td className="actions"><input checked={company.is_active !== false} type="checkbox"
                      onChange={(e) => updateCompany(company.id, { is_active: e.target.checked })} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'users' && (
        <>
          <form className="admin-create-card" onSubmit={createUser}>
            <h2>Create User</h2>
            <label>Name<input value={newUser.name} placeholder="Jane Smith" onChange={(e) => setNewUser((u) => ({ ...u, name: e.target.value }))} /></label>
            <label>Email<input required type="email" value={newUser.email} placeholder="jane@example.com" onChange={(e) => setNewUser((u) => ({ ...u, email: e.target.value }))} /></label>
            <label>Temporary Password
              <div className="password-field">
                <input required minLength={6} type={showNewUserPassword ? 'text' : 'password'} value={newUser.password}
                  onChange={(e) => setNewUser((u) => ({ ...u, password: e.target.value }))} />
                <button aria-label={showNewUserPassword ? 'Hide' : 'Show'} className="password-toggle" type="button"
                  onClick={() => setShowNewUserPassword((v) => !v)}>
                  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
                    <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
            </label>
            <label>Company
              <select
                required
                value={newUser.company_id ?? ''}
                onChange={(e) => setNewUser((u) => ({ ...u, company_id: Number(e.target.value) }))}
              >
                {companies.map((company) => (
                  <option key={company.id} value={company.id}>{company.company_name || company.name}</option>
                ))}
              </select>
            </label>
            <label>Role
              <select value={newUser.role} onChange={(e) => setNewUser((u) => ({ ...u, role: e.target.value as Role }))}>
                <option value="editor">Editor</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <label className="checkbox-label"><input checked={newUser.is_active} type="checkbox" onChange={(e) => setNewUser((u) => ({ ...u, is_active: e.target.checked }))} />Active</label>
            <button className="primary-action" type="submit">Create User</button>
          </form>

          <section className="table-card admin-users-card">
            <div className="table-tools">
              <span>{usersLoading ? 'Loading users...' : `${users.length} users`}</span>
              <button type="button" onClick={loadUsers}>Refresh</button>
            </div>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Active</th><th>Actions</th></tr></thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id}>
                      <td><input value={user.name}
                        onBlur={(e) => updateUser(user.id, { name: e.target.value })}
                        onChange={(e) => setUsers((u) => u.map((r) => r.id === user.id ? { ...r, name: e.target.value } : r))} /></td>
                      <td><input type="email" value={user.email}
                        onBlur={(e) => updateUser(user.id, { email: e.target.value })}
                        onChange={(e) => setUsers((u) => u.map((r) => r.id === user.id ? { ...r, email: e.target.value } : r))} /></td>
                      <td>
                        <select
                          value={user.company_id ?? ''}
                          onChange={(e) => updateUser(user.id, { company_id: Number(e.target.value) })}
                        >
                          {companies.map((company) => (
                            <option key={company.id} value={company.id}>{company.company_name || company.name}</option>
                          ))}
                        </select>
                      </td>
                      <td><select value={user.role} onChange={(e) => updateUser(user.id, { role: e.target.value as Role })}>
                        <option value="editor">Editor</option><option value="admin">Admin</option>
                      </select></td>
                      <td className="actions"><input checked={user.is_active} type="checkbox" onChange={(e) => updateUser(user.id, { is_active: e.target.checked })} /></td>
                      <td className="actions"><button type="button" onClick={() => setResetPasswordUserId(user.id)}>Reset Password</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {tab === 'statuses' && (
        <section className="admin-create-card status-settings-card">
          <form onSubmit={createSheetStatus}>
            <h2>Manage Statuses</h2>
            <label>Sheet
              <select value={newSheetStatus.sheet} onChange={(e) => setNewSheetStatus((s) => ({ ...s, sheet: e.target.value as StatusTargetSheet }))}>
                <option value="all">All Sheets</option>
                {SHEET_ORDER.map((k) => <option key={k} value={k}>{activeSheets?.[k]?.label ?? k}</option>)}
              </select>
            </label>
            <label>New Status<input required value={newSheetStatus.status} placeholder="New status label"
              onChange={(e) => setNewSheetStatus((s) => ({ ...s, status: e.target.value }))} /></label>
            <button className="primary-action" type="submit">Add Status</button>
          </form>
          <div className="status-settings-list">
            <div className="table-tools">
              <span>{statusesLoading ? 'Loading statuses...' : `${sheetStatuses.length} tracker statuses`}</span>
              <button type="button" onClick={loadSheetStatuses}>Refresh</button>
            </div>
            {SHEET_ORDER.map((k) => {
              const rows = sheetStatuses.filter((r) => r.sheet === k)
              return (
                <div key={k} className="status-group">
                  <h3>{activeSheets?.[k]?.label ?? k}</h3>
                  <div className="selected-status-chips removable-status-chips">
                    {rows.map((r) => (
                      <span key={r.id}>{r.status}<button type="button" onClick={() => deleteSheetStatus(r)}>Remove</button></span>
                    ))}
                    {!rows.length && <em>No statuses yet.</em>}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {tab === 'kanban' && (
        <section className="kanban-settings-page">
          <form className="admin-create-card kanban-create-card" onSubmit={createKanbanColumn}>
            <h2>Create Kanban Column</h2>
            <label>Header<input required value={newKanbanColumn.title} placeholder="Needs Details"
              onChange={(e) => setNewKanbanColumn((c) => ({ ...c, title: e.target.value }))} /></label>
            <label className="wide-field">Statuses
              <StatusMultiSelect allOptions={allStatusOptions} selected={newKanbanColumn.statuses}
                onChange={(next) => setNewKanbanColumn((c) => ({ ...c, statuses: next }))} />
            </label>
            <label>Order<input type="number" value={newKanbanColumn.sort_order}
              onChange={(e) => setNewKanbanColumn((c) => ({ ...c, sort_order: Number(e.target.value) }))} /></label>
            <label className="checkbox-label"><input checked={newKanbanColumn.is_visible} type="checkbox"
              onChange={(e) => setNewKanbanColumn((c) => ({ ...c, is_visible: e.target.checked }))} />Visible</label>
            <button className="primary-action" type="submit">Create Column</button>
          </form>
          <section className="table-card kanban-settings-card">
            <div className="table-tools">
              <span>{kanbanColumnsLoading ? 'Loading...' : `${kanbanColumns.length} kanban columns`}</span>
              <button type="button" onClick={loadKanbanColumns}>Refresh</button>
            </div>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Header</th><th>Statuses</th><th>Order</th><th>Visible</th><th>Actions</th></tr></thead>
                <tbody>
                  {kanbanColumns.map((col) => (
                    <tr key={col.id}>
                      <td><input value={col.title}
                        onBlur={(e) => updateKanbanColumn(col.id, { title: e.target.value })}
                        onChange={(e) => setKanbanColumns((c) => c.map((r) => r.id === col.id ? { ...r, title: e.target.value } : r))} /></td>
                      <td><StatusMultiSelect allOptions={allStatusOptions} selected={col.statuses}
                        onChange={(next) => updateKanbanColumn(col.id, { statuses: next })} /></td>
                      <td><input type="number" value={col.sort_order}
                        onBlur={(e) => updateKanbanColumn(col.id, { sort_order: Number(e.target.value) })}
                        onChange={(e) => setKanbanColumns((c) => c.map((r) => r.id === col.id ? { ...r, sort_order: Number(e.target.value) } : r))} /></td>
                      <td className="actions"><input checked={col.is_visible} type="checkbox"
                        onChange={(e) => updateKanbanColumn(col.id, { is_visible: e.target.checked })} /></td>
                      <td className="actions"><button type="button" onClick={() => deleteKanbanColumn(col.id)}>Delete</button></td>
                    </tr>
                  ))}
                  {!kanbanColumnsLoading && kanbanColumns.length === 0 && (
                    <tr><td colSpan={5} className="empty-state">No kanban columns yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      )}

      {resetPasswordUserId !== null && (
        <PromptModal
          title="Reset Password"
          label="New password"
          inputType="password"
          minLength={6}
          placeholder="Minimum 6 characters"
          submitLabel="Reset Password"
          onSubmit={handleResetPassword}
          onCancel={() => setResetPasswordUserId(null)}
        />
      )}
    </section>
  )
}
