import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement matchMedia. ThemeProvider needs it to resolve "system", and
// tests that don't care about theme still mount it (it's app-wide), so this has to be a
// global default rather than something each test file stubs individually.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
