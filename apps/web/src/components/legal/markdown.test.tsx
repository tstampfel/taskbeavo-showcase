import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Markdown } from './markdown';

const html = (source: string) => renderToStaticMarkup(<Markdown source={source} />);

describe('Markdown', () => {
  it('renders headings one level below the page title, paragraphs and line breaks', () => {
    expect(html('# 1. What TaskBeavo does\n\nTaskBeavo arranges tasks\nbetween people.')).toBe(
      '<h2>1. What TaskBeavo does</h2><p>TaskBeavo arranges tasks<br/>between people.</p>',
    );
    expect(html('## Sub\n\n### Deep')).toBe('<h3>Sub</h3><h4>Deep</h4>');
  });

  it('renders bulleted and numbered lists', () => {
    expect(html('- one\n- two\n\n1. first\n2. second')).toBe(
      '<ul><li>one</li><li>two</li></ul><ol><li>first</li><li>second</li></ol>',
    );
  });

  it('renders bold, italic and links, allowing only web, mail and relative targets', () => {
    expect(html('**Bold** and *italic* and [site](https://www.maxmind.com).')).toBe(
      '<p><strong>Bold</strong> and <em>italic</em> and <a href="https://www.maxmind.com">site</a>.</p>',
    );
    expect(html('[mail](mailto:info@example.com) [page](/en/cancellation)')).toBe(
      '<p><a href="mailto:info@example.com">mail</a> <a href="/en/cancellation">page</a></p>',
    );
    expect(html('[bad](javascript:alert)')).toBe('<p>bad</p>');
  });

  it('never renders raw HTML and drops HTML comments', () => {
    expect(html('<script>alert(1)</script>\n\n<!-- note -->\nText')).toBe(
      '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p><p>Text</p>',
    );
  });

  it('renders a horizontal rule', () => {
    expect(html('Above\n\n---\n\nBelow')).toBe('<p>Above</p><hr/><p>Below</p>');
  });
});
