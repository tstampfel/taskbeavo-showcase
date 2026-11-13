#!/usr/bin/env node
/**
 * Catalogue check for CI: `pnpm --filter @taskbeavo/i18n check:catalogues`.
 *
 * Errors (exit 1):
 * - a key in en that another locale lacks, or a key another locale has that en lacks;
 * - a message that isn't valid ICU;
 * - a key no app code uses (see `usedKeys` for what counts as a use);
 * - a hr, de or fr message identical to en (left untranslated), unless its visible text is short, has no space
 *   (see `mayMatchEnglish`), or is listed under "sameAsEnglish" in `catalogue-check.json`.
 *
 * Warnings (exit 0): a German or French message much longer than English, for keys marked in
 * `catalogue-check.json` under "layouts". The i18n-strings skill documents the config.
 *
 * Plurals live inside one ICU message, so Croatian's extra `few` form never adds a key.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, TYPE } from '@formatjs/icu-messageformat-parser';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoDir = resolve(packageDir, '../..');

export const LOCALES = ['hr', 'en', 'de', 'fr'];
const SOURCE_LOCALE = 'en';
const LENGTH_CHECKED = ['de', 'fr'];
const TRANSLATED = LOCALES.filter((locale) => locale !== SOURCE_LOCALE);
/** German and French normally run 20–30% longer than English; beyond this is "much longer". */
export const DEFAULT_MAX_RATIO = 1.3;

export const defaults = {
  localesDir: join(packageDir, 'src/locales'),
  sourceDirs: [
    join(repoDir, 'apps/web/src'),
    join(repoDir, 'apps/mobile/app'),
    join(repoDir, 'apps/mobile/src'),
    // The iOS permission texts, read from the catalogues at build time.
    join(repoDir, 'apps/mobile/app.config.ts'),
    // The shared upload flow's client-side error messages.
    join(repoDir, 'packages/graphql/src'),
  ],
  schemaPath: join(repoDir, 'packages/graphql/schema.graphql'),
  configPath: join(packageDir, 'catalogue-check.json'),
};

const SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']);
/** Tests mention keys to assert on them; that isn't a use. */
const TEST_FILE = /\.(test|spec)\.[cm]?[jt]sx?$/;
const SKIPPED_DIRS = new Set(['node_modules', '.next', '.expo', 'dist', 'e2e', '__tests__']);

/** Flattens a nested catalogue to a Map of dotted key → message. */
export function flatten(obj, prefix = '', out = new Map()) {
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'object' && value !== null) flatten(value, `${prefix}${key}.`, out);
    else out.set(`${prefix}${key}`, value);
  }
  return out;
}

/** Values of `enum ErrorCode` in the exported schema, with descriptions stripped. */
export function readErrorCodes(schemaPath) {
  const sdl = readFileSync(schemaPath, 'utf8');
  const body = /enum ErrorCode \{([^}]*)\}/.exec(sdl)?.[1];
  if (body === undefined) throw new Error(`enum ErrorCode not found in ${schemaPath}`);
  return body
    .replace(/"""[\s\S]*?"""/g, '')
    .replace(/"[^"\n]*"/g, '')
    .split(/\s+/)
    .filter(Boolean);
}

/** The source files under `dir`, or `dir` itself when it is a source file. */
function sourceFiles(dir) {
  if (SOURCE_EXTENSIONS.has(extname(dir)) && !TEST_FILE.test(dir)) return [dir];
  let names;
  try {
    names = readdirSync(dir);
  } catch {
    return [];
  }
  return names.flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return SKIPPED_DIRS.has(name) ? [] : sourceFiles(path);
    return SOURCE_EXTENSIONS.has(extname(name)) && !TEST_FILE.test(name) ? [path] : [];
  });
}

const NAMESPACE = [
  /useTranslations\(\s*['"]([\w.-]+)['"]/g, // next-intl client
  /getTranslations\(\s*['"]([\w.-]+)['"]/g, // next-intl server, short form
  /namespace:\s*['"]([\w.-]+)['"]/g, // next-intl server, object form
  /keyPrefix:\s*['"]([\w.-]+)['"]/g, // i18next
];
const STRING = /'((?:\\.|[^'\\\n])*)'|"((?:\\.|[^"\\\n])*)"|`((?:\\.|[^`\\])*)`/g;
/** Strings first, so `//` inside a string (a URL) isn't taken for a comment. */
const STRING_OR_COMMENT = new RegExp(`${STRING.source}|//[^\\n]*|/\\*[\\s\\S]*?\\*/`, 'g');
const KEY_LIKE = /^[\w-]+(\.[\w-]+)*\.?$/;

/**
 * The catalogue keys app code uses. A key counts as used when a string literal in a source
 * file equals it, or equals its tail after a namespace the file opens (`useTranslations('ns')`,
 * `getTranslations({ namespace })`, i18next `keyPrefix`). A template literal whose static start
 * is a key prefix (`` t(`enums.BookingStatus.${status}`) ``) marks every key under that prefix.
 * Every `errors.<CODE>` whose code is in the schema's `ErrorCode` enum is used, because clients
 * translate whichever code the API returns. Comments are ignored, so commented-out code
 * doesn't keep a key alive.
 */
export function usedKeys(keys, { sourceDirs, errorCodes }) {
  const used = new Set();
  for (const code of errorCodes) used.add(`errors.${code}`);

  for (const file of sourceDirs.flatMap(sourceFiles)) {
    const text = readFileSync(file, 'utf8').replace(STRING_OR_COMMENT, (match) =>
      match.startsWith('/') ? ' ' : match,
    );
    const prefixes = [''];
    for (const pattern of NAMESPACE) {
      for (const match of text.matchAll(pattern)) prefixes.push(`${match[1]}.`);
    }
    for (const match of text.matchAll(STRING)) {
      const template = match[3];
      const isDynamic = template !== undefined && template.includes('${');
      const literal = isDynamic
        ? template.slice(0, template.indexOf('${'))
        : (match[1] ?? match[2] ?? template);
      if (!KEY_LIKE.test(literal)) continue;
      for (const prefix of prefixes) {
        const candidate = prefix + literal;
        if (isDynamic) {
          if (!candidate.endsWith('.')) continue;
          for (const key of keys) if (key.startsWith(candidate)) used.add(key);
        } else if (keys.has(candidate)) {
          used.add(candidate);
        }
      }
    }
  }
  return used;
}

/** Visible length of a parsed ICU message: placeholders count 0, plurals and selects their longest option. */
export function visibleLength(elements) {
  let length = 0;
  for (const element of elements) {
    if (element.type === TYPE.literal) length += Array.from(element.value).length;
    else if (element.type === TYPE.pound) length += 1;
    else if (element.type === TYPE.tag) length += visibleLength(element.children);
    else if (element.type === TYPE.plural || element.type === TYPE.select) {
      length += Math.max(...Object.values(element.options).map((o) => visibleLength(o.value)));
    }
  }
  return length;
}

/**
 * Whether a translation may equal the English text without being listed: a short string (12
 * visible characters or fewer, so `Version {version}` counts 8) or one with no space (an email
 * address, a code) is often the same in every language.
 */
export function mayMatchEnglish(message, elements) {
  return visibleLength(elements) <= 12 || !/\s/.test(message);
}

/** `*` matches one key segment: `push.*.title` marks `push.offerCreated.title`. */
function matchesPattern(pattern, key) {
  const source = pattern
    .split('.')
    .map((part) => (part === '*' ? '[^.]+' : part))
    .join('\\.');
  return new RegExp(`^${source}$`).test(key);
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** Runs every check and returns `{ errors, warnings }`, each a list of `{ file, message }`. */
export function checkCatalogues(options = {}) {
  const { localesDir, sourceDirs, schemaPath, configPath } = { ...defaults, ...options };
  const config = readJson(configPath);
  const errors = [];
  const warnings = [];
  const localeFile = (locale) => join(localesDir, `${locale}.json`);

  const catalogues = Object.fromEntries(
    LOCALES.map((locale) => [locale, flatten(readJson(localeFile(locale)))]),
  );
  const source = catalogues[SOURCE_LOCALE];

  for (const locale of LOCALES) {
    if (locale === SOURCE_LOCALE) continue;
    const keys = catalogues[locale];
    for (const key of source.keys()) {
      if (!keys.has(key))
        errors.push({ file: localeFile(locale), message: `${locale} is missing "${key}"` });
    }
    for (const key of keys.keys()) {
      if (!source.has(key)) {
        errors.push({
          file: localeFile(locale),
          message: `${locale} has "${key}", which en doesn't`,
        });
      }
    }
  }

  const parsed = Object.fromEntries(LOCALES.map((locale) => [locale, new Map()]));
  for (const locale of LOCALES) {
    for (const [key, message] of catalogues[locale]) {
      try {
        if (typeof message !== 'string' || !/\S/.test(message))
          throw new Error('not a non-empty string');
        parsed[locale].set(key, parse(message));
      } catch (error) {
        errors.push({
          file: localeFile(locale),
          message: `${locale} "${key}" isn't valid ICU: ${error.message}`,
        });
      }
    }
  }

  const keep = config.keep ?? {};
  const used = usedKeys(new Set(source.keys()), {
    sourceDirs,
    errorCodes: readErrorCodes(schemaPath),
  });
  for (const key of source.keys()) {
    if (!used.has(key) && !(key in keep)) {
      errors.push({
        file: localeFile(SOURCE_LOCALE),
        message: `"${key}" isn't used by apps/web or apps/mobile; remove it from every locale, or list it under "keep" in catalogue-check.json with a reason`,
      });
    }
  }
  for (const key of Object.keys(keep)) {
    if (!source.has(key))
      errors.push({ file: configPath, message: `"keep" lists "${key}", which en doesn't have` });
    else if (used.has(key))
      warnings.push({ file: configPath, message: `"${key}" is used now; remove it from "keep"` });
  }

  const sameAsEnglish = config.sameAsEnglish ?? {};
  for (const [key, entry] of Object.entries(sameAsEnglish)) {
    if (!source.has(key)) {
      errors.push({
        file: configPath,
        message: `"sameAsEnglish" lists "${key}", which en doesn't have`,
      });
    } else if (
      !Array.isArray(entry?.locales) ||
      entry.locales.length === 0 ||
      !entry.locales.every((locale) => TRANSLATED.includes(locale)) ||
      typeof entry.reason !== 'string' ||
      !/\S/.test(entry.reason)
    ) {
      errors.push({
        file: configPath,
        message: `"sameAsEnglish" entry "${key}" needs locales from ${TRANSLATED.join(', ')} and a reason`,
      });
    }
  }
  for (const locale of TRANSLATED) {
    for (const [key, message] of catalogues[locale]) {
      const english = source.get(key);
      const englishElements = parsed[SOURCE_LOCALE].get(key);
      const allowed = sameAsEnglish[key]?.locales?.includes(locale) ?? false;
      if (
        message === english &&
        englishElements !== undefined &&
        !allowed &&
        !mayMatchEnglish(english, englishElements)
      ) {
        errors.push({
          file: localeFile(locale),
          message: `${locale} "${key}" is the same as en; translate it, or list it under "sameAsEnglish" in catalogue-check.json with a reason`,
        });
      } else if (allowed && english !== undefined && message !== english) {
        warnings.push({
          file: configPath,
          message: `"${key}" differs from en in ${locale} now; remove ${locale} from its "sameAsEnglish" entry`,
        });
      }
    }
  }

  for (const layout of config.layouts ?? []) {
    const matched = [...source.keys()].filter((key) => matchesPattern(layout.pattern, key));
    if (matched.length === 0) {
      warnings.push({ file: configPath, message: `layout "${layout.pattern}" marks no key` });
    }
    const maxRatio = layout.maxRatio ?? DEFAULT_MAX_RATIO;
    for (const key of matched) {
      const english = parsed.en.get(key);
      if (english === undefined) continue;
      const base = visibleLength(english);
      for (const locale of LENGTH_CHECKED) {
        const elements = parsed[locale].get(key);
        if (elements === undefined) continue;
        const length = visibleLength(elements);
        if (base > 0 && length > base * maxRatio) {
          warnings.push({
            file: localeFile(locale),
            message: `${locale} "${key}" is ${length} characters, ${(length / base).toFixed(1)}× en (${base}); the ${layout.pattern} layout allows ${maxRatio}×`,
          });
        }
        if (layout.maxLength !== undefined && length > layout.maxLength) {
          warnings.push({
            file: localeFile(locale),
            message: `${locale} "${key}" is ${length} characters; the ${layout.pattern} layout allows ${layout.maxLength}`,
          });
        }
      }
    }
  }

  return { errors, warnings };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 2) {
    const [flag, value] = [argv[i], argv[i + 1]];
    if (value === undefined) throw new Error(`${flag} needs a value`);
    if (flag === '--locales') options.localesDir = resolve(value);
    else if (flag === '--sources') options.sourceDirs = value.split(',').map((dir) => resolve(dir));
    else if (flag === '--schema') options.schemaPath = resolve(value);
    else if (flag === '--config') options.configPath = resolve(value);
    else throw new Error(`unknown option ${flag}`);
  }
  return options;
}

export function main(argv = process.argv.slice(2)) {
  const { errors, warnings } = checkCatalogues(parseArgs(argv));
  // GitHub Actions turns these lines into annotations on the PR.
  const annotate = process.env.GITHUB_ACTIONS === 'true';
  const root = process.env.GITHUB_WORKSPACE ?? repoDir;
  const print = (level, { file, message }) =>
    console.log(
      annotate ? `::${level} file=${relative(root, file)}::${message}` : `${level}: ${message}`,
    );
  warnings.forEach((w) => print('warning', w));
  errors.forEach((e) => print('error', e));
  console.log(
    `i18n catalogues: ${errors.length} error(s), ${warnings.length} warning(s) across ${LOCALES.join(', ')}`,
  );
  return errors.length > 0 ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
