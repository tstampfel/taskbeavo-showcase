import base from '@taskbeavo/eslint-config';

export default [...base, { ignores: ['.expo/**', 'ios/**', 'android/**', 'expo-env.d.ts'] }];
