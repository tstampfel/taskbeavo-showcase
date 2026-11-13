import { graphqlUrl } from '../src/apollo/endpoint';

describe('graphqlUrl', () => {
  it('uses EXPO_PUBLIC_GRAPHQL_URL when the build sets it', () => {
    expect(graphqlUrl('ios', 'https://api.taskbeavo.hr/graphql', false)).toBe(
      'https://api.taskbeavo.hr/graphql',
    );
  });

  it('reaches the host machine from the iOS simulator and the Android emulator in development', () => {
    expect(graphqlUrl('ios', undefined, true)).toBe('http://localhost:8000/graphql');
    expect(graphqlUrl('android', undefined, true)).toBe('http://10.0.2.2:8000/graphql');
  });

  it('refuses to fall back to localhost outside development', () => {
    expect(() => graphqlUrl('ios', undefined, false)).toThrow('EXPO_PUBLIC_GRAPHQL_URL');
  });
});
