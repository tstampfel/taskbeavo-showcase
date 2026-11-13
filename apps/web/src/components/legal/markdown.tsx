import { Fragment, type ReactNode } from 'react';

/**
 * Renders a legal document's Markdown (as the lawyer wrote it, from the API) without ever
 * passing it through as HTML: headings, paragraphs, lists, rules, bold, italic and links to
 * web, mail or same-site pages. Anything else shows as plain text. Headings start at h2, since
 * the page title is the h1.
 */
export function Markdown({ source }: { source: string }) {
  return <>{blocks(source.replace(/<!--[\s\S]*?-->/g, ''))}</>;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^[-*+]\s+(.*)$/;
const NUMBERED = /^\d+[.)]\s+(.*)$/;
const RULE = /^(?:-{3,}|\*{3,}|_{3,})$/;

function blocks(source: string): ReactNode[] {
  const out: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = () => {
    if (paragraph.length) {
      out.push(
        <p key={out.length}>
          {paragraph.map((line, index) => (
            <Fragment key={index}>
              {index > 0 ? <br /> : null}
              {inline(line)}
            </Fragment>
          ))}
        </p>,
      );
      paragraph = [];
    }
    if (list) {
      const items = list.items.map((item, index) => <li key={index}>{inline(item)}</li>);
      out.push(
        list.ordered ? <ol key={out.length}>{items}</ol> : <ul key={out.length}>{items}</ul>,
      );
      list = null;
    }
  };

  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const level = Math.min(heading[1]!.length + 1, 6);
      const Tag = `h${level}` as 'h2';
      out.push(<Tag key={out.length}>{inline(heading[2]!)}</Tag>);
      continue;
    }
    if (RULE.test(line)) {
      flush();
      out.push(<hr key={out.length} />);
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const ordered = !!numbered;
      if (paragraph.length || (list && list.ordered !== ordered)) flush();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]!);
      continue;
    }
    if (list) flush();
    paragraph.push(line);
  }
  flush();
  return out;
}

const INLINE = /\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const [, bold, italic, label, href] = match;
    const key = out.length;
    if (bold !== undefined) out.push(<strong key={key}>{inline(bold)}</strong>);
    else if (italic !== undefined) out.push(<em key={key}>{inline(italic)}</em>);
    else if (safeHref(href!)) {
      out.push(
        <a key={key} href={href}>
          {inline(label!)}
        </a>,
      );
    } else out.push(<Fragment key={key}>{inline(label!)}</Fragment>);
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function safeHref(href: string): boolean {
  return /^(https?:|mailto:)/i.test(href) || (href.startsWith('/') && !href.startsWith('//'));
}
