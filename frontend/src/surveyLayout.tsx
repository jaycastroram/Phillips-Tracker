import { useEffect, useState, type ReactNode } from 'react'
import { SURVEY_FILTER_FIELDS } from './constants'
import type { SurveyFilterField, SurveyItem } from './types'

type SurveyLayoutEditorProps = {
  items: SurveyItem[]
  filterFields: SurveyFilterField[]
  loading: boolean
  saving: boolean
  message: string
  title?: string
  description?: string
  showSurvey?: boolean
  showBuyBook?: boolean
  showResponseWidth?: boolean
  filterLegend?: string
  filterExtra?: ReactNode
  includeLabel?: string
  saveLabel?: string
  disabled?: boolean
  onSave: (items: SurveyItem[], filterFields: SurveyFilterField[]) => void
}

type ImageDisplaySize = 'small' | 'medium' | 'large' | 'full'
type ImageFit = 'contain' | 'cover'

type SurveyItemExtended = SurveyItem & {
  image_display_size?: ImageDisplaySize
  image_fit?: ImageFit
}

export function SurveyLayoutEditor({
  items,
  filterFields,
  loading,
  saving,
  message,
  title = 'Survey Layout',
  description = 'Drag items into survey order. Page breaks split the survey on screen and when printing.',
  showSurvey = true,
  showBuyBook = true,
  showResponseWidth = true,
  filterLegend = 'Survey filters',
  filterExtra,
  includeLabel = 'Buy Book',
  saveLabel = 'Save Layout',
  disabled = false,
  onSave,
}: SurveyLayoutEditorProps) {
  const [draftItems, setDraftItems] = useState<SurveyItemExtended[]>(items as SurveyItemExtended[])
  const [draftFilters, setDraftFilters] = useState(filterFields)
  const [draggingId, setDraggingId] = useState<number | null>(null)

  useEffect(() => {
    setDraftItems(items as SurveyItemExtended[])
    setDraftFilters(filterFields)
  }, [filterFields, items])

  function moveItem(sourceId: number, targetId: number) {
    if (sourceId === targetId) return
    setDraftItems((current) => {
      const fromIndex = current.findIndex((item) => item.id === sourceId)
      const toIndex = current.findIndex((item) => item.id === targetId)
      if (fromIndex < 0 || toIndex < 0) return current
      const next = [...current]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(toIndex, 0, moved)
      return next.map((item, index) => ({
        ...item,
        survey_sort_order: index + 1,
        page_break_before: index === 0 ? false : item.page_break_before,
      }))
    })
  }

  function updateItem(itemId: number, updates: Partial<SurveyItem>) {
    setDraftItems((current) =>
      current.map((item) => (item.id === itemId ? { ...item, ...updates } : item)),
    )
  }

  function toggleFilter(field: SurveyFilterField) {
    setDraftFilters((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    )
  }

  return (
    <section className="survey-layout-editor">
      <div className="table-tools">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <button
          className="primary-action"
          type="button"
          disabled={disabled || loading || saving || draftItems.length === 0}
          onClick={() => onSave(draftItems, draftFilters)}
        >
          {saving ? 'Saving...' : saveLabel}
        </button>
      </div>
      {message && <div className="success">{message}</div>}

      <fieldset className={`survey-layout-filters${filterExtra ? ' has-extra' : ''}`}>
        <legend>{filterLegend}</legend>
        {filterExtra}
        <div className="survey-layout-filter-row">
          <p className="survey-layout-filter-label">Public filters</p>
          <div className="survey-layout-filter-options">
            {SURVEY_FILTER_FIELDS.map((filter) => (
              <label key={filter.field}>
                <input
                  checked={draftFilters.includes(filter.field)}
                  type="checkbox"
                  onChange={() => toggleFilter(filter.field)}
                />
                {filter.label}
              </label>
            ))}
          </div>
        </div>
      </fieldset>

      {loading ? (
        <div className="empty-state">Loading layout...</div>
      ) : (
        <ol className="survey-layout-list">
          {draftItems.map((item, index) => (
            <li
              key={item.id}
              className={draggingId === item.id ? 'is-dragging' : ''}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (draggingId !== null) moveItem(draggingId, item.id)
                setDraggingId(null)
              }}
            >
              <span
                className="survey-layout-handle"
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/plain', String(item.id))
                  event.dataTransfer.effectAllowed = 'move'
                  setDraggingId(item.id)
                }}
                onDragEnd={() => setDraggingId(null)}
              >
                Drag
              </span>
              <div>
                <strong>{item.item_name}</strong>
                <p>{[item.brand, item.channel, item.product_type].filter(Boolean).join(' | ')}</p>
              </div>
              {showSurvey && (
                <label>
                  <input
                    checked={item.in_survey}
                    type="checkbox"
                    onChange={(event) => updateItem(item.id, { in_survey: event.target.checked })}
                  />
                  Survey
                </label>
              )}
              {showBuyBook && (
                <label>
                  <input
                    checked={item.in_buy_book}
                    type="checkbox"
                    onChange={(event) => updateItem(item.id, { in_buy_book: event.target.checked })}
                  />
                  {includeLabel}
                </label>
              )}
              {showResponseWidth && (
                <label className="survey-layout-width">
                  Response width
                  <input
                    max={70}
                    min={20}
                    type="range"
                    value={item.response_width}
                    onChange={(event) =>
                      updateItem(item.id, { response_width: Number(event.target.value) })
                    }
                  />
                  <span>{item.response_width}%</span>
                </label>
              )}
              <label className="survey-layout-select">
                Image Size
                <select
                  value={(item as SurveyItemExtended).image_display_size ?? 'medium'}
                  onChange={(e) => updateItem(item.id, { image_display_size: e.target.value as ImageDisplaySize } as Partial<SurveyItem>)}
                >
                  <option value="small">Small (80px)</option>
                  <option value="medium">Medium (120px)</option>
                  <option value="large">Large (200px)</option>
                  <option value="full">Full width</option>
                </select>
              </label>
              <label className="survey-layout-select">
                Image Fit
                <select
                  value={(item as SurveyItemExtended).image_fit ?? 'contain'}
                  onChange={(e) => updateItem(item.id, { image_fit: e.target.value as ImageFit } as Partial<SurveyItem>)}
                >
                  <option value="contain">Contain</option>
                  <option value="cover">Cover</option>
                </select>
              </label>
              <button
                className={item.page_break_before ? 'primary-action' : 'secondary-action'}
                disabled={index === 0}
                type="button"
                onClick={() => updateItem(item.id, { page_break_before: !item.page_break_before })}
              >
                {item.page_break_before ? 'Remove page break' : 'Page break before'}
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
