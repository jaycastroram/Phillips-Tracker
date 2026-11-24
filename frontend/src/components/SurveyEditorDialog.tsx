import { useEffect, useRef, useState } from 'react'
import { SurveyQuestionEditor } from './SurveyQuestionEditor'
import { Modal } from './Modal'
import { DEFAULT_SURVEY_QUESTIONS } from '../constants'
import type { SurveyItemDraft, SurveyItemPayload, SurveyQuestion } from '../types'

type Props = {
  mode: 'create' | 'edit'
  initial: {
    name: string
    questions: SurveyQuestion[]
    items: SurveyItemDraft[]
    openImmediately: boolean
  }
  saving: boolean
  error: string
  /** Uploads one item image and resolves to its URL. */
  uploadImage: (file: File) => Promise<string>
  /** Uploads one question image and resolves to its URL. */
  uploadQuestionImage: (file: File) => Promise<string>
  onCancel: () => void
  onSave: (payload: {
    name: string
    questions: SurveyQuestion[]
    items: SurveyItemPayload[]
    openImmediately: boolean
  }) => void
}

let itemKeySeed = 0
function nextKey() {
  itemKeySeed += 1
  return `item-${itemKeySeed}`
}

export function emptyItemDraft(questionIds: string[]): SurveyItemDraft {
  return {
    key: nextKey(),
    id: null,
    item_name: '',
    cost: '',
    image_url: '',
    item_number: '',
    item_description: '',
    question_ids: [...questionIds],
  }
}

function looksLikeImage(url: string) {
  return /^(https?:\/\/|\/)/i.test(url.trim())
}

/**
 * The survey dialog: the pool of questions, then the items that tick from it.
 *
 * It sits over the list instead of pushing it down, and scrolls internally, so
 * a survey with twenty items does not turn the page into one long form. The
 * pool is defined once; each item owns a checklist of which of those questions
 * it asks, which is what lets two items carry different question sets.
 */
export function SurveyEditorDialog({
  mode,
  initial,
  saving,
  error,
  uploadImage,
  uploadQuestionImage,
  onCancel,
  onSave,
}: Props) {
  const [name, setName] = useState(initial.name)
  const [questions, setQuestions] = useState<SurveyQuestion[]>(
    initial.questions.length > 0 ? initial.questions : DEFAULT_SURVEY_QUESTIONS,
  )
  const [items, setItems] = useState<SurveyItemDraft[]>(initial.items)
  const [openImmediately, setOpenImmediately] = useState(initial.openImmediately)
  const [expandedKey, setExpandedKey] = useState<string | null>(initial.items[0]?.key ?? null)
  const [questionsFor, setQuestionsFor] = useState<string | null>(null)
  const [itemError, setItemError] = useState('')
  const [bulkNote, setBulkNote] = useState('')
  const poolIdsRef = useRef<string[]>(questions.map((question) => question.id))

  // Keep the items in step with the pool: a question added here ticks every
  // item at once (the server does the same for a save that arrives without
  // it), and a question removed from the pool stops being ticked anywhere.
  // Questions that were already in the pool keep each item's own choice.
  useEffect(() => {
    const poolIds = questions.map((question) => question.id)
    const alive = new Set(poolIds)
    const added = poolIds.filter((questionId) => !poolIdsRef.current.includes(questionId))
    poolIdsRef.current = poolIds
    setItems((current) =>
      current.map((item) => {
        const ticked = item.question_ids.filter((questionId) => alive.has(questionId))
        const next = [...ticked, ...added.filter((questionId) => !ticked.includes(questionId))]
        const unchanged =
          next.length === item.question_ids.length
          && next.every((questionId, index) => questionId === item.question_ids[index])
        return unchanged ? item : { ...item, question_ids: next }
      }),
    )
    if (added.length > 0) {
      setBulkNote(
        added.length === 1
          ? 'The new question is ticked on every item.'
          : `${added.length} new questions are ticked on every item.`,
      )
    }
  }, [questions])

  const everyItemAsksEverything =
    items.length > 0 && questions.length > 0
    && items.every((item) => item.question_ids.length === questions.length)

  function askEverything() {
    const poolIds = questions.map((question) => question.id)
    setItems((current) => current.map((item) => ({ ...item, question_ids: [...poolIds] })))
    setBulkNote(
      poolIds.length === 0
        ? 'There are no questions to ask yet.'
        : `Every item now asks all ${poolIds.length} questions.`,
    )
  }

  function updateItem(key: string, updates: Partial<SurveyItemDraft>) {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...updates } : item)))
  }

  function addItem() {
    const draft = emptyItemDraft(questions.map((question) => question.id))
    setItems((current) => [...current, draft])
    setExpandedKey(draft.key)
    setQuestionsFor(null)
  }

  function removeItem(key: string) {
    setItems((current) => current.filter((item) => item.key !== key))
    if (expandedKey === key) setExpandedKey(null)
    if (questionsFor === key) setQuestionsFor(null)
  }

  function moveItem(key: string, direction: -1 | 1) {
    setItems((current) => {
      const index = current.findIndex((item) => item.key === key)
      const target = index + direction
      if (index < 0 || target < 0 || target >= current.length) return current
      const next = [...current]
      const [moved] = next.splice(index, 1)
      next.splice(target, 0, moved)
      return next
    })
  }

  function toggleQuestion(itemKey: string, questionId: string) {
    setItems((current) =>
      current.map((item) => {
        if (item.key !== itemKey) return item
        const ticked = item.question_ids.includes(questionId)
        return {
          ...item,
          question_ids: ticked
            ? item.question_ids.filter((id) => id !== questionId)
            : [...item.question_ids, questionId],
        }
      }),
    )
  }

  function submit() {
    setItemError('')
    if (!name.trim()) {
      setItemError('Name the survey before saving.')
      return
    }
    if (items.length === 0) {
      setItemError('Add at least one item.')
      return
    }
    const unnamed = items.findIndex((item) => !item.item_name.trim())
    if (unnamed >= 0) {
      setItemError(`Item ${unnamed + 1} needs a name.`)
      setExpandedKey(items[unnamed].key)
      return
    }
    onSave({
      name: name.trim(),
      questions,
      openImmediately,
      items: items.map((item) => ({
        id: item.id,
        item_name: item.item_name.trim(),
        cost: item.cost.trim(),
        image_url: item.image_url.trim(),
        item_number: item.item_number.trim(),
        item_description: item.item_description.trim(),
        question_ids: item.question_ids,
      })),
    })
  }

  return (
    <Modal
      title={mode === 'create' ? 'New survey' : `Edit “${initial.name}”`}
      size="xl"
      className="modal-card--survey"
      onClose={onCancel}
    >
      <form
        className="survey-dialog"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <div className="survey-dialog-body">
          <section className="survey-dialog-section">
            <div className="survey-dialog-details">
              <label>
                <span className="survey-field-label">Survey name</span>
                <input
                  autoFocus
                  required
                  value={name}
                  placeholder="Spring 2026 Pre-Buy"
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label className="survey-create-open">
                <input
                  checked={openImmediately}
                  disabled={mode === 'edit'}
                  type="checkbox"
                  onChange={(event) => setOpenImmediately(event.target.checked)}
                />
                <span>
                  Open immediately
                  <span className="survey-create-hint">
                    {mode === 'edit'
                      ? 'Use Open or Close in the list'
                      : 'Closes any currently open survey'}
                  </span>
                </span>
              </label>
            </div>
          </section>

          <section className="survey-dialog-section">
            <SurveyQuestionEditor
              disabled={saving}
              note="These are the questions a survey can ask. Each item below ticks the ones it shows."
              questions={questions}
              onChange={setQuestions}
              uploadImage={uploadQuestionImage}
            />
          </section>

          <section className="survey-dialog-section">
            <div className="survey-items-head">
              <div>
                <h3>Items</h3>
                <p className="survey-section-note">
                  One card per item on the public survey, in this order. Each item asks
                  the questions ticked on it — use Questions to change one item.
                </p>
              </div>
              <div className="survey-items-head-actions">
                <button
                  className="survey-bulk-all"
                  type="button"
                  disabled={saving || items.length === 0 || questions.length === 0 || everyItemAsksEverything}
                  title="Tick every question on every item below"
                  onClick={askEverything}
                >
                  {questions.length > 0
                    ? `Ask all ${questions.length} questions on every item`
                    : 'Ask all questions on every item'}
                </button>
                <span className="survey-question-count">
                  {items.length} {items.length === 1 ? 'item' : 'items'}
                </span>
              </div>
            </div>
            {bulkNote && (
              <p className="survey-bulk-note" role="status">
                {bulkNote}
              </p>
            )}

            <ol className="survey-item-list">
              {items.map((item, index) => {
                const expanded = expandedKey === item.key
                const questionCount = item.question_ids.length
                return (
                  <li key={item.key} className={`survey-item-card${expanded ? ' is-open' : ''}`}>
                    <div className="survey-item-card-head">
                      <span className="survey-question-handle" aria-hidden="true">{index + 1}</span>
                      <div className="survey-question-moves">
                        <button
                          aria-label={`Move item ${index + 1} up`}
                          disabled={saving || index === 0}
                          type="button"
                          onClick={() => moveItem(item.key, -1)}
                        >
                          ↑
                        </button>
                        <button
                          aria-label={`Move item ${index + 1} down`}
                          disabled={saving || index === items.length - 1}
                          type="button"
                          onClick={() => moveItem(item.key, 1)}
                        >
                          ↓
                        </button>
                      </div>
                      {looksLikeImage(item.image_url) ? (
                        <img className="survey-item-thumb" src={item.image_url} alt="" />
                      ) : (
                        <span className="survey-item-thumb survey-item-thumb--empty" aria-hidden="true">IMG</span>
                      )}
                      <div className="survey-item-summary">
                        <strong>{item.item_name.trim() || 'Untitled item'}</strong>
                        <span>
                          {[item.cost.trim(), item.item_number.trim() ? `#${item.item_number.trim()}` : '']
                            .filter(Boolean)
                            .join(' · ') || 'No cost or number'}
                        </span>
                      </div>
                      <button
                        className={`survey-item-questions${questionsFor === item.key ? ' is-open' : ''}`}
                        type="button"
                        onClick={() => setQuestionsFor(questionsFor === item.key ? null : item.key)}
                        aria-expanded={questionsFor === item.key}
                      >
                        Questions {questionCount}/{questions.length}
                      </button>
                      <button
                        className="secondary-button"
                        type="button"
                        onClick={() => setExpandedKey(expanded ? null : item.key)}
                        aria-expanded={expanded}
                      >
                        {expanded ? 'Done' : 'Edit'}
                      </button>
                      <button
                        aria-label={`Remove item ${index + 1}`}
                        className="survey-question-remove"
                        disabled={saving}
                        type="button"
                        onClick={() => removeItem(item.key)}
                      >
                        ✕
                      </button>
                    </div>

                    {questionsFor === item.key && (
                      <div className="survey-item-question-picker">
                        <div className="survey-item-question-picker-head">
                          <strong>Questions this item asks</strong>
                          <div>
                            <button
                              className="link-button"
                              type="button"
                              onClick={() =>
                                updateItem(item.key, { question_ids: questions.map((question) => question.id) })
                              }
                            >
                              Tick all
                            </button>
                            <button
                              className="link-button"
                              type="button"
                              onClick={() => updateItem(item.key, { question_ids: [] })}
                            >
                              Untick all
                            </button>
                          </div>
                        </div>
                        {questions.length === 0 && <p className="muted">Add a question to the pool first.</p>}
                        <ul>
                          {questions.map((question) => (
                            <li key={question.id}>
                              <label>
                                <input
                                  checked={item.question_ids.includes(question.id)}
                                  disabled={saving}
                                  type="checkbox"
                                  onChange={() => toggleQuestion(item.key, question.id)}
                                />
                                <span>
                                  {question.label}
                                  <em>{question.type === 'yes_no' ? 'Yes / No' : question.type.replace('_', ' ')}</em>
                                </span>
                              </label>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {expanded && (
                      <div className="survey-item-fields">
                        <label>
                          <span className="survey-field-label">Item name</span>
                          <input
                            disabled={saving}
                            placeholder="Blue hoodie, size L"
                            value={item.item_name}
                            onChange={(event) => updateItem(item.key, { item_name: event.target.value })}
                          />
                        </label>
                        <div className="survey-item-fields-row">
                          <label>
                            <span className="survey-field-label">Cost</span>
                            <input
                              disabled={saving}
                              placeholder="$29.00"
                              value={item.cost}
                              onChange={(event) => updateItem(item.key, { cost: event.target.value })}
                            />
                          </label>
                          <label>
                            <span className="survey-field-label">Item number</span>
                            <input
                              disabled={saving}
                              placeholder="10042"
                              value={item.item_number}
                              onChange={(event) => updateItem(item.key, { item_number: event.target.value })}
                            />
                          </label>
                        </div>
                        <label>
                          <span className="survey-field-label">Description</span>
                          <textarea
                            disabled={saving}
                            rows={2}
                            placeholder="What the buyer needs to know about this item."
                            value={item.item_description}
                            onChange={(event) => updateItem(item.key, { item_description: event.target.value })}
                          />
                        </label>
                        <div className="survey-item-image">
                          <div className="survey-item-image-pick">
                            <span className="survey-field-label">Image</span>
                            <label className="survey-question-image-pick">
                              <input
                                accept="image/jpeg,image/png,image/webp"
                                disabled={saving}
                                type="file"
                                onChange={async (event) => {
                                  const file = event.target.files?.[0]
                                  if (!file) return
                                  try {
                                    const url = await uploadImage(file)
                                    if (url) updateItem(item.key, { image_url: url })
                                  } catch {
                                    setItemError('Image upload failed. Paste an image URL instead.')
                                  } finally {
                                    event.target.value = ''
                                  }
                                }}
                              />
                              Upload image
                            </label>
                            <input
                              disabled={saving}
                              placeholder="…or paste an image URL"
                              value={item.image_url}
                              onChange={(event) => updateItem(item.key, { image_url: event.target.value })}
                            />
                          </div>
                          {looksLikeImage(item.image_url) ? (
                            <img className="survey-item-image-preview" src={item.image_url} alt="" />
                          ) : (
                            <span className="survey-item-image-preview survey-item-image-preview--empty">
                              No image
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </li>
                )
              })}
            </ol>

            <button className="survey-question-add" disabled={saving} type="button" onClick={addItem}>
              + Add item
            </button>
          </section>

          {(itemError || error) && <div className="error">{itemError || error}</div>}
        </div>

        <div className="survey-dialog-actions">
          <button className="secondary-button" disabled={saving} type="button" onClick={onCancel}>
            Cancel
          </button>
          <button className="primary-action" disabled={saving} type="submit">
            {saving ? 'Saving…' : mode === 'create' ? 'Create survey' : 'Save changes'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
