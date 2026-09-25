import { useTranslation } from 'react-i18next'

import { LANGUAGES, SUPPORTED_LANGUAGES, changeLanguage } from '../i18n'

/** The choice stays in the browser; later it will be tied to the account. A new language appears here on its own. */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  return (
    <div className="flex items-center rounded-full border border-ink-700 bg-ink-850 p-0.5" role="group" aria-label={t('language.label')}>
      {SUPPORTED_LANGUAGES.map((language) => {
        const active = i18n.language === language
        return (
          <button
            key={language}
            type="button"
            lang={language}
            aria-label={LANGUAGES[language].name}
            title={LANGUAGES[language].name}
            onClick={() => void changeLanguage(language)}
            aria-pressed={active}
            className={'rounded-full px-2.5 py-1 text-xs font-semibold transition-colors ' + (active ? 'bg-accent-500 text-on-accent' : 'text-mist-500 hover:text-mist-100')}
          >
            {LANGUAGES[language].label}
          </button>
        )
      })}
    </div>
  )
}
