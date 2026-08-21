/**
 * Next.js resolves the bare `server-only` specifier to its own compiled guard,
 * so the package is never installed. Vitest has no such resolution, and the
 * real package throws on import outside a React Server Component. Aliasing it
 * to this empty module lets server modules keep their production guard while
 * still being unit-testable.
 */
export {};
