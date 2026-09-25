// Before the first paint: otherwise the dark side briefly flashes when "light" is set.
// Own file instead of an inline script, because the server's Content Security Policy only allows its own files.
try {
  if (localStorage.getItem('nextrmnl.theme') === 'light') {
    document.documentElement.setAttribute('data-theme', 'light')
    document.querySelector('meta[name="theme-color"]').content = '#f5f5f8'
  }
} catch (e) {
  /* private mode without localStorage */
}
