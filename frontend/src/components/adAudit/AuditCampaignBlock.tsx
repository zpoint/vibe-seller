import { useTranslation } from 'react-i18next'
import type {
  ActionCode,
  AuditCampaign,
  Choice,
  Layer,
  MatchType,
} from '../../lib/adAudit/types'
import { hasRealName, roasClass } from '../../lib/adAudit/review'
import { AuditLayerTable } from './AuditLayerTable'

const fmt = (v: number | null, d = 2) =>
  v == null
    ? '—'
    : v.toLocaleString('en-US', {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      })

interface Props {
  campaign: AuditCampaign
  open: boolean
  onToggle: () => void
  changeCount: number
  breakeven: number | null
  choiceOf: (key: string) => Choice | null
  baseOf: (key: string) => Choice | null
  onAction: (key: string, act: ActionCode) => void
  onMatchType: (key: string, mt: MatchType) => void
  onBulk: (campaign: AuditCampaign, layer: Layer, mode: 'sugg' | 'keep') => void
  /** False when this campaign OR its market/platform was dropped. */
  inScope: boolean
  /** True when THIS campaign's own key is excluded, ignoring ancestors. */
  selfDropped: boolean
  onDrop: () => void
  onKeepOnly: () => void
}

export function AuditCampaignBlock({
  campaign: c,
  open,
  onToggle,
  changeCount,
  breakeven,
  choiceOf,
  baseOf,
  onAction,
  onMatchType,
  onBulk,
  inScope,
  selfDropped,
  onDrop,
  onKeepOnly,
}: Props) {
  const { t } = useTranslation()
  const named = hasRealName(c)
  const groupLabel = c.platform === 'noon' ? t('audit.sku') : t('audit.adGroup')

  return (
    <div
      className="adaudit-grp"
      data-open={open}
      data-quar={!!c.quarantine}
      data-dropped={!inScope}
    >
      <div className="adaudit-ghead">
        <button
          type="button"
          className="adaudit-gtoggle"
          aria-expanded={open}
          onClick={onToggle}
        >
          <span className="adaudit-gtitle">
          <span className="adaudit-gname">
            <span className="adaudit-chip cc">{c.country}</span>
            {named ? (
              <span className="adaudit-cname">{c.name}</span>
            ) : (
              <>
                <span className="adaudit-cid">{c.id}</span>
                <span
                  className="adaudit-chip nomame"
                  title={t('audit.noNameHint')}
                >
                  {t('audit.noName')}
                </span>
              </>
            )}
          </span>
          <span className="adaudit-gmeta">
            {named && <span className="adaudit-cid dim">{c.id}</span>}
            {c.type && <span className="adaudit-chip">{c.type}</span>}
            {c.adGroups.length > 0 && (
              <span
                className="adaudit-chip ag"
                title={`${groupLabel}: ${c.adGroups.join(' / ')}`}
              >
                {groupLabel}
                {c.adGroups.length > 1 ? ` ×${c.adGroups.length} ` : ' '}
                {c.adGroups[0]}
              </span>
            )}
            </span>
          </span>
        </button>
        <span className="adaudit-gstats">
          <span className="adaudit-scopectl">
            {/* The control reflects THIS campaign's own state. When an
                ancestor dropped it there is nothing here to toggle — the
                button would say "click to include" and then do nothing —
                so it is disabled and says which level to undo instead. */}
            <button
              type="button"
              className="adaudit-drop"
              aria-pressed={selfDropped}
              disabled={!inScope && !selfDropped}
              title={
                !inScope && !selfDropped
                  ? t('audit.scope.inheritedHint')
                  : undefined
              }
              onClick={onDrop}
            >
              {t(
                !inScope && !selfDropped
                  ? 'audit.scope.inherited'
                  : selfDropped
                    ? 'audit.scope.include'
                    : 'audit.scope.drop',
              )}
            </button>
            <button type="button" className="adaudit-drop" onClick={onKeepOnly}>
              {t('audit.scope.keepOnly')}
            </button>
          </span>
          {changeCount > 0 && (
            <span className="adaudit-chip todo">
              {t('audit.layer.toChange', { count: changeCount })}
            </span>
          )}
          {c.quarantine && (
            <span className="adaudit-qbadge">{t('audit.quarantineBadge')}</span>
          )}
          {c.recon && (
            <span
              className="adaudit-recon"
              data-ok={c.recon.ok}
              title={t('audit.reconHint', {
                currency: c.currency,
                tSpend: fmt(c.recon.tSpend),
                tClicks: c.recon.tClicks ?? '—',
                sSpend: fmt(c.recon.sSpend),
                sClicks: c.recon.sClicks ?? '—',
              })}
            >
              {c.recon.ok ? t('audit.reconOk') : t('audit.reconBad')}
              {c.recon.tSpend
                ? ` ${((c.recon.sSpend || 0) / c.recon.tSpend).toFixed(2)}×`
                : ''}
            </span>
          )}
          <span className="adaudit-stat">
            {t('audit.col.spend')} <b>{`${c.currency} ${fmt(c.spend)}`}</b>
          </span>
          <span className="adaudit-stat">
            {t('audit.col.roas')}{' '}
            <b className={roasClass(c.roas, breakeven)}>{fmt(c.roas)}</b>
          </span>
          <span className="adaudit-stat">
            {t('audit.layer.rowCount', { count: c.kw.length + c.st.length })}
          </span>
        </span>
      </div>

      {open && (
        <div>
          {/* The campaign's own block says its figures disagree with
              themselves, so nothing here is executable. Say why, in place,
              rather than letting a reviewer wonder why the buttons are dead. */}
          {c.quarantine && (
            <p className="adaudit-qnote">
              <b>{t('audit.quarantineTitle')}</b>
              <br />
              {c.quarantine}
            </p>
          )}
          <AuditLayerTable
            campaign={c}
            rows={c.kw}
            layer="kw"
            breakeven={breakeven}
            choiceOf={choiceOf}
            baseOf={baseOf}
            onAction={onAction}
            onMatchType={onMatchType}
            onBulk={(layer, mode) => onBulk(c, layer, mode)}
          />
          <AuditLayerTable
            campaign={c}
            rows={c.st}
            layer="st"
            breakeven={breakeven}
            choiceOf={choiceOf}
            baseOf={baseOf}
            onAction={onAction}
            onMatchType={onMatchType}
            onBulk={(layer, mode) => onBulk(c, layer, mode)}
          />
        </div>
      )}
    </div>
  )
}
