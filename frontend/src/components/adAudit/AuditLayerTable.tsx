import { useTranslation } from 'react-i18next'
import type {
  ActionCode,
  AuditCampaign,
  AuditRow,
  Choice,
  Layer,
  MatchType,
} from '../../lib/adAudit/types'
import { KW_ACTS, isChange, stActs } from '../../lib/adAudit/vocabulary'
import { problemsOf, roasClass, rowKey } from '../../lib/adAudit/review'
import { AuditRowActions } from './AuditRowActions'

const fmt = (v: number | null, d = 2) =>
  v == null
    ? '—'
    : v.toLocaleString('en-US', {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      })

interface Props {
  campaign: AuditCampaign
  rows: AuditRow[]
  layer: Layer
  breakeven: number | null
  choiceOf: (key: string) => Choice | null
  baseOf: (key: string) => Choice | null
  onAction: (key: string, act: ActionCode) => void
  onMatchType: (key: string, mt: MatchType) => void
  onBulk: (layer: Layer, mode: 'sugg' | 'keep') => void
  targetBidOf: (key: string) => number | null
  onTargetBid: (key: string, v: number | null) => void
}

export function AuditLayerTable({
  campaign,
  rows,
  layer,
  breakeven,
  choiceOf,
  baseOf,
  onAction,
  onMatchType,
  onBulk,
  targetBidOf,
  onTargetBid,
}: Props) {
  const { t } = useTranslation()
  if (!rows.length) {
    return (
      <div className="adaudit-empty">
        {t(layer === 'kw' ? 'audit.layer.noTargets' : 'audit.layer.noTerms')}
      </div>
    )
  }
  const changes = rows.filter((r) =>
    isChange(choiceOf(rowKey(campaign.id, r))),
  ).length

  return (
    <div>
      <div className="adaudit-layerhead">
        <span>
          {t(layer === 'kw' ? 'audit.layer.targeting' : 'audit.layer.searchTerms')}
          {' · '}
          {t('audit.layer.rowCount', { count: rows.length })}
        </span>
        {changes > 0 && (
          <span className="adaudit-chip todo">
            {t('audit.layer.toChange', { count: changes })}
          </span>
        )}
        <span className="adaudit-bulk">
          <button type="button" onClick={() => onBulk(layer, 'sugg')}>
            {t('audit.layer.restoreAgent')}
          </button>
          <button type="button" onClick={() => onBulk(layer, 'keep')}>
            {t('audit.layer.keepAll')}
          </button>
        </span>
      </div>

      <div className="adaudit-tscroll">
        <table>
          <thead>
            <tr>
              <th>
                {t(layer === 'kw' ? 'audit.col.target' : 'audit.col.searchTerm')}
              </th>
              <th className="mt">{t('audit.col.match')}</th>
              <th className="num">{t('audit.col.bid')}</th>
              <th className="num">{t('audit.col.clicks')}</th>
              <th className="num">{t('audit.col.spend')}</th>
              <th className="num">{t('audit.col.orders')}</th>
              <th className="num">{t('audit.col.roas')}</th>
              <th>{t('audit.col.yourAction')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const k = rowKey(campaign.id, r)
              const cur = choiceOf(k)
              const problems = problemsOf(r, breakeven)
              const rowCls = [
                r.sugg == null
                  ? 'ambig-row'
                  : cur !== baseOf(k)
                    ? 'edited'
                    : isChange(cur)
                      ? 'acting'
                      : '',
                problems.includes('bleeding')
                  ? 'row-bleeding'
                  : problems.includes('idle')
                    ? 'row-idle'
                    : problems.length
                      ? 'row-problem'
                      : '',
              ]
                .filter(Boolean)
                .join(' ')
              return (
                <tr key={k} className={rowCls}>
                  <td>
                    <div className="adaudit-term">{r.term}</div>
                    {r.src && r.src !== '-' && (
                      <div className="adaudit-srck">← {r.src}</div>
                    )}
                  </td>
                  <td className="mt">{r.match || ''}</td>
                  <td className="num">{r.bid == null ? '—' : fmt(r.bid)}</td>
                  <td
                    className={
                      'num' +
                      (problems.includes('noclick')
                        ? ' bad-cell'
                        : problems.includes('idle')
                          ? ' idle-cell'
                          : '')
                    }
                  >
                    {r.clicks == null ? '—' : r.clicks}
                  </td>
                  <td className={'num' + (problems.length ? ' spend-cell' : '')}>
                    {fmt(r.spend)}
                  </td>
                  <td
                    className={
                      'num' + (problems.includes('noorder') ? ' bad-cell' : '')
                    }
                  >
                    {r.orders == null ? '—' : r.orders}
                  </td>
                  <td className={`num ${roasClass(r.roas, breakeven)}`}>
                    {r.roas == null ? '—' : fmt(r.roas)}
                  </td>
                  <td className="adaudit-actscell">
                    <AuditRowActions
                      row={r}
                      acts={layer === 'kw' ? KW_ACTS : stActs(r.match)}
                      choice={cur}
                      locked={!!campaign.quarantine}
                      onAction={(a) => onAction(k, a)}
                      onMatchType={(m) => onMatchType(k, m)}
                      targetBid={targetBidOf(k)}
                      onTargetBid={(v) => onTargetBid(k, v)}
                    />
                    {r.advice && <div className="adaudit-why">{r.advice}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
