import { describe, expect, it } from 'vitest';
import {
  appleAppSiteAssociation,
  assetLinks,
  associationResponse,
  linkedPathPatterns,
} from './association';

const RELEASE = Array.from({ length: 32 }, (_, i) => (i + 16).toString(16).toUpperCase()).join(':');
const UPLOAD = Array.from({ length: 32 }, () => 'AB').join(':');

describe('apple-app-site-association', () => {
  it('is not served until APPLE_TEAM_ID is set', () => {
    expect(appleAppSiteAssociation({})).toBeNull();
    expect(appleAppSiteAssociation({ APPLE_TEAM_ID: ' ' })).toBeNull();
  });

  it('associates every app build of the team with the linked paths', () => {
    const file = appleAppSiteAssociation({ APPLE_TEAM_ID: 'ABCDE12345' });
    const [details] = file?.applinks.details ?? [];
    expect(details?.appIDs).toEqual([
      'ABCDE12345.com.taskbeavo.app',
      'ABCDE12345.com.taskbeavo.app.preview',
      'ABCDE12345.com.taskbeavo.app.dev',
    ]);
    const paths = details?.components.map((component) => component['/']);
    expect(paths).toEqual(linkedPathPatterns());
    expect(paths).toEqual(
      expect.arrayContaining(['/tasks/*', '/bookings/*', '/de/bookings/*', '/fr/conversations/*']),
    );
    // Nothing else of the site opens the app (no "/*").
    expect(
      paths?.every((path) =>
        /^(\/[a-z]{2})?\/(tasks|bookings|conversations|helper)\/\*$/.test(path),
      ),
    ).toBe(true);
  });

  it('refuses a malformed team ID', () => {
    expect(() => appleAppSiteAssociation({ APPLE_TEAM_ID: 'not-a-team' })).toThrow(/APPLE_TEAM_ID/);
  });
});

describe('assetlinks.json', () => {
  it('is not served until a build has fingerprints', () => {
    expect(assetLinks({})).toBeNull();
  });

  it('lists each build with fingerprints, normalised to upper case', () => {
    const statements = assetLinks({
      ANDROID_SHA256_CERT_FINGERPRINTS: `${RELEASE.toLowerCase()}, ${UPLOAD}`,
      ANDROID_SHA256_CERT_FINGERPRINTS_PREVIEW: UPLOAD,
    });
    expect(statements).toEqual([
      {
        relation: ['delegate_permission/common.handle_all_urls'],
        target: {
          namespace: 'android_app',
          package_name: 'com.taskbeavo.app',
          sha256_cert_fingerprints: [RELEASE, UPLOAD],
        },
      },
      {
        relation: ['delegate_permission/common.handle_all_urls'],
        target: {
          namespace: 'android_app',
          package_name: 'com.taskbeavo.app.preview',
          sha256_cert_fingerprints: [UPLOAD],
        },
      },
    ]);
  });

  it('refuses a malformed fingerprint without echoing it', () => {
    expect(() => assetLinks({ ANDROID_SHA256_CERT_FINGERPRINTS: 'AB:CD' })).toThrow(
      /^ANDROID_SHA256_CERT_FINGERPRINTS must/,
    );
  });
});

describe('associationResponse', () => {
  it('serves JSON with a cache header', async () => {
    const response = associationResponse({ applinks: {} });
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/json');
    expect(response.headers.get('cache-control')).toBe('public, max-age=3600');
    expect(await response.json()).toEqual({ applinks: {} });
  });

  it('is a 404 when the file is not configured', () => {
    expect(associationResponse(null).status).toBe(404);
  });
});
