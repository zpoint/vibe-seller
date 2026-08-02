import { useTranslation } from 'react-i18next'
import type { ActionCode, AuditRow, Choice, MatchType } from '../../lib/adAudit/types'
import {
  ACT_I18N,
  MATCH_TYPES,
  MT_I18N,
  actOf,
  mtOf,
} from '../../lib/adAudit/vocabulary'
import { needsMatchType } from '../../lib/adAudit/review'

interface Props {
  row: AuditRow
  acts: readonly ActionCode[]
  choice: Choice | null
  /** Quarantined campaign: its own block says the advice is not executable. */
  locked: boolean
  onAction: (act: ActionCode) => void
  onMatchType: (mt: MatchType) => void
  /** The bid this raise/lower will carry; null when nobody has set one. */
  targetBid: number | null
  onTargetBid: (v: number | null) => void
}

/**
 * The action control for one row: WHAT to do, then HOW BROADLY.
 *
 * The second level renders only once a match-type-bearing action is chosen.
 * Showing it always would put four controls back on every row — the flat
 * layout this replaced, which hid the fact that adding a keyword needs a
 * match type just as negating does.
 */
export function AuditRowActions({
  row,
  acts,
  choice,
  locked,
  onAction,
  onMatchType,
  targetBid,
  onTargetBid,
}: Props) {
  const { t } = useTranslation()
  const act = actOf(choice)
  const mt = mtOf(choice)

  // Only a bid TRIM depends on CPC, so only that is disabled when CPC could
  // not be computed. Blocking every action on such a row threw away valid
  // work: a raise triggers on ROAS, which rendered fine.
  const unsafeTitle = t('audit.row.bidUnsafeHint')
  const lockedTitle = t('audit.row.quarantinedHint')

  return (
    <>
      <div className="adaudit-acts">
        {acts.map((a) => {
          const unsafe = !!row.bidUnsafe && a === 'lower'
          const disabled = locked || unsafe
          return (
            <button
              key={a}
              type="button"
              data-act={a}
              aria-pressed={act === a}
              disabled={disabled}
              title={locked ? lockedTitle : unsafe ? unsafeTitle : undefined}
              onClick={() => onAction(a)}
            >
              {t(ACT_I18N[a])}
            </button>
          )
        })}
      </div>

      {/* A bid move has to say HOW MUCH. The audit supplies a number when
          it proposed the move; when the reviewer overrides a hold into a
          raise there is nothing to inherit, and an amount-less raise is
          not an instruction anyone can carry out. */}
      {(act === 'raise' || act === 'lower') && (
        <div className="adaudit-mts">
          <span className="adaudit-mtlab">{t('audit.row.targetBidLabel')}</span>
          <input
            type="number"
            step="0.01"
            min="0"
            className="adaudit-bidinput"
            value={targetBid ?? ''}
            disabled={locked}
            aria-label={t('audit.row.targetBidLabel')}
            placeholder={row.bid != null ? String(row.bid) : ''}
            onChange={(e) => {
              const v = e.target.value.trim()
              onTargetBid(v === '' ? null : Number(v))
            }}
          />
          {row.bid != null && (
            <span className="adaudit-mtlab">
              {t('audit.row.fromBid', { bid: row.bid })}
            </span>
          )}
          {targetBid == null && (
            <span className="adaudit-mtdef">{t('audit.row.bidRequired')}</span>
          )}
        </div>
      )}

      {needsMatchType(choice) && (
        <div className="adaudit-mts">
          <span className="adaudit-mtlab">{t('audit.row.matchTypeLabel')}</span>
          {MATCH_TYPES.map((m) => (
            <button
              key={m}
              type="button"
              data-mt={m}
              aria-pressed={mt === m}
              disabled={locked}
              onClick={() => onMatchType(m)}
            >
              {t(MT_I18N[m])}
            </button>
          ))}
          {/* The page may pre-select the narrower option, but it must never
              present its own fallback as the audit's call. */}
          {row.mtFromAudit === false && (
            <span
              className="adaudit-mtdef"
              title={t('audit.row.matchTypeDefaultHint', {
                advice: row.advice.slice(0, 40),
              })}
            >
              {t('audit.row.matchTypeDefault')}
            </span>
          )}
        </div>
      )}

      <div className="adaudit-sugg">
        {row.sugg ? (
          <>
            {t('audit.row.agentSuggests')}
            {': '}
            {t(ACT_I18N[actOf(row.sugg)!])}
            {mtOf(row.sugg) ? ` · ${t(MT_I18N[mtOf(row.sugg)!])}` : ''}
            {row.to != null ? ` → ${row.to}` : ''}
          </>
        ) : (
          <span className="adaudit-ambig">
            {t('audit.row.noExecutableAction', {
              reason: row.ambiguous || '',
            })}
          </span>
        )}
      </div>

      {row.flag === 'no_clicks' && (
        <span className="adaudit-flagchip">{t('audit.row.zeroClicks')}</span>
      )}
    </>
  )
}
