/** Vite (vitest) imports a file's text with `?raw`. */
declare module '*.graphql?raw' {
  const text: string;
  export default text;
}
