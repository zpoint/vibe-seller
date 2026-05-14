import { useTranslation } from 'react-i18next'
import { LanguageSwitcher } from './ui'

interface LoginPageProps {
  loginIdentifier: string
  setLoginIdentifier: (v: string) => void
  loginPassword: string
  setLoginPassword: (v: string) => void
  loginError: string
  onLogin: () => void
}

export function LoginPage({ loginIdentifier, setLoginIdentifier, loginPassword, setLoginPassword, loginError, onLogin }: LoginPageProps) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center justify-center h-screen bg-gray-100">
      <div className="bg-white rounded-xl shadow-lg p-8 w-full max-w-sm">
        <h1 className="text-xl font-bold mb-1">Vibe Seller</h1>
        <p className="text-sm text-gray-500 mb-6">{t('auth.signInTitle')}</p>
        {loginError && <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded mb-4">{loginError}</div>}
        <input
          value={loginIdentifier} onChange={e => setLoginIdentifier(e.target.value)}
          placeholder={t('auth.identifierPlaceholder')} type="text" autoFocus
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          onKeyDown={e => e.key === 'Enter' && onLogin()}
        />
        <input
          value={loginPassword} onChange={e => setLoginPassword(e.target.value)}
          placeholder={t('auth.passwordPlaceholder')} type="password"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
          onKeyDown={e => e.key === 'Enter' && onLogin()}
        />
        <button onClick={onLogin} className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">
          {t('auth.signIn')}
        </button>
        {/* Language switcher on login page */}
        <div className="mt-4 flex justify-center">
          <LanguageSwitcher />
        </div>
      </div>
    </div>
  )
}
