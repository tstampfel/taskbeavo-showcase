// Sentry's Metro config adds debug IDs to bundles and source maps, so stack traces from EAS
// builds and EAS Updates match their uploaded source maps.
// Metro loads its config as CommonJS.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { getSentryExpoConfig } = require('@sentry/react-native/metro');

module.exports = getSentryExpoConfig(__dirname);
