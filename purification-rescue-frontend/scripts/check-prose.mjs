// Fails if the production CSS lacks the @tailwindcss/typography `prose` styles.
//
// ProtocolViewer renders the synthesized protocol Markdown inside `prose` classes. If
// the plugin stops loading (in Tailwind 4 it is loaded by `@plugin` in src/app.css, not
// by a config file), the build still succeeds but the protocol renders unstyled. That
// is what broke the February 2026 Svelte 5 attempt, so CI checks the built CSS for it.
// Run after `npm run build`.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const assets = join(import.meta.dirname, '..', 'dist', 'assets');

let css;
try {
  css = readdirSync(assets)
    .filter((name) => name.endsWith('.css'))
    .map((name) => readFileSync(join(assets, name), 'utf8'))
    .join('\n');
} catch {
  console.error(`check-prose: no build output in ${assets}. Run \`npm run build\` first.`);
  process.exit(1);
}

// Selector prefixes the plugin emits for the elements the protocol uses.
const required = {
  'the .prose base rule': /\.prose\s*\{[^}]*--tw-prose-body/,
  'the prose-slate color theme': /\.prose-slate\s*\{/,
  headings: /\.prose\s*:where\(h2\)/,
  lists: /\.prose\s*:where\(ol\)/,
  tables: /\.prose\s*:where\(table\)/,
};

const missing = Object.entries(required)
  .filter(([, pattern]) => !pattern.test(css))
  .map(([label]) => label);

if (missing.length) {
  console.error('check-prose: the built CSS is missing @tailwindcss/typography styles:');
  for (const label of missing) console.error(`  - ${label}`);
  console.error("Check that src/app.css still has `@plugin '@tailwindcss/typography';`.");
  process.exit(1);
}

console.log('check-prose: typography (prose) styles present in the built CSS.');
