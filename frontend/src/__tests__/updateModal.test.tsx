/**
 * The update modal, in the states the auto-update machine can be in,
 * in both languages, admin and member. Chinese texts are the approved
 * mockup's, copied exactly.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateAvailableModal } from '../components/UpdateAvailableModal'
import type { UpdateCheckResult, UpdateStatus } from '../types'

const make = (lng: 'en' | 'zh' = 'zh') => {
  const instance = i18n.createInstance()
  instance.use(initReactI18next).init({
    lng,
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    interpolation: { escapeValue: false },
  })
  return instance
}

const MB = 1024 * 1024

const baseCheck = (over: Partial<UpdateCheckResult> = {}): UpdateCheckResult => ({
  dev: false,
  update_available: true,
  current_version: '0.1.0',
  latest_version: '0.3.2',
  channel: 'stable',
  platform: 'windows',
  upgrade_command: null,
  download_url: 'https://github.com/zpoint/vibe-seller/releases/latest',
  releases_page_url: 'https://github.com/zpoint/vibe-seller/releases',
  in_app_update: true,
  releases: [{ version: '0.3.2', name: 'v0.3.2', body: '', url: 'u', published_at: null }],
  highlights: [
    { version: '0.3.2', zh: '- 亮点甲', en: '- Highlight A' },
    { version: '0.2.0', zh: '- 亮点乙', en: '' },
  ],
  notes: [{ version: '0.3.2', text: '- 需要重启后再配置' }],
  ...over,
})

const baseStatus = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  supported: true,
  state: 'ready',
  version: '0.3.2',
  current_version: '0.1.0',
  downloaded_bytes: null,
  total_bytes: null,
  error: null,
  running_tasks: 0,
  platform: 'windows',
  ...over,
})

const renderModal = (
  ui: 'en' | 'zh',
  props: Partial<React.ComponentProps<typeof UpdateAvailableModal>> = {}
) => {
  const instance = make(ui)
  const onClose = vi.fn()
  const onRestart = vi.fn()
  render(
    <I18nextProvider i18n={instance}>
      <UpdateAvailableModal
        check={baseCheck()}
        status={baseStatus()}
        isAdmin
        onClose={onClose}
        onRestart={onRestart}
        restartError={null}
        restartTimedOut={false}
        {...props}
      />
    </I18nextProvider>
  )
  return { onClose, onRestart }
}

describe('update modal — ready', () => {
  it('windows admin: title, exactly the machine note, restart button', () => {
    renderModal('zh')
    expect(screen.getByText('新版本 v0.3.2 已下载好')).toBeTruthy()
    expect(
      screen.getByText('需要在运行 Vibe Seller 的电脑上操作。')
    ).toBeTruthy()
    expect(
      screen.getByText(/点「重启并更新」后，Vibe Seller 会退出并打开安装程序/)
    ).toBeTruthy()
    expect(screen.getByText('重启并更新')).toBeTruthy()
    expect(screen.getByText('稍后')).toBeTruthy()
  })

  it('unix admin: the 30-second subtitle, no machine note', () => {
    renderModal('zh', {
      status: baseStatus({ platform: 'linux' }),
      check: baseCheck({ platform: 'linux' }),
    })
    expect(
      screen.getByText(/服务器会重启到新版本，大约 30 秒/)
    ).toBeTruthy()
    expect(screen.queryByText('需要在运行 Vibe Seller 的电脑上操作。')).toBeNull()
  })

  it('running tasks: warning box and the "anyway" button', () => {
    renderModal('zh', { status: baseStatus({ running_tasks: 2 }) })
    expect(screen.getByText('有 2 个任务正在运行。')).toBeTruthy()
    expect(screen.getByText('仍然重启并更新')).toBeTruthy()
    expect(screen.queryByText(/^重启并更新$/)).toBeNull()
  })

  it('member: subtitle about the admin, only 知道了', () => {
    renderModal('zh', { isAdmin: false })
    expect(
      screen.getByText('当前 v0.1.0。新版本已经下载好，需要管理员点「重启并更新」后生效。')
    ).toBeTruthy()
    expect(screen.getByText('知道了')).toBeTruthy()
    expect(screen.queryByText('重启并更新')).toBeNull()
    expect(screen.queryByText('稍后')).toBeNull()
  })

  it('restart click calls onRestart; later click calls onClose', () => {
    const { onClose, onRestart } = renderModal('en')
    fireEvent.click(screen.getByText('Restart and update'))
    expect(onRestart).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('Later'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('update modal — downloading', () => {
  it('windows shows MB and percent', () => {
    renderModal('zh', {
      status: baseStatus({
        state: 'downloading',
        downloaded_bytes: Math.floor(603.5 * MB),
        total_bytes: 701 * MB,
      }),
    })
    expect(screen.getByText('正在下载 v0.3.2')).toBeTruthy()
    expect(screen.getByText('正在后台下载安装包')).toBeTruthy()
    expect(screen.getByText('603 / 701 MB · 86%')).toBeTruthy()
    expect(
      screen.getByText(/下载在后台进行，不影响正在运行的任务/)
    ).toBeTruthy()
    expect(screen.getByText('关闭')).toBeTruthy()
  })

  it('mac/linux shows the indeterminate label, no numbers', () => {
    renderModal('zh', {
      status: baseStatus({ state: 'downloading', platform: 'linux' }),
    })
    expect(screen.getByText('正在后台下载新版本')).toBeTruthy()
    expect(screen.queryByText(/MB · %/)).toBeNull()
    expect(screen.queryByText(/\d+%/)).toBeNull()
  })
})

describe('update modal — restarting', () => {
  it('windows message after the click', () => {
    renderModal('zh', { status: baseStatus({ state: 'restarting' }) })
    expect(screen.getByText('正在更新到 v0.3.2')).toBeTruthy()
    expect(
      screen.getByText(/安装程序正在安装新版本。装完会自动启动/)
    ).toBeTruthy()
  })

  it('unix message', () => {
    renderModal('zh', {
      status: baseStatus({ state: 'restarting', platform: 'mac' }),
    })
    expect(screen.getByText(/正在安装新版本并重启服务器/)).toBeTruthy()
  })

  it('timeout falls back to manual instructions', () => {
    renderModal('zh', {
      status: baseStatus({ state: 'restarting' }),
      restartTimedOut: true,
    })
    expect(screen.getByText(/更新还没有完成/)).toBeTruthy()
    expect(screen.getByText('下载最新版本')).toBeTruthy()
  })
})

describe('update modal — merged notes', () => {
  const many = (n: number, prefix: string) =>
    Array.from({ length: n }, (_x, i) => ({
      version: '0.3.2',
      zh: `- ${prefix}${i}`,
      en: `- ${prefix}EN${i}`,
    }))

  it('collapses highlights after 6 and expands with the right count', () => {
    renderModal('zh', {
      check: baseCheck({ highlights: many(8, '亮点') }),
    })
    expect(screen.queryByText('亮点7', { exact: false })).toBeNull()
    fireEvent.click(screen.getByText('展开其余 2 条'))
    expect(screen.getByText('亮点7', { exact: false })).toBeTruthy()
    expect(screen.getByText('在 GitHub 查看完整更新日志')).toBeTruthy()
  })

  it('collapses upgrade notes after 4', () => {
    const notes = Array.from({ length: 6 }, (_x, i) => ({
      version: '0.3.2',
      text: `- note${i}`,
    }))
    renderModal('zh', { check: baseCheck({ notes }) })
    expect(screen.queryByText('note5')).toBeNull()
    fireEvent.click(screen.getByText('展开其余 2 条'))
    expect(screen.getByText('note5')).toBeTruthy()
  })

  it('version header lists every skipped release newest-first', () => {
    renderModal('zh', {
      check: baseCheck({
        releases: [
          { version: '0.3.2', name: '', body: '', url: '', published_at: null },
          { version: '0.2.0', name: '', body: '', url: '', published_at: null },
        ],
      }),
    })
    expect(
      screen.getByText('更新内容 · v0.1.0 → v0.3.2，共 2 个版本（v0.3.2、v0.2.0）')
    ).toBeTruthy()
  })

  it('english UI picks en, falling back to zh when empty', () => {
    renderModal('en')
    expect(screen.getByText('Highlight A', { exact: false })).toBeTruthy()
    // item 2 has en:'' → the zh text shows through
    expect(screen.getByText('亮点乙', { exact: false })).toBeTruthy()
  })
})

describe('update modal — manual experience', () => {
  it('non-supported unix install: the copy command and merged notes', () => {
    renderModal('en', {
      status: baseStatus({ supported: false }),
      check: baseCheck({
        platform: 'linux',
        in_app_update: false,
        upgrade_command: 'vibe-seller upgrade',
        download_url: null,
      }),
    })
    expect(screen.getByText('Update available')).toBeTruthy()
    expect(screen.getByText('vibe-seller upgrade')).toBeTruthy()
    expect(screen.getByText('Copy')).toBeTruthy()
    expect(screen.getByText('Highlight A', { exact: false })).toBeTruthy()
    expect(screen.getByText('Remind me later')).toBeTruthy()
  })

  it('supported install whose background update failed: manual fallback + the error', () => {
    renderModal('zh', {
      status: baseStatus({ state: 'error', error: 'installer size mismatch' }),
      check: baseCheck({
        platform: 'linux',
        upgrade_command: 'vibe-seller upgrade',
        download_url: null,
      }),
    })
    expect(screen.getByText('后台自动更新没有成功')).toBeTruthy()
    expect(screen.getByText('installer size mismatch')).toBeTruthy()
    expect(screen.getByText('vibe-seller upgrade')).toBeTruthy()
  })
})
