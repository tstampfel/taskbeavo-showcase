import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { checkCatalogues } from './check-catalogues.mjs';

const script = resolve(dirname(fileURLToPath(import.meta.url)), 'check-catalogues.mjs');

const schema = 'enum ErrorCode {\n  """Too fast."""\n  RATE_LIMITED\n}\n';

/** A small, passing set of catalogues and the app source that uses every key. */
function baseline() {
  return {
    catalogues: {
      en: {
        home: { title: 'Home', offers: '{count, plural, one {# offer} other {# offers}}' },
        errors: { RATE_LIMITED: 'Too many attempts.' },
      },
      hr: {
        home: {
          title: 'Početna',
          offers: '{count, plural, one {# ponuda} few {# ponude} other {# ponuda}}',
        },
        errors: { RATE_LIMITED: 'Previše pokušaja.' },
      },
      de: {
        home: { title: 'Start', offers: '{count, plural, one {# Angebot} other {# Angebote}}' },
        errors: { RATE_LIMITED: 'Zu viele Versuche.' },
      },
      fr: {
        home: { title: 'Accueil', offers: '{count, plural, one {# offre} other {# offres}}' },
        errors: { RATE_LIMITED: 'Trop de tentatives.' },
      },
    },
    sources: {
      'web/page.tsx': "const t = useTranslations('home');\nt('title');\nt('offers', { count });\n",
    },
    config: { keep: {}, layouts: [] },
  };
}

const dirs = [];
afterEach(() => {
  while (dirs.length > 0) rmSync(dirs.pop(), { recursive: true, force: true });
});

/** Writes a fixture to a temp dir and returns the CLI arguments that point the check at it. */
function write({ catalogues, sources, config }) {
  const dir = mkdtempSync(join(tmpdir(), 'i18n-check-'));
  dirs.push(dir);
  mkdirSync(join(dir, 'locales'));
  for (const [locale, messages] of Object.entries(catalogues)) {
    writeFileSync(join(dir, 'locales', `${locale}.json`), JSON.stringify(messages));
  }
  for (const [path, text] of Object.entries(sources)) {
    mkdirSync(dirname(join(dir, 'src', path)), { recursive: true });
    writeFileSync(join(dir, 'src', path), text);
  }
  writeFileSync(join(dir, 'schema.graphql'), schema);
  writeFileSync(join(dir, 'config.json'), JSON.stringify(config));
  return {
    args: [
      '--locales',
      join(dir, 'locales'),
      '--sources',
      join(dir, 'src'),
      '--schema',
      join(dir, 'schema.graphql'),
      '--config',
      join(dir, 'config.json'),
    ],
    options: {
      localesDir: join(dir, 'locales'),
      sourceDirs: [join(dir, 'src')],
      schemaPath: join(dir, 'schema.graphql'),
      configPath: join(dir, 'config.json'),
    },
  };
}

function run(args) {
  return spawnSync(process.execPath, [script, ...args], {
    encoding: 'utf8',
    env: { ...process.env, GITHUB_ACTIONS: '' },
  });
}

function messages(fixture) {
  const { errors, warnings } = checkCatalogues(write(fixture).options);
  return { errors: errors.map((e) => e.message), warnings: warnings.map((w) => w.message) };
}

describe('check:catalogues CLI', () => {
  it('passes on the committed catalogues', () => {
    const result = run([]);
    expect(result.stdout).toContain('0 error(s)');
    expect(result.status).toBe(0);
  });

  it('passes on a complete fixture', () => {
    const result = run(write(baseline()).args);
    expect(result.status).toBe(0);
  });

  it.each(['de', 'fr'])('exits non-zero when %s is missing a key', (locale) => {
    const fixture = baseline();
    delete fixture.catalogues[locale].home.title;
    const result = run(write(fixture).args);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain(`error: ${locale} is missing "home.title"`);
  });

  it('prints GitHub annotations in Actions', () => {
    const fixture = baseline();
    delete fixture.catalogues.de.home.title;
    const { args } = write(fixture);
    const result = spawnSync(process.execPath, [script, ...args], {
      encoding: 'utf8',
      env: { ...process.env, GITHUB_ACTIONS: 'true', GITHUB_WORKSPACE: resolve(args[1], '..') },
    });
    expect(result.stdout).toContain('::error file=locales/de.json::de is missing "home.title"');
  });
});

describe('key sets', () => {
  it('flag a key en lacks', () => {
    const fixture = baseline();
    fixture.catalogues.hr.home.extra = 'Višak';
    expect(messages(fixture).errors).toEqual([`hr has "home.extra", which en doesn't`]);
  });

  it("don't count Croatian plural forms as keys", () => {
    expect(messages(baseline()).errors).toEqual([]);
  });

  it('flag invalid ICU', () => {
    const fixture = baseline();
    fixture.catalogues.fr.home.title = 'Accueil {';
    expect(messages(fixture).errors).toEqual([
      expect.stringContaining(`fr "home.title" isn't valid ICU`),
    ]);
  });
});

describe('unused keys', () => {
  it('are errors', () => {
    const fixture = baseline();
    fixture.sources['web/page.tsx'] = "const t = useTranslations('home');\nt('title');\n";
    expect(messages(fixture).errors).toEqual([expect.stringContaining(`"home.offers" isn't used`)]);
  });

  it('may be kept with a reason', () => {
    const fixture = baseline();
    fixture.sources['web/page.tsx'] = "const t = useTranslations('home');\nt('title');\n";
    fixture.config.keep = { 'home.offers': 'plural fixture' };
    expect(messages(fixture).errors).toEqual([]);
  });

  it('warn when a kept key is used again', () => {
    const fixture = baseline();
    fixture.config.keep = { 'home.offers': 'plural fixture' };
    expect(messages(fixture).warnings).toEqual([
      `"home.offers" is used now; remove it from "keep"`,
    ]);
  });

  it('read a single source file listed on its own, like apps/mobile/app.config.ts', () => {
    const fixture = baseline();
    fixture.sources['web/page.tsx'] = "const t = useTranslations('home');\nt('title');\n";
    fixture.sources['app.config.ts'] = "message(locale, 'home.offers');\n";
    const { options } = write(fixture);
    const { errors } = checkCatalogues({
      ...options,
      sourceDirs: [
        join(options.sourceDirs[0], 'web'),
        join(options.sourceDirs[0], 'app.config.ts'),
      ],
    });
    expect(errors).toEqual([]);
  });

  it("don't count keys in comments, but keep strings that contain //", () => {
    const fixture = baseline();
    fixture.sources['web/page.tsx'] =
      "const t = useTranslations('home');\n// t('offers');\n/* t('offers') */\n" +
      "const url = 'https://taskbeavo.hr'; t('title');\n";
    expect(messages(fixture).errors).toEqual([expect.stringContaining(`"home.offers" isn't used`)]);
  });

  it('count full keys and server namespaces as uses', () => {
    const fixture = baseline();
    fixture.sources = {
      'mobile/screen.tsx': "t('home.title');\n",
      'web/server.tsx':
        "const t = await getTranslations({ locale, namespace: 'home' });\nt('offers', { count });\n",
    };
    expect(messages(fixture).errors).toEqual([]);
  });

  it('count every key under the static start of a template literal as used', () => {
    const fixture = baseline();
    fixture.sources = { 'web/page.tsx': 't(`home.${name}`);\n' };
    expect(messages(fixture).errors).toEqual([]);
    fixture.sources = { 'web/page.tsx': 't(`hom${name}`);\n' };
    expect(messages(fixture).errors).toHaveLength(2);
  });

  it("don't count mentions in tests", () => {
    const fixture = baseline();
    fixture.sources = { 'web/page.test.tsx': "t('home.title'); t('home.offers');\n" };
    expect(messages(fixture).errors).toHaveLength(2);
  });

  it('count every errors.<CODE> from the schema as used, and flag other errors keys', () => {
    const fixture = baseline();
    for (const locale of ['hr', 'en', 'de', 'fr']) fixture.catalogues[locale].errors.GONE = 'x';
    expect(messages(fixture).errors).toEqual([expect.stringContaining(`"errors.GONE" isn't used`)]);
  });

  it('flag a "keep" entry for a key that no longer exists', () => {
    const fixture = baseline();
    fixture.config.keep = { 'home.gone': 'old' };
    expect(messages(fixture).errors).toEqual([`"keep" lists "home.gone", which en doesn't have`]);
  });
});

describe('layout length', () => {
  function withTitles(de, fr) {
    const fixture = baseline();
    fixture.catalogues.en.home.title = 'Try again';
    fixture.catalogues.de.home.title = de;
    fixture.catalogues.fr.home.title = fr;
    return fixture;
  }

  it('warns, without failing, when de or fr is much longer than en in a marked layout', () => {
    const fixture = withTitles('Erneut versuchen', 'Réessayer');
    fixture.config.layouts = [{ pattern: 'home.*' }];
    const { errors, warnings } = messages(fixture);
    expect(errors).toEqual([]);
    expect(warnings).toEqual([
      `de "home.title" is 16 characters, 1.8× en (9); the home.* layout allows 1.3×`,
    ]);
    expect(run(write(fixture).args).status).toBe(0);
  });

  it('ignores unmarked keys', () => {
    expect(messages(withTitles('Erneut versuchen', 'Réessayer')).warnings).toEqual([]);
  });

  it('honours a per-layout ratio and character limit', () => {
    const fixture = withTitles('Erneut versuchen', 'Réessayer encore une fois');
    fixture.config.layouts = [{ pattern: 'home.title', maxRatio: 2, maxLength: 20 }];
    expect(messages(fixture).warnings).toEqual([
      `fr "home.title" is 25 characters, 2.8× en (9); the home.title layout allows 2×`,
      `fr "home.title" is 25 characters; the home.title layout allows 20`,
    ]);
  });

  it('measures plurals by their longest option and ignores placeholders', () => {
    const fixture = baseline();
    fixture.config.layouts = [{ pattern: 'home.offers' }];
    // en "# offers" is 8; de "# Angebote" is 10, within 1.3×.
    expect(messages(fixture).warnings).toEqual([]);
  });

  it('warns when a layout marks no key', () => {
    const fixture = baseline();
    fixture.config.layouts = [{ pattern: 'push.*.title' }];
    expect(messages(fixture).warnings).toEqual(['layout "push.*.title" marks no key']);
  });
});

describe('English left in a translation', () => {
  const sentence = 'Your offer was sent to the poster.';

  function withSentence(locales) {
    const fixture = baseline();
    fixture.catalogues.en.home.title = sentence;
    fixture.catalogues.hr.home.title = 'Vaša ponuda poslana je naručitelju.';
    fixture.catalogues.de.home.title = 'Ihr Angebot wurde gesendet.';
    fixture.catalogues.fr.home.title = 'Votre offre a été envoyée.';
    for (const locale of locales) fixture.catalogues[locale].home.title = sentence;
    return fixture;
  }

  it.each(['hr', 'de', 'fr'])('is an error when %s equals en', (locale) => {
    const fixture = withSentence([locale]);
    expect(messages(fixture).errors).toEqual([
      `${locale} "home.title" is the same as en; translate it, or list it under "sameAsEnglish" in catalogue-check.json with a reason`,
    ]);
    expect(run(write(fixture).args).status).toBe(1);
  });

  it('ignores strings of 12 visible characters or fewer, and strings with no space', () => {
    const fixture = baseline();
    // 18 characters in the source, but placeholders count nothing: "Version !" is 9.
    fixture.catalogues.en.home.title = 'Version {version}!';
    fixture.catalogues.de.home.title = 'Version {version}!';
    fixture.catalogues.en.errors.RATE_LIMITED = 'support@taskbeavo.example';
    fixture.catalogues.fr.errors.RATE_LIMITED = 'support@taskbeavo.example';
    expect(messages(fixture).errors).toEqual([]);
  });

  it('flags a 13-character string with a space', () => {
    const fixture = baseline();
    fixture.catalogues.en.home.title = 'Thirteen char'; // 13 characters
    fixture.catalogues.fr.home.title = 'Thirteen char';
    expect(messages(fixture).errors).toEqual([
      expect.stringContaining(`fr "home.title" is the same as en`),
    ]);
  });

  it('counts the visible text, so placeholders do not make a short string long', () => {
    const fixture = baseline();
    fixture.catalogues.en.home.title = '{name} · OIB {oib}';
    for (const locale of ['hr', 'de', 'fr'])
      fixture.catalogues[locale].home.title = '{name} · OIB {oib}';
    expect(messages(fixture).errors).toEqual([]);
  });

  it('may be allowed for the listed locales, with a reason', () => {
    const fixture = withSentence(['de', 'fr']);
    fixture.config.sameAsEnglish = {
      'home.title': { locales: ['de', 'fr'], reason: 'fixture' },
    };
    expect(messages(fixture).errors).toEqual([]);
  });

  it('is still an error in a locale the allowlist does not name', () => {
    const fixture = withSentence(['hr', 'de']);
    fixture.config.sameAsEnglish = { 'home.title': { locales: ['de'], reason: 'fixture' } };
    expect(messages(fixture).errors).toEqual([
      expect.stringContaining(`hr "home.title" is the same as en`),
    ]);
  });

  it('warns when an allowed key is translated now', () => {
    const fixture = withSentence(['de']);
    fixture.config.sameAsEnglish = {
      'home.title': { locales: ['de', 'fr'], reason: 'fixture' },
    };
    expect(messages(fixture).warnings).toEqual([
      `"home.title" differs from en in fr now; remove fr from its "sameAsEnglish" entry`,
    ]);
  });

  it('flags an allowlist entry for a missing key, an unknown locale or no reason', () => {
    const fixture = baseline();
    fixture.config.sameAsEnglish = {
      'home.gone': { locales: ['de'], reason: 'fixture' },
      'home.title': { locales: ['en'], reason: 'fixture' },
      'home.offers': { locales: ['de'] },
    };
    expect(messages(fixture).errors).toEqual([
      `"sameAsEnglish" lists "home.gone", which en doesn't have`,
      `"sameAsEnglish" entry "home.title" needs locales from hr, de, fr and a reason`,
      `"sameAsEnglish" entry "home.offers" needs locales from hr, de, fr and a reason`,
    ]);
  });
});
