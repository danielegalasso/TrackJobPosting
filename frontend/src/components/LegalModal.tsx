import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { Modal } from './Modal';

interface LegalModalProps {
  document: 'privacy' | 'terms';
  onClose: () => void;
}

const TITLES = { privacy: 'Privacy Policy', terms: 'Terms and Conditions' } as const;

/**
 * Renders the markdown served from `docs/` — the same text the repository
 * ships, so the modal cannot drift from the document of record.
 *
 * This is a small, deliberately limited renderer for headings, lists,
 * emphasis and inline code. It escapes the source first, so the markdown
 * itself can never inject markup.
 */
function renderMarkdown(source: string): string {
  const escape = (text: string) =>
    text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

  const inline = (text: string) =>
    escape(text)
      .replace(/`([^`]+)`/g, '<code class="rounded bg-gray-100 px-1 text-[11px]">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-gray-800">$1</strong>')
      .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');

  const blocks: string[] = [];
  let list: string[] = [];

  const flush = () => {
    if (list.length) {
      blocks.push(`<ul class="ml-4 list-disc space-y-1">${list.join('')}</ul>`);
      list = [];
    }
  };

  for (const raw of source.split('\n')) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);

    if (bullet) {
      list.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    flush();

    if (!line.trim()) continue;
    if (line.trim() === '---') {
      blocks.push('<hr class="my-4 border-gray-200" />');
    } else if (heading) {
      const level = Math.min(heading[1].length + 1, 5);
      const size = level <= 2 ? 'text-base' : level === 3 ? 'text-sm' : 'text-xs';
      blocks.push(
        `<h${level} class="mt-4 ${size} font-semibold text-gray-900">${inline(heading[2])}</h${level}>`,
      );
    } else if (quote) {
      blocks.push(
        `<blockquote class="border-l-2 border-brand/40 bg-emerald-50/50 py-1 pl-3 text-gray-600">${inline(quote[1])}</blockquote>`,
      );
    } else {
      blocks.push(`<p>${inline(line)}</p>`);
    }
  }
  flush();
  return blocks.join('');
}

export function LegalModal({ document, onClose }: LegalModalProps) {
  const [html, setHtml] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .legal(document)
      .then((text) => {
        if (!cancelled) setHtml(renderMarkdown(text));
      })
      .catch(() => {
        if (!cancelled) setError('This document could not be loaded from the server.');
      });
    return () => {
      cancelled = true;
    };
  }, [document]);

  return (
    <Modal title={TITLES[document]} onClose={onClose} wide>
      {error ? (
        <p className="text-sm text-red-700">{error}</p>
      ) : (
        <div
          className="space-y-2 pr-2 text-xs leading-relaxed text-gray-600"
          // The source is repository-owned markdown, escaped before rendering.
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
    </Modal>
  );
}
