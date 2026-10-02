// Drift check for frontend i18n (#308): every user-visible string literal must
// come from the catalogue, so a new page cannot quietly reintroduce a hardcoded
// English string. The tree is not converted yet, so this is a *ratchet*: the
// per-file counts in `ui-string-baseline.json` may only go down. Converting a
// file means lowering (or removing) its entry in the same PR; adding a literal
// to an already-counted file fails the check. Same shape as the mypy `files`
// ratchet in `pyproject.toml`, and the same reason: a ratchet that can grow
// silently is not a gate.
//
// Heuristic on purpose. It over-counts (a string that is really a message key
// still counts) rather than under-counts, because a missed literal is a
// half-translated page while a surplus count is a one-line baseline bump.
//
// One exception is not heuristic: a literal that is *exactly* a key in the
// catalogue is never copy. A page can hold its keys in a data model (the
// sidebar's nav list) rather than inline in a `t(...)` call, and counting those
// would make a fully-converted file look unconverted.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, "..");
const SRC = join(FRONTEND, "src");
const BASELINE_PATH = join(HERE, "ui-string-baseline.json");
const CATALOGUE_PATH = join(FRONTEND, "src", "lib", "i18n", "en.json");

const USER_FACING_ATTRS = new Set(["aria-label", "aria-description", "placeholder", "title", "alt"]);
const NON_UI_ATTRS = new Set([
  "className", "style", "href", "id", "type", "name", "key", "role", "target", "rel",
  "variant", "size", "value", "src", "sizes", "as", "htmlFor", "width", "height", "fill",
  "stroke", "viewBox", "d", "transform", "method", "action", "autoComplete", "inputMode",
  "pattern", "accept", "min", "max", "step", "lang", "dir", "color", "download",
]);
const NON_UI_PROPS = new Set([
  "href", "className", "style", "id", "key", "role", "src", "sizes", "type", "name",
  "method", "action", "target", "rel", "value", "variant", "as", "htmlFor", "strategy",
  "displayName", "placement", "side", "align", "orientation", "aria-hidden",
]);
// Class-name helpers: their string arguments are Tailwind classes, not copy.
const CLASS_HELPERS = new Set(["cn", "clsx", "cva", "twMerge", "classNames", "tv"]);
// `t("settings.title")` / `translate("...")` — the string is a catalogue key.
const TRANSLATION_CALLS = new Set(["t", "translate", "tRich"]);

// Catalogue keys, so a key held in a data model is not mistaken for copy.
function catalogueKeys() {
  try {
    return new Set(Object.keys(JSON.parse(readFileSync(CATALOGUE_PATH, "utf8"))));
  } catch {
    return new Set();
  }
}
const CATALOGUE_KEYS = catalogueKeys();

function isUrlLike(text) {
  return (
    text.startsWith("/") || text.startsWith("http") || text.includes("://") ||
    text.startsWith("#") || text.startsWith("data:") || text.startsWith(".")
  );
}

// Tailwind class strings are multi-token and most tokens carry a `-`, `/`, `.`
// or digit. Prose does not. "Server access token" stays; "flex flex-col gap-2"
// goes. Imperfect at the edges, deterministic always.
function looksLikeClassList(text) {
  const tokens = text.trim().split(/\s+/);
  if (tokens.length < 2) return false;
  const classy = tokens.filter((t) => /[-/.\d[\]:%]/.test(t)).length;
  return classy * 2 >= tokens.length;
}

function looksUserFacing(text) {
  const t = text.trim();
  if (!/[A-Za-z]/.test(t)) return false;
  if (t === "use client" || t === "use server") return false;
  if (isUrlLike(t)) return false;
  // A single lowercase token is a class, variant or enum value, not prose.
  if (/^[a-z][a-z0-9_-]*$/.test(t)) return false;
  if (looksLikeClassList(t)) return false;
  return true;
}

function moduleSpecifierParent(node) {
  const p = node.parent;
  if (!p) return false;
  if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isImportTypeNode(p)) return true;
  if (ts.isExternalModuleReference(p)) return true;
  if (ts.isCallExpression(p)) {
    const callee = p.expression;
    if (callee.kind === ts.SyntaxKind.ImportKeyword) return true;
    if (ts.isIdentifier(callee) && callee.text === "require") return true;
  }
  return false;
}

function classHelperAncestor(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isCallExpression(p) && ts.isIdentifier(p.expression)) {
      if (CLASS_HELPERS.has(p.expression.text)) return true;
      // A catalogue key passed to `t(...)` is not copy in the component.
      if (TRANSLATION_CALLS.has(p.expression.text)) return true;
    }
    // Do not climb out of the enclosing statement/JSX element.
    if (ts.isStatement(p) || ts.isJsxElement(p) || ts.isJsxSelfClosingElement(p)) return false;
  }
  return false;
}

function enclosingName(node) {
  const p = node.parent;
  if (!p) return null;
  if (ts.isJsxAttribute(p) && p.name && ts.isIdentifier(p.name)) return { jsx: p.name.text };
  if (ts.isPropertyAssignment(p) && p.name && ts.isIdentifier(p.name)) return { prop: p.name.text };
  return null;
}

// `Component.displayName = "Component"` — a debug label, not copy.
function isDisplayNameAssignment(node) {
  const p = node.parent;
  return (
    !!p && ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
    ts.isPropertyAccessExpression(p.left) && p.left.name.text === "displayName"
  );
}

export function scanFile(filePath) {
  const source = readFileSync(filePath, "utf8");
  const sf = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hits = [];

  // `aria-hidden="true"` marks a decorative subtree: a glyph or spacer, not
  // copy. Skipping it keeps the "x" in a dialog's close button out of the
  // catalogue without a per-file exemption.
  function isAriaHidden(node) {
    const attrs = ts.isJsxElement(node)
      ? node.openingElement.attributes
      : ts.isJsxSelfClosingElement(node)
        ? node.attributes
        : null;
    if (!attrs) return false;
    return attrs.properties.some(
      (p) =>
        ts.isJsxAttribute(p) && ts.isIdentifier(p.name) && p.name.text === "aria-hidden" &&
        p.initializer && ts.isStringLiteral(p.initializer) && p.initializer.text === "true"
    );
  }

  const visit = (node) => {
    if ((ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) && isAriaHidden(node)) return;
    if (ts.isJsxText(node)) {
      const text = node.text.replace(/\s+/g, " ").trim();
      if (/[A-Za-z]/.test(text)) hits.push(text);
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const name = enclosingName(node);
      // A catalogue key is not copy — but only where it is *addressed* as a key
      // (a `t(...)` argument, or a key held in a data model). A key literal in a
      // user-facing attribute is the opposite: `aria-label="header.action.help"`
      // renders the key to a screen reader, which is exactly the half-translated
      // page the gate exists to catch, so the exemption must not reach there.
      const inUserFacingAttr = !!(name && name.jsx && USER_FACING_ATTRS.has(name.jsx));
      if (!inUserFacingAttr && CATALOGUE_KEYS.has(node.text)) {
        ts.forEachChild(node, visit);
        return;
      }
      if (isDisplayNameAssignment(node)) {
        ts.forEachChild(node, visit);
        return;
      }
      if (name && name.jsx) {
        if (USER_FACING_ATTRS.has(name.jsx)) hits.push(node.text);
        else if (!NON_UI_ATTRS.has(name.jsx) && looksUserFacing(node.text)) hits.push(node.text);
      } else if (name && name.prop) {
        if (!NON_UI_PROPS.has(name.prop) && looksUserFacing(node.text)) hits.push(node.text);
      } else if (!moduleSpecifierParent(node) && !classHelperAncestor(node) && looksUserFacing(node.text)) {
        hits.push(node.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return hits;
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx$/.test(path) && !/\.test\.tsx$/.test(path) && !/\.d\.ts$/.test(path)) out.push(path);
  }
  return out;
}

export function collectCounts() {
  const counts = {};
  for (const file of walk(SRC)) {
    const hits = scanFile(file).length;
    if (hits > 0) counts[relative(FRONTEND, file).split("\\").join("/")] = hits;
  }
  return counts;
}

function main() {
  const counts = collectCounts();
  if (process.argv.includes("--update")) {
    const sorted = Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
    writeFileSync(BASELINE_PATH, JSON.stringify(sorted, null, 2) + "\n");
    console.log(`wrote ${Object.keys(sorted).length} entries to ${relative(process.cwd(), BASELINE_PATH)}`);
    return;
  }

  let baseline;
  try {
    baseline = JSON.parse(readFileSync(BASELINE_PATH, "utf8"));
  } catch {
    console.error(`Missing ${BASELINE_PATH}. Generate it with: node scripts/check-ui-strings.mjs --update`);
    process.exit(1);
  }

  const regressions = [];
  const stale = [];
  for (const [file, count] of Object.entries(counts)) {
    const allowed = baseline[file] ?? 0;
    if (count > allowed) regressions.push({ file, count, allowed });
  }
  for (const [file, allowed] of Object.entries(baseline)) {
    const count = counts[file] ?? 0;
    if (count < allowed) stale.push({ file, count, allowed });
  }

  if (regressions.length || stale.length) {
    for (const r of regressions) {
      console.error(`  +${r.count - r.allowed} literal(s) in ${r.file} (baseline ${r.allowed}) — move them into the catalogue (src/lib/i18n/en.json) or, if the count is a false positive, run --update.`);
    }
    for (const s of stale) {
      console.error(`  ${s.file} dropped ${s.allowed - s.count} literal(s) (baseline ${s.allowed}) — tighten the ratchet with: node scripts/check-ui-strings.mjs --update`);
    }
    console.error(`\nUI string drift: ${regressions.length} file(s) grew, ${stale.length} file(s) shrank.`);
    process.exit(1);
  }

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  console.log(`UI string ratchet: ${Object.keys(counts).length} files, ${total} literals still outside the catalogue.`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
