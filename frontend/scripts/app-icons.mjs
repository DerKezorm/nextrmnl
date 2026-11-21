/* The icons for the home screen, rendered once from the light version of the mark.
 *
 * Same shape as components/Logo.tsx and public/logo.svg; the source of every version is nexapps/icons-vergleich/neu.py.
 * Rendered with Playwright, which the end-to-end tests bring anyway:  node scripts/app-icons.mjs
 *
 * The maskable icon is cropped by Android, so the mark sits smaller on a full white square. The Apple touch icon is
 * full bleed: iOS rounds it itself.
 */
import { chromium } from '@playwright/test'

const VERLAUF = `<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
  <stop offset="0" stop-color="#c4b5fd"/><stop offset=".5" stop-color="#8b5cf6"/><stop offset="1" stop-color="#4c1d95"/>
</linearGradient></defs>`
const ZEICHEN = `<rect x="10" y="13" width="44" height="38" rx="5.5" fill="none" stroke="url(#g)" stroke-width="4.2"/>
  <path d="M10 22h44" stroke="url(#g)" stroke-width="3" stroke-opacity=".55"/>
  <circle cx="16" cy="17.6" r="1.7" fill="url(#g)"/><circle cx="21.5" cy="17.6" r="1.7" fill="url(#g)"/><circle cx="27" cy="17.6" r="1.7" fill="url(#g)"/>
  <path d="M18 30 24.5 36 18 42" fill="none" stroke="url(#g)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M29 42h11" stroke="#7c3aed" stroke-width="4" stroke-linecap="round"/>`

const kachel = (s) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}">${VERLAUF}
  <rect x="2" y="2" width="60" height="60" rx="16" fill="#fff"/>
  <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="15.3" fill="none" stroke="#8b5cf6" stroke-opacity=".35" stroke-width="1.5"/>
  ${ZEICHEN}</svg>`
const voll = (s, m) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}">${VERLAUF}
  <rect width="64" height="64" fill="#fff"/>
  <g transform="translate(32 32) scale(${m}) translate(-32 -32)">${ZEICHEN}</g></svg>`
const randlos = (s) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 2 60 60" width="${s}" height="${s}">${VERLAUF}
  <rect x="2" y="2" width="60" height="60" fill="#fff"/>${ZEICHEN}</svg>`

const BILDER = [
  ['public/icon-192.png', kachel(192), 192],
  ['public/icon-512.png', kachel(512), 512],
  ['public/icon-maskable-512.png', voll(512, 0.8), 512],
  ['public/apple-touch-icon.png', randlos(180), 180],
]

const browser = await chromium.launch()
for (const [pfad, svg, groesse] of BILDER) {
  const seite = await browser.newPage({ viewport: { width: groesse, height: groesse } })
  await seite.setContent(`<body style="margin:0;background:transparent">${svg}</body>`)
  await seite.locator('svg').screenshot({ path: pfad, omitBackground: true })
  await seite.close()
  console.log('written:', pfad)
}
await browser.close()
