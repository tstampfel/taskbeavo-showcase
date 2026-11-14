import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { cssVariables } from '@taskbeavo/ui-tokens';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Badge, Button, Card, Input, Sheet } from './index';

const html = (node: ReactElement) => renderToStaticMarkup(node);

describe('Button', () => {
  it('renders the caller’s label as a non-submitting button', () => {
    const markup = html(<Button>Pošalji ponudu</Button>);
    expect(markup).toMatch(/^<button type="button"[^>]*>Pošalji ponudu<\/button>$/);
  });

  it('can submit a form and be disabled', () => {
    const markup = html(
      <Button type="submit" disabled>
        Senden
      </Button>,
    );
    expect(markup).toContain('type="submit"');
    expect(markup).toContain('disabled=""');
  });
});

describe('Input', () => {
  it('labels the field', () => {
    const markup = html(<Input id="email" label="E-Mail" />);
    expect(markup).toContain('<label for="email"');
    expect(markup).toContain('>E-Mail</label>');
    expect(markup).toContain('id="email"');
    expect(markup).not.toContain('aria-invalid');
    expect(markup).not.toContain('aria-describedby');
  });

  it('links the hint and the error to the field and marks it invalid', () => {
    const markup = html(
      <Input id="price" label="Prix" hint="En euros" error="Saisissez un prix" />,
    );
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain('aria-describedby="price-error price-hint"');
    expect(markup).toContain('<p id="price-hint"');
    expect(markup).toMatch(/<p id="price-error"[^>]*role="alert"[^>]*>Saisissez un prix<\/p>/);
  });

  it('generates an id when none is given', () => {
    const markup = html(<Input label="Ime" />);
    const id = /<label for="([^"]+)"/.exec(markup)?.[1];
    expect(id).toBeTruthy();
    expect(markup).toContain(`id="${id}"`);
  });
});

describe('Card and Badge', () => {
  it('render their children', () => {
    expect(html(<Card data-testid="c">Zadatak</Card>)).toMatch(
      /^<div[^>]*data-testid="c"[^>]*>Zadatak<\/div>$/,
    );
    expect(html(<Badge tone="green">Plaćeno</Badge>)).toMatch(/^<span[^>]*>Plaćeno<\/span>$/);
  });
});

describe('Sheet', () => {
  it('is a dialog named by its title, with a labelled close button', () => {
    const markup = html(
      <Sheet open={false} onClose={() => {}} title="Filter" closeLabel="Schließen">
        <p>Inhalt</p>
      </Sheet>,
    );
    const titleId = /<h2 id="([^"]+)"/.exec(markup)?.[1];
    expect(titleId).toBeTruthy();
    expect(markup).toMatch(new RegExp(`^<dialog[^>]*aria-labelledby="${titleId}"`));
    expect(markup).toMatch(/<button type="button"[^>]*aria-label="Schließen"/);
    expect(markup).toContain('<span aria-hidden="true">×</span>');
    expect(markup).toContain('<p>Inhalt</p>');
    // Opening is done by showModal() in the browser, never by the static `open` attribute.
    expect(markup).not.toMatch(/<dialog[^>]* open/);
  });
});

describe('component styles', () => {
  const dir = import.meta.dirname;
  const sheets = readdirSync(dir).filter((file) => file.endsWith('.module.css'));
  const defined = new Set(Object.keys(cssVariables()));

  it('exist for every component', () => {
    expect(sheets.sort()).toEqual([
      'avatar.module.css',
      'badge.module.css',
      'button.module.css',
      'card.module.css',
      'input.module.css',
      'sheet.module.css',
    ]);
  });

  it.each(sheets)('%s takes colours only from ui-tokens', (file) => {
    const css = readFileSync(join(dir, file), 'utf8');
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    const used = [...css.matchAll(/var\((--tb-[a-z0-9-]+)/g)].map((m) => m[1]!);
    expect(used.length).toBeGreaterThan(0);
    for (const name of used) expect(defined, `${file} uses ${name}`).toContain(name);
  });
});
