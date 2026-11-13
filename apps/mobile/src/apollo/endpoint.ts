/**
 * The GraphQL endpoint. EAS build profiles set EXPO_PUBLIC_GRAPHQL_URL. Only a development
 * build may go without it: it talks to Django on the host machine, which the Android
 * emulator reaches as 10.0.2.2. A preview or production build without it fails at startup
 * rather than shipping pointed at localhost.
 */
export function graphqlUrl(
  platform: string,
  envUrl: string | undefined = process.env.EXPO_PUBLIC_GRAPHQL_URL,
  isDev: boolean = __DEV__,
): string {
  if (envUrl) return envUrl;
  if (!isDev) {
    throw new Error('EXPO_PUBLIC_GRAPHQL_URL is not set for this build profile (eas.json)');
  }
  const host = platform === 'android' ? '10.0.2.2' : 'localhost';
  return `http://${host}:8000/graphql`;
}
