import type { ReactNode } from 'react'

import { LanguageSwitcher } from '../../components/LanguageSwitcher'
import { Logo } from '../../components/Logo'
import { ThemeSwitcher } from '../../components/ThemeSwitcher'
import { Card } from '../../components/ui'

/** The frame for the pages before sign-in: logo, switchers, a card in the middle. */
export function AuthFrame({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="nt-glow flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className={'relative z-10 w-full ' + (wide ? 'max-w-md' : 'max-w-sm')}>
        <div className="mb-6 flex items-center justify-between">
          <Logo withWordmark className="h-10 w-10" />
          <div className="flex gap-2">
            <ThemeSwitcher />
            <LanguageSwitcher />
          </div>
        </div>
        <Card>{children}</Card>
      </div>
    </div>
  )
}
