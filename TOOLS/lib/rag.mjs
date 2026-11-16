/**
 * rag.mjs — shared tokenizer and BM25 scorer for the local RAG.
 *
 * Deliberately dependency-free. The internal system this framework comes from used
 * SQLite FTS5 through better-sqlite3; that means a native build on every machine,
 * which is the wrong trade for a template meant to be cloned and run anywhere. A
 * corpus of a few thousand markdown chunks scores in milliseconds in plain JS.
 */

const STOPWORDS = new Set([
  // English
  'the', 'and', 'for', 'that', 'with', 'this', 'from', 'are', 'was', 'were', 'has',
  'have', 'had', 'not', 'but', 'can', 'all', 'any', 'its', 'you', 'your', 'our',
  'they', 'their', 'what', 'when', 'which', 'who', 'how', 'into', 'than', 'then',
  'there', 'been', 'will', 'would', 'should', 'could', 'each', 'one', 'two', 'per',
  // French
  'les', 'des', 'une', 'aux', 'par', 'pour', 'dans', 'sur', 'avec', 'sans', 'est',
  'sont', 'etre', 'avoir', 'que', 'qui', 'quoi', 'dont', 'mais', 'donc', 'ainsi',
  'cette', 'ces', 'son', 'ses', 'leur', 'leurs', 'plus', 'moins', 'tout', 'tous',
  'ete', 'fait', 'faire', 'chaque', 'entre', 'apres', 'avant', 'elle', 'nous', 'vous',
]);

/** Lowercase, strip accents, split on non-alphanumerics, drop stopwords and 1-char tokens. */
export function tokenize(text) {
  return String(text)
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .split(/[^a-z0-9_]+/)
    .filter((t) => t.length > 1 && !STOPWORDS.has(t));
}

const K1 = 1.5;
const B = 0.75;

/**
 * Scores every chunk against the query terms.
 * `chunks` carry a precomputed `tf` map and `len`; `df` and `avgdl` come from the index.
 * Returns [{chunk, score}] sorted descending, zero scores dropped.
 */
export function bm25(queryTerms, chunks, df, avgdl, N) {
  const idf = new Map();
  for (const t of new Set(queryTerms)) {
    const n = df[t] ?? 0;
    // Standard BM25 idf, floored at a small positive value so a term present
    // everywhere still contributes a little rather than going negative.
    idf.set(t, Math.max(0.05, Math.log(1 + (N - n + 0.5) / (n + 0.5))));
  }

  const out = [];
  for (const c of chunks) {
    let score = 0;
    for (const [t, w] of idf) {
      const f = c.tf[t];
      if (!f) continue;
      score += w * ((f * (K1 + 1)) / (f + K1 * (1 - B + B * (c.len / avgdl))));
    }
    // A hit in the title is worth more than one in the body: headings are how these
    // documents are addressed, and the query usually echoes one.
    if (score > 0 && c.titleTokens) {
      const inTitle = queryTerms.filter((t) => c.titleTokens.includes(t)).length;
      if (inTitle) score *= 1 + 0.35 * inTitle;
    }
    if (score > 0) out.push({ chunk: c, score });
  }
  return out.sort((a, b) => b.score - a.score);
}

/** The window of `width` characters of `text` densest in query terms. */
export function bestWindow(text, queryTerms, width = 280) {
  if (text.length <= width) return text;
  const lower = text.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  let best = 0, bestHits = -1;
  const step = Math.max(40, Math.floor(width / 4));
  for (let start = 0; start < text.length - width / 2; start += step) {
    const slice = lower.slice(start, start + width);
    let hits = 0;
    for (const t of queryTerms) if (slice.includes(t)) hits++;
    if (hits > bestHits) { bestHits = hits; best = start; }
  }
  // Do not cut mid-word.
  let from = best === 0 ? 0 : text.indexOf(' ', best) + 1;
  if (from <= 0) from = best;
  const snippet = text.slice(from, from + width).trim();
  return (from > 0 ? '…' : '') + snippet + (from + width < text.length ? '…' : '');
}
