/**
 * Shape of the structured ``HTTPException.detail`` payload our
 * profile / task APIs return when ``~/.claude/settings.json`` env
 * entries would override the selected AI profile. Kept in a
 * separate file from the modal component so React Refresh's
 * "only export components" rule stays happy.
 */
export type ExternalConfigOverrideDetail = {
  code: 'external_config_override'
  profile_id: string
  overriding_keys: string[]
  settings_path: string
  clear_command: string
  message: string
}

export function isExternalConfigOverrideDetail(
  d: unknown
): d is ExternalConfigOverrideDetail {
  if (!d || typeof d !== 'object') return false
  return (d as { code?: unknown }).code === 'external_config_override'
}
