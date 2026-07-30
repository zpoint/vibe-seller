import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  ActionCode,
  AuditCampaign,
  AuditDoc,
  DecisionSubmission,
  Layer,
  MatchType,
} from '../../lib/adAudit/types'
import type { TargetBidMap } from '../../lib/adAudit/review'
import {
  MATERIAL_SPEND,
  NO_TARGET_BIDS,
  buildSubmission,
  effectiveTargetBid,
  buildReviewState,
  bulkSet,
  campaignChangeCount,
  chooseAction,
  chooseMatchType,
  hasRealName,
  initialChoices,
  roasClass,
  totalsOf,
} from '../../lib/adAudit/review'
import type { ExcludedScopes } from '../../lib/adAudit/scope'
import {
  NOTHING_EXCLUDED,
  campaignInScope,
  campaignKey,
  countryKey,
  keepOnly,
  platformKey,
  scopeCounts,
  toggleScope,
} from '../../lib/adAudit/scope'
import { AuditCampaignBlock } from './AuditCampaignBlock'
import './adAudit.css'

const fmt = (v: number | null, d = 2) =>
  v == null
    ? '—'
    : v.toLocaleString('en-US', {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      })

interface Props {
  doc: AuditDoc
  /** Handed the decision set when the reviewer commits. */
  onSubmit?: (submission: DecisionSubmission) => void
  submitting?: boolean
}

/**
 * The review console: every drilled row, with the audit's recommendation
 * pre-selected so agreeing costs nothing and disagreeing costs one click.
 */
export function AuditConsole({ doc, onSubmit, submitting }: Props) {
  const { t } = useTranslation()
  const state = useMemo(() => buildReviewState(doc), [doc])
  // Choices are immutable state: each edit produces a new map. An earlier
  // version mutated one in place and drove renders off a counter, which made
  // every derived value depend on something React could not see.
  const [choices, setChoices] = useState(() => initialChoices(state))
  const [openIds, setOpenIds] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [country, setCountry] = useState<string | null>(null)
  const [onlyChanged, setOnlyChanged] = useState(false)
  const [showJson, setShowJson] = useState(false)
  // What the reviewer will actually submit. Empty = the whole report; a
  // first pass is usually "just this campaign, leave the rest for next week".
  const [excluded, setExcluded] = useState<ExcludedScopes>(NOTHING_EXCLUDED)
  // Amounts the reviewer typed for raise/lower rows the audit gave no
  // number for. Kept apart from `choices` because a choice is an action,
  // not a value.
  const [targetBids, setTargetBids] = useState<TargetBidMap>(NO_TARGET_BIDS)
  const setTargetBid = (k: string, v: number | null) =>
    setTargetBids((prev) => {
      const next = new Map(prev)
      if (v == null || !Number.isFinite(v)) next.delete(k)
      else next.set(k, v)
      return next
    })
  const drop = (key: string) => setExcluded((prev) => toggleScope(prev, key))

  const totals = totalsOf(state, choices, excluded)
  const scope = scopeCounts(state.campaigns, excluded)
  const countries = useMemo(
    () => [...new Set(state.campaigns.map((c) => c.country))],
    [state],
  )

  const choiceOf = (k: string) => choices.get(k) ?? null
  const baseOf = (k: string) => state.base.get(k) ?? null

  const setAction = (k: string, act: ActionCode) =>
    setChoices((prev) => chooseAction(prev, k, act))
  const setMatchType = (k: string, mt: MatchType) =>
    setChoices((prev) => chooseMatchType(prev, k, mt))
  const bulk = (c: AuditCampaign, layer: Layer, mode: 'sugg' | 'keep') =>
    setChoices((prev) => bulkSet(prev, c, layer, mode))

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return state.campaigns.filter((c) => {
      if (country && c.country !== country) return false
      if (onlyChanged && campaignChangeCount(choices, c) === 0) return false
      if (!q) return true
      if (c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q)) {
        return true
      }
      return [...c.kw, ...c.st].some((r) => r.term.toLowerCase().includes(q))
    })
  }, [state, choices, query, country, onlyChanged])

  const noName = state.campaigns.filter((c) => !hasRealName(c)).length
  const totalRows = state.campaigns.reduce(
    (a, c) => a + c.kw.length + c.st.length,
    0,
  )
  const submission = buildSubmission(state, choices, excluded, targetBids)
  const decisions = showJson ? submission : null

  return (
    <div className="adaudit">
      <div className="adaudit-canvas">
        <section className="adaudit-hero">
          <div className="adaudit-eyebrow">{t('audit.eyebrow')}</div>
          <h2>
            {t('audit.heroTitle', {
              markets: countries.length,
              campaigns: state.campaigns.length,
            })}
          </h2>
          <p className="adaudit-herometa">
            {t('audit.heroMeta', {
              rows: totalRows,
              preselected: totals.acting,
              breakeven: state.breakeven == null ? '—' : fmt(state.breakeven),
            })}
            {noName > 0 && (
              <>
                <br />
                <span className="warn">
                  {t('audit.missingNames', {
                    got: state.campaigns.length - noName,
                    total: state.campaigns.length,
                  })}
                </span>
              </>
            )}
          </p>
          {/* Colour is only useful if it is stated. */}
          <div className="adaudit-legend">
            <span className="adaudit-lg">
              <i className="adaudit-sw adaudit-sw-bleed" />
              {t('audit.legend.bleeding')}
            </span>
            <span className="adaudit-lg">
              <i className="adaudit-sw adaudit-sw-prob" />
              {t('audit.legend.problem', { min: MATERIAL_SPEND })}
            </span>
            <span className="adaudit-lg">
              <i className="adaudit-sw adaudit-sw-idle" />
              {t('audit.legend.idle')}
            </span>
          </div>
        </section>

        {doc.summary.markets.length > 0 && (
          <div className="adaudit-tiles">
            {countries.map((cc) => {
              const mk = doc.summary.markets.filter((m) => m.country === cc)
              const spend = mk.reduce((a, m) => a + (m.spend || 0), 0)
              const sales = mk.reduce((a, m) => a + (m.sales || 0), 0)
              const orders = mk.reduce((a, m) => a + (m.orders || 0), 0)
              const roas = spend > 0 ? sales / spend : null
              const quar = state.campaigns.filter(
                (c) => c.country === cc && c.quarantine,
              ).length
              return (
                <div className="adaudit-tile" key={cc}>
                  <div className="adaudit-tile-head">
                    <span className="adaudit-tile-cc">{cc}</span>
                    <span className="adaudit-chip">{mk[0]?.currency || ''}</span>
                  </div>
                  <div className="adaudit-herometa" style={{ fontSize: 13 }}>
                    {t('audit.tile.campaigns', {
                      count: state.campaigns.filter((c) => c.country === cc)
                        .length,
                    })}
                    {quar > 0 && (
                      <>
                        {' · '}
                        <span className="neg">
                          {t('audit.tile.untrusted', { count: quar })}
                        </span>
                      </>
                    )}
                  </div>
                  <dl className="adaudit-tile-grid">
                    <div>
                      <dt>{t('audit.col.spend')}</dt>
                      <dd>{fmt(spend)}</dd>
                    </div>
                    <div>
                      <dt>{t('audit.col.sales')}</dt>
                      <dd>{fmt(sales)}</dd>
                    </div>
                    <div>
                      <dt>{t('audit.col.roas')}</dt>
                      <dd className={roasClass(roas, state.breakeven)}>
                        {fmt(roas)}
                      </dd>
                    </div>
                    <div>
                      <dt>{t('audit.col.orders')}</dt>
                      <dd>{orders}</dd>
                    </div>
                  </dl>
                </div>
              )
            })}
          </div>
        )}

        <div className="adaudit-bar">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('audit.searchPlaceholder')}
            aria-label={t('audit.searchPlaceholder')}
          />
          <button
            type="button"
            data-on={country === null}
            onClick={() => setCountry(null)}
          >
            {t('audit.allMarkets')}
          </button>
          {countries.map((cc) => (
            <button
              key={cc}
              type="button"
              data-on={country === cc}
              onClick={() => setCountry(cc)}
            >
              {cc}
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <button
            type="button"
            data-on={onlyChanged}
            onClick={() => setOnlyChanged((v) => !v)}
          >
            {t('audit.onlyChanged')}
          </button>
          <button
            type="button"
            onClick={() => setOpenIds(new Set(visible.map((c) => c.id)))}
          >
            {t('audit.expandAll')}
          </button>
          <button type="button" onClick={() => setOpenIds(new Set())}>
            {t('audit.collapseAll')}
          </button>
        </div>

        {visible.length === 0 && (
          <div className="adaudit-empty">{t('audit.noMatches')}</div>
        )}

        {countries
          .filter((cc) => !country || cc === country)
          .map((cc) => {
            const inCountry = visible.filter((c) => c.country === cc)
            if (!inCountry.length) return null
            return (
              <section key={cc} data-dropped={excluded.has(countryKey(cc))}>
                <h3 className="adaudit-sectionhead">
                  {cc}
                  <button
                    type="button"
                    className="adaudit-drop"
                    aria-pressed={excluded.has(countryKey(cc))}
                    onClick={() => drop(countryKey(cc))}
                  >
                    {t(
                      excluded.has(countryKey(cc))
                        ? 'audit.scope.include'
                        : 'audit.scope.drop',
                    )}
                  </button>
                </h3>
                {[...new Set(inCountry.map((c) => c.platform))].map((plat) => (
                  <div
                    key={plat}
                    data-dropped={excluded.has(platformKey(cc, plat))}
                  >
                    <div className="adaudit-plathead">
                      {plat}
                      <button
                        type="button"
                        className="adaudit-drop"
                        aria-pressed={excluded.has(platformKey(cc, plat))}
                        disabled={
                          excluded.has(countryKey(cc)) &&
                          !excluded.has(platformKey(cc, plat))
                        }
                        title={
                          excluded.has(countryKey(cc)) &&
                          !excluded.has(platformKey(cc, plat))
                            ? t('audit.scope.inheritedHint')
                            : undefined
                        }
                        onClick={() => drop(platformKey(cc, plat))}
                      >
                        {t(
                          excluded.has(countryKey(cc)) &&
                          !excluded.has(platformKey(cc, plat))
                            ? 'audit.scope.inherited'
                            : excluded.has(platformKey(cc, plat))
                              ? 'audit.scope.include'
                              : 'audit.scope.drop',
                        )}
                      </button>
                    </div>
                    {inCountry
                      .filter((c) => c.platform === plat)
                      .map((c) => (
                        <AuditCampaignBlock
                          key={c.id}
                          campaign={c}
                          open={openIds.has(c.id)}
                          onToggle={() =>
                            setOpenIds((prev) => {
                              const next = new Set(prev)
                              if (next.has(c.id)) next.delete(c.id)
                              else next.add(c.id)
                              return next
                            })
                          }
                          changeCount={campaignChangeCount(choices, c)}
                          breakeven={state.breakeven}
                          choiceOf={choiceOf}
                          baseOf={baseOf}
                          onAction={setAction}
                          onMatchType={setMatchType}
                          onBulk={bulk}
                          targetBidOf={(k) => {
                            const e = state.rows.get(k)
                            return e
                              ? effectiveTargetBid(e.row, k, targetBids)
                              : null
                          }}
                          onTargetBid={setTargetBid}
                          inScope={campaignInScope(c, excluded)}
                          selfDropped={excluded.has(campaignKey(c.id))}
                          onDrop={() => drop(campaignKey(c.id))}
                          onKeepOnly={() =>
                            setExcluded(keepOnly(state.campaigns, c.id))
                          }
                        />
                      ))}
                  </div>
                ))}
              </section>
            )
          })}
      </div>

      <div className="adaudit-foot">
        <span>
          {t('audit.foot.toChange')} <b>{totals.acting}</b>
          {' · '}
          {t('audit.foot.edited')} <b>{totals.edited}</b>
          {' · '}
          {t('audit.foot.keeping')} <b>{totals.keeping}</b>
          {state.forced > 0 && (
            <> {t('audit.foot.forced', { count: state.forced })}</>
          )}
        </span>
        {scope.droppedCampaigns > 0 && (
          <span className="adaudit-chip todo">
            {t('audit.scope.narrowed', {
              campaigns: scope.campaigns,
              total: state.campaigns.length,
            })}
            {'  '}
            <button
              type="button"
              className="adaudit-drop"
              onClick={() => setExcluded(NOTHING_EXCLUDED)}
            >
              {t('audit.scope.reset')}
            </button>
          </span>
        )}
        {submission.totals.rows_missing_bid > 0 && (
          <span className="adaudit-mtdef">
            {t('audit.foot.needBids', {
              count: submission.totals.rows_missing_bid,
            })}
          </span>
        )}
        <span className="grow" />
        <span>
          {t('audit.foot.impact')}{' '}
          {[...totals.impact.entries()]
            .filter(([, v]) => v > 0)
            .map(([cur, v]) => `${cur} ${fmt(v)}`)
            .join(' · ') || '—'}
        </span>
        <button type="button" onClick={() => setShowJson((v) => !v)}>
          {t('audit.foot.viewJson')}
        </button>
        <button
          type="button"
          className="primary"
          disabled={
            submitting ||
            totals.acting === 0 ||
            !onSubmit ||
            submission.totals.rows_missing_bid > 0
          }
          onClick={() => onSubmit?.(submission)}
        >
          {submitting ? t('audit.foot.sending') : t('audit.foot.handToAgent')}
        </button>
      </div>

      {showJson && (
        <div className="adaudit-canvas" style={{ paddingTop: 0 }}>
          <div className="adaudit-eyebrow">{t('audit.foot.jsonTitle')}</div>
          <pre
            style={{
              maxHeight: 360,
              overflow: 'auto',
              background: '#fff',
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--r-card)',
              padding: 14,
              fontSize: 12,
            }}
          >
            {JSON.stringify(decisions, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}
