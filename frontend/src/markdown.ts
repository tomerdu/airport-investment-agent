/**
 * Minimal, escaping-first markdown renderer.
 *
 * The model's answer is narration and may contain anything, so the input is
 * HTML-escaped FIRST and only a fixed set of tags is then reintroduced:
 * headings, paragraphs, lists, tables, bold, italic and inline code. No raw
 * HTML from the model ever reaches the DOM, and there are no links or images,
 * so there is no injection surface.
 *
 * Kept local rather than pulling in a markdown + sanitiser dependency pair for
 * the handful of constructs the answers actually use.
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((c) => c.trim());
}

const isDivider = (line: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line);

export function renderMarkdown(markdown: string): string {
  const lines = escapeHtml(markdown ?? '').split('\n');
  const out: string[] = [];
  let listType: 'ul' | 'ol' | null = null;

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // table: header row followed by a divider row
    if (line.includes('|') && i + 1 < lines.length && isDivider(lines[i + 1])) {
      closeList();
      const headers = splitRow(line);
      out.push('<table><thead><tr>');
      headers.forEach((h) => out.push(`<th>${inline(h)}</th>`));
      out.push('</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && lines[i].includes('|')) {
        out.push('<tr>');
        splitRow(lines[i]).forEach((c) => out.push(`<td>${inline(c)}</td>`));
        out.push('</tr>');
        i++;
      }
      i--;
      out.push('</tbody></table>');
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(6, Math.max(3, heading[1].length + 1));
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (listType !== 'ul') {
        closeList();
        out.push('<ul>');
        listType = 'ul';
      }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }

    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      if (listType !== 'ol') {
        closeList();
        out.push('<ol>');
        listType = 'ol';
      }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }

    if (!line.trim()) {
      closeList();
      continue;
    }

    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }

  closeList();
  return out.join('');
}
