/**
 * A stand-in for the Django API that server components call (GRAPHQL_URL in
 * playwright.config.ts). page.route only reaches the browser's requests, so the legal pages
 * (server-rendered, BEAV-81) read their documents from here. It answers the legal page queries
 * and refuses everything else, as if the API were down.
 *
 * The published versions: every kind has 2026-11 in force and 2027-01 scheduled; PRIVACY's
 * current version is a draft; COOKIES has nothing published yet. GeoLite2 is in use.
 *
 * It also answers the service fee price lists (/fees, BEAV-191): 2026-11 charged now, a 2027-03
 * change announced, and an earlier 2026-01 list for the archive. The real numbers come from
 * backend/bookings/money.py; these only stand in for them.
 */
import { createServer } from 'node:http';

const port = Number(process.env.LEGAL_API_STUB_PORT ?? 3199);
const DAY = 24 * 3600 * 1000;
const inForce = new Date(Date.now() - 30 * DAY).toISOString();
const scheduled = new Date(Date.now() + 40 * DAY).toISOString();

function versions(kind, language) {
  if (kind === 'COOKIES') return [];
  return [
    { version: '2027-01', effectiveAt: scheduled, isDraft: false },
    { version: '2026-11', effectiveAt: inForce, isDraft: kind === 'PRIVACY' },
  ].map((row) => ({
    __typename: 'LegalDocument',
    id: `${kind}-${row.version}-${language}`,
    kind,
    language,
    ...row,
    text:
      `# 1. ${kind} ${row.version} (${language})\n\n` +
      'Stub text from e2e/legal-api-stub.mjs, standing in for the lawyer’s.\n\n' +
      '- First point\n- Second point',
  }));
}

function feeSchedule(version, effectiveFrom, percent, minimumCents, status) {
  const examples = [4000, 1500].map((price) => {
    const fee = Math.max(Math.floor((price * percent + 50) / 100), minimumCents);
    return {
      __typename: 'ServiceFeeExample',
      id: `${version}:${price}`,
      taskPriceCents: price,
      feeCents: fee,
      totalCents: price + fee,
      vatCents: null,
    };
  });
  return {
    __typename: 'ServiceFeeSchedule',
    id: version,
    version,
    effectiveFrom,
    percent,
    minimumCents,
    status,
    vat: 'UNDECIDED',
    vatPercent: 25,
    examples,
  };
}

function answer({ operationName, variables = {} }) {
  if (operationName === 'ServiceFeeSchedules') {
    return {
      data: {
        serviceFeeSchedules: [
          feeSchedule('2027-03', '2027-03-01', 15, 300, 'UPCOMING'),
          feeSchedule('2026-11', '2026-11-01', 12, 250, 'CURRENT'),
          feeSchedule('2026-01', '2026-01-01', 10, 200, 'EARLIER'),
        ],
        cancellationAmounts: {
          __typename: 'CancellationAmounts',
          id: 'current',
          gracePeriodMinutes: 60,
          lateWindowHours: 24,
          latePayPercent: 25,
          latePayMinCents: 500,
          latePayMaxCents: 2000,
          callOutPercent: 25,
          callOutMinCents: 500,
          callOutMaxCents: 1500,
          keptFeePercent: 12,
          helperCancelCreditCents: 500,
          withdrawalPeriodDays: 14,
        },
      },
    };
  }
  const { kind, language, version } = variables;
  const all = versions(kind, language);
  const current = all.find((row) => row.version === '2026-11') ?? null;
  const list = all.map(({ id, version: v, effectiveAt, isDraft }) => ({
    __typename: 'LegalDocument',
    id,
    version: v,
    effectiveAt,
    isDraft,
  }));
  if (operationName === 'LegalPage') {
    return {
      data: { currentLegalDocument: current, legalDocumentVersions: list, usesGeolite2: true },
    };
  }
  if (operationName === 'LegalVersionPage') {
    return {
      data: {
        legalDocument: all.find((row) => row.version === version) ?? null,
        currentLegalDocument: current && { __typename: 'LegalDocument', id: current.id },
        legalDocumentVersions: list,
        usesGeolite2: true,
      },
    };
  }
  return null;
}

createServer((request, response) => {
  let body = '';
  request.on('data', (chunk) => (body += chunk));
  request.on('end', () => {
    let result = null;
    try {
      result = answer(JSON.parse(body || '{}'));
    } catch {
      // Not JSON: refused below.
    }
    response.writeHead(result ? 200 : 503, { 'content-type': 'application/json' });
    response.end(JSON.stringify(result ?? { errors: [{ message: 'Stub API: not available.' }] }));
  });
}).listen(port);
