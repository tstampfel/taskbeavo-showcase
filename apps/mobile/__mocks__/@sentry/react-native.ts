// The real SDK needs its native module and starts timers when imported, so every test that
// reaches it (through the Apollo client or the root layout) gets this one instead.
export const init = jest.fn();
export const captureException = jest.fn();
export const addBreadcrumb = jest.fn();
