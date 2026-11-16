import { useCallback, useEffect, useState } from 'react'
import {
  BUY_BOOK_GROUP_OPTIONS,
  DEFAULT_BUY_BOOK_GROUP_BY,
  type BuyBookGroupBy,
} from '../constants'
import { SurveyLayoutEditor } from '../surveyLayout'
import type { SurveyFilterField, SurveyItem } from '../types'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
}

export function BuyBookLayoutPage({ apiFetch }: Props) {
  const [items, setItems] = useState<SurveyItem[]>([])
  const [filterFields, setFilterFields] = useState<SurveyFilterField[]>([])
  const [groupBy, setGroupBy] = useState<BuyBookGroupBy>(DEFAULT_BUY_BOOK_GROUP_BY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadLayout = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await apiFetch('/admin/buy-book-layout')
      if (!res.ok) throw new Error('Unable to load buy book layout.')
      const data = await res.json()
      setItems(data.items || [])
      setFilterFields(data.filter_fields || [])
      const nextGroup = BUY_BOOK_GROUP_OPTIONS.some((option) => option.field === data.group_by)
        ? (data.group_by as BuyBookGroupBy)
        : DEFAULT_BUY_BOOK_GROUP_BY
      setGroupBy(nextGroup)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load buy book layout.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    loadLayout()
  }, [loadLayout])

  async function saveLayout(draftItems: SurveyItem[], draftFilters: SurveyFilterField[]) {
    setSaving(true)
    setMessage('')
    setError('')
    try {
      const res = await apiFetch('/admin/buy-book-layout', {
        method: 'PUT',
        body: JSON.stringify({
          filter_fields: draftFilters,
          group_by: groupBy,
          items: draftItems.map((item) => ({
            id: item.id,
            in_buy_book: item.in_buy_book,
            page_break_before: item.page_break_before,
            image_display_size: item.image_display_size ?? 'medium',
            image_fit: item.image_fit ?? 'contain',
          })),
        }),
      })
      if (!res.ok) throw new Error('Unable to save buy book layout.')
      const data = await res.json()
      setItems(data.items || [])
      setFilterFields(data.filter_fields || [])
      if (typeof data.group_by === 'string') {
        setGroupBy(
          BUY_BOOK_GROUP_OPTIONS.some((option) => option.field === data.group_by)
            ? (data.group_by as BuyBookGroupBy)
            : DEFAULT_BUY_BOOK_GROUP_BY,
        )
      }
      setMessage('Buy book layout saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save buy book layout.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="buy-book-layout-page">
      {error && <div className="error">{error}</div>}

      <SurveyLayoutEditor
        title="Buy Book Layout"
        description="Choose catalog items, grouping, and public filters. Use the dashed bars between items for manual page breaks, and drag the image slider to preview print size."
        showSurvey={false}
        showBuyBook
        showResponseWidth={false}
        showImageControls
        filterLegend="Buy book options"
        filterExtra={(
          <div className="survey-layout-filter-row">
            <p className="survey-layout-filter-label">Group by</p>
            <p className="survey-layout-filter-note">
              Items are sorted by this facet and a page break is added when the group changes.
              Manual page breaks still apply inside a group.
            </p>
            <div className="buy-book-group-options">
              {BUY_BOOK_GROUP_OPTIONS.map((option) => (
                <label key={option.field}>
                  <input
                    checked={groupBy === option.field}
                    name="buy-book-group-by"
                    type="radio"
                    value={option.field}
                    onChange={() => setGroupBy(option.field)}
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </div>
        )}
        includeLabel="In Buy Book"
        saveLabel="Save Buy Book Layout"
        filterFields={filterFields}
        items={items}
        loading={loading}
        message={message}
        saving={saving}
        onSave={saveLayout}
      />
    </section>
  )
}
