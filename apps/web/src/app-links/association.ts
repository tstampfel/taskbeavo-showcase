import { APP_BUILDS, linkedPathPrefixes } from '@taskbeavo/app-links';

/**
 * The association files that let taskbeavo.com links open the mobile app: Apple's
 * apple-app-site-association (universal links) and Android's assetlinks.json (App Links).
 * Paths and app IDs come from @taskbeavo/app-links, which the mobile app reads too.
 *
 * Signing identities come from the environment, never the repo:
 * - APPLE_TEAM_ID: the Apple Developer team that signs the iOS app (10 characters).
 * - ANDROID_SHA256_CERT_FINGERPRINTS[_PREVIEW|_DEVELOPMENT]: comma-separated SHA-256 signing
 *   certificate fingerprints per Android build. Production needs both the Play app signing key
 *   and the upload key; EAS credentials show them.
 * A file whose values aren't set is not served (404), so validators fail loudly instead of
 * passing on an empty file.
 */

type Env = Readonly<Record<string, string | undefined>>;

const TEAM_ID = /^[A-Z0-9]{10}$/;
const FINGERPRINT = /^(?:[0-9A-F]{2}:){31}[0-9A-F]{2}$/;

/**
 * `/tasks/*`, `/de/tasks/*`, ... : a linked section with and without a locale prefix. Whole
 * sections go to the app; see LINKED_SECTIONS before adding a web-only page under one.
 */
export function linkedPathPatterns(): string[] {
  return linkedPathPrefixes().map((prefix) => `${prefix}*`);
}

export interface AppleAppSiteAssociation {
  applinks: {
    details: { appIDs: string[]; components: { '/': string; comment: string }[] }[];
  };
}

/** The apple-app-site-association document, or null when APPLE_TEAM_ID isn't set. */
export function appleAppSiteAssociation(env: Env): AppleAppSiteAssociation | null {
  const teamId = env.APPLE_TEAM_ID?.trim();
  if (!teamId) return null;
  if (!TEAM_ID.test(teamId)) throw new Error('APPLE_TEAM_ID must be 10 letters or digits.');
  return {
    applinks: {
      details: [
        {
          appIDs: APP_BUILDS.map((build) => `${teamId}.${build.id}`),
          components: linkedPathPatterns().map((pattern) => ({
            '/': pattern,
            comment: 'Opens the screen only; actions need a tap in the app.',
          })),
        },
      ],
    },
  };
}

export interface AssetLinkStatement {
  relation: ['delegate_permission/common.handle_all_urls'];
  target: { namespace: 'android_app'; package_name: string; sha256_cert_fingerprints: string[] };
}

function fingerprints(env: Env, name: string): string[] {
  const values = (env[name] ?? '')
    .split(',')
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);
  if (values.some((value) => !FINGERPRINT.test(value))) {
    throw new Error(`${name} must hold SHA-256 fingerprints like AB:CD:… (32 bytes each).`);
  }
  return values;
}

/** The assetlinks.json statements for each build with fingerprints, or null when none has. */
export function assetLinks(env: Env): AssetLinkStatement[] | null {
  const statements = APP_BUILDS.flatMap((build): AssetLinkStatement[] => {
    const sha256 = fingerprints(env, build.fingerprintsVar);
    return sha256.length === 0
      ? []
      : [
          {
            relation: ['delegate_permission/common.handle_all_urls'],
            target: {
              namespace: 'android_app',
              package_name: build.id,
              sha256_cert_fingerprints: sha256,
            },
          },
        ];
  });
  return statements.length === 0 ? null : statements;
}

/**
 * The response both files share: JSON at its exact path, no redirect (Apple's CDN and Android
 * refuse to follow one), cached for an hour.
 */
export function associationResponse(body: unknown): Response {
  if (body === null) return new Response('Not found', { status: 404 });
  return Response.json(body, { headers: { 'cache-control': 'public, max-age=3600' } });
}
