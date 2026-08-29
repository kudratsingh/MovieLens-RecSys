// Renders every docs/diagrams/src/*.mmd to a committed light/dark SVG pair.
//
// Mermaid is a browser library, so it needs a DOM. Rather than pull in
// mermaid-cli (which drags its own Puppeteer and a second Chromium download
// alongside the Playwright one this repo already installs), this drives the
// Playwright Chromium that the e2e suites use. The mermaid ESM bundle loads
// its own lazy chunks, so the page is served over a throwaway loopback HTTP
// server rooted at the installed package rather than injected into
// about:blank, where those relative imports would have nothing to resolve
// against.
//
// The output is committed, so it has to be byte-stable: same source in, same
// bytes out, or `make diagrams` becomes a permanent diff. Everything mermaid
// derives from a run-scoped id is normalised below.

import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..");
const sourceDirectory = join(repoRoot, "docs", "diagrams", "src");
const outputDirectory = join(repoRoot, "docs", "diagrams");
const mermaidDistribution = join(webRoot, "node_modules", "mermaid", "dist");

// The product's own tokens, read across from web/app/globals.css so a diagram
// and a screenshot of the app look like one system. Light mode is a derived
// palette rather than a second set of product tokens: the app ships dark only,
// but a README is read in both, and a coral that passes on #0b0a09 does not
// pass on white.
const THEMES = {
  light: {
    background: "#ffffff",
    surface: "#f7f4f0",
    surfaceAlt: "#efe9e1",
    border: "#cfc6b9",
    borderStrong: "#8d8375",
    text: "#171411",
    textMuted: "#5f584f",
    accent: "#b8452a",
    accentSurface: "#fbe9e3",
  },
  dark: {
    background: "#0b0a09",
    surface: "#1a1815",
    surfaceAlt: "#221f1b",
    // Deliberately lighter than the product's --border-subtle. On a page the
    // eye has other cues for where a card ends; a diagram has only the stroke,
    // and #2b2823 on #0b0a09 leaves the boxes floating.
    border: "#494236",
    borderStrong: "#7b7264",
    text: "#f4ede3",
    textMuted: "#bdb3a6",
    accent: "#ee7657",
    accentSurface: "#3a211a",
  },
};

const FONT_FAMILY =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

function mermaidConfig(theme) {
  const palette = THEMES[theme];
  return {
    theme: "base",
    // Strict keeps mermaid from honouring any HTML in a label, which is what
    // we want anyway: GitHub's image proxy renders foreignObject unreliably,
    // so every label has to end up as a real SVG <text>.
    securityLevel: "strict",
    startOnLoad: false,
    fontFamily: FONT_FAMILY,
    // The committed SVGs have to be byte-stable. Mermaid draws node and label
    // outlines through rough.js even under the classic look, and rough.js
    // seeds itself randomly unless told otherwise — which moves every bezier
    // control point a fraction of a pixel on each run.
    deterministicIds: true,
    deterministicIDSeed: "movielens-recsys",
    handDrawnSeed: 1,
    // Set at both levels on purpose. The flowchart renderer reads its own key;
    // the shared label helper reads the top-level one, and leaving that true
    // puts foreignObject back into the output through the side door.
    htmlLabels: false,
    flowchart: { htmlLabels: false, useMaxWidth: false, curve: "basis", padding: 14 },
    // messageMargin is up from the default 35: a two-line self-message would
    // otherwise have its second line sitting on the loop arrow.
    sequence: {
      useMaxWidth: false,
      // Auto-wrap off so line breaks are the source's decision. Mermaid's own
      // wrap point lands mid-phrase often enough that a self-message's second
      // line ends up sitting on its loop arrow.
      wrap: false,
      messageMargin: 48,
      boxMargin: 12,
      actorFontFamily: FONT_FAMILY,
      noteFontFamily: FONT_FAMILY,
      messageFontFamily: FONT_FAMILY,
    },
    // LR, so ranks run left to right and the entities stack downwards. The
    // default TB puts every child of `tenants` in one row, which makes a
    // fifteen-entity model four times wider than it is tall — unreadable at
    // any width a README will give it.
    er: { useMaxWidth: false, fontSize: 12, layoutDirection: "LR" },
    themeVariables: {
      fontFamily: FONT_FAMILY,
      fontSize: "15px",
      background: palette.background,
      primaryColor: palette.surface,
      primaryTextColor: palette.text,
      primaryBorderColor: palette.border,
      secondaryColor: palette.surfaceAlt,
      secondaryTextColor: palette.text,
      secondaryBorderColor: palette.border,
      tertiaryColor: palette.surfaceAlt,
      tertiaryTextColor: palette.text,
      tertiaryBorderColor: palette.border,
      lineColor: palette.borderStrong,
      textColor: palette.text,
      mainBkg: palette.surface,
      nodeBorder: palette.border,
      nodeTextColor: palette.text,
      clusterBkg: palette.background,
      clusterBorder: palette.border,
      titleColor: palette.text,
      edgeLabelBackground: palette.background,
      // Sequence diagram
      actorBkg: palette.surface,
      actorBorder: palette.borderStrong,
      actorTextColor: palette.text,
      actorLineColor: palette.borderStrong,
      signalColor: palette.text,
      signalTextColor: palette.text,
      labelBoxBkgColor: palette.accentSurface,
      labelBoxBorderColor: palette.accent,
      labelTextColor: palette.text,
      loopTextColor: palette.text,
      noteBkgColor: palette.accentSurface,
      noteBorderColor: palette.accent,
      noteTextColor: palette.text,
      activationBkgColor: palette.accentSurface,
      activationBorderColor: palette.accent,
      sequenceNumberColor: palette.background,
      // ER diagram. rowOdd/rowEven are the variables the base theme actually
      // reads; left unset it derives them by lightening mainBkg, which paints
      // every other attribute row near-white under the dark palette and hides
      // the light text on it. The legacy names are set too, harmlessly.
      rowOdd: palette.surface,
      rowEven: palette.surfaceAlt,
      attributeBackgroundColorOdd: palette.surface,
      attributeBackgroundColorEven: palette.surfaceAlt,
    },
  };
}

const CONTENT_TYPES = {
  ".mjs": "text/javascript; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".map": "application/json; charset=utf-8",
};

const HOST_PAGE = `<!doctype html>
<html><head><meta charset="utf-8"><title>diagram host</title></head>
<body><div id="container"></div>
<script type="module">
import mermaid from "./mermaid.esm.min.mjs";
window.mermaid = mermaid;
window.__mermaidReady = true;
</script>
</body></html>`;

async function startHostServer() {
  const server = createServer((request, response) => {
    const path = (request.url ?? "/").split("?")[0];
    if (path === "/" || path === "/index.html") {
      response.writeHead(200, { "content-type": CONTENT_TYPES[".html"] });
      response.end(HOST_PAGE);
      return;
    }
    // Path traversal cannot escape the package directory: resolve() collapses
    // the "..", and anything landing outside is refused rather than served.
    const filePath = resolve(mermaidDistribution, `.${path}`);
    if (!filePath.startsWith(mermaidDistribution)) {
      response.writeHead(403).end();
      return;
    }
    const stream = createReadStream(filePath);
    stream.on("error", () => response.writeHead(404).end());
    stream.on("open", () => {
      response.writeHead(200, {
        "content-type": CONTENT_TYPES[extname(filePath)] ?? "application/octet-stream",
      });
      stream.pipe(response);
    });
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const { port } = server.address();
  return { server, origin: `http://127.0.0.1:${port}` };
}

// mermaid derives element ids, marker ids and CSS selectors from the id handed
// to render(), and appends a per-call counter to a few of them. A fixed id per
// diagram removes most of it; these two passes remove the rest, so re-rendering
// an unchanged source is a no-op in `git status`.
function stabiliseIdentifiers(svg, slug) {
  return svg
    .replace(/mermaid-\d+/g, `mermaid-${slug}`)
    .replace(/(id="|url\(#|href="#)([A-Za-z][\w-]*?)-\d{4,}/g, `$1$2-${slug}`);
}

function paintBackground(svg, theme) {
  const palette = THEMES[theme];
  const viewBox = svg.match(/viewBox="([-\d.\s]+)"/);
  if (!viewBox) {
    throw new Error("rendered SVG has no viewBox");
  }
  const [minX, minY, width, height] = viewBox[1].trim().split(/\s+/).map(Number);
  // A transparent SVG shows the reader's own page colour through the diagram,
  // which is exactly wrong when the light file is being viewed in a dark
  // context (a social card, an external viewer). The bleed covers the
  // half-pixel seam that rounding leaves at the edges.
  const bleed = 1;
  const rect =
    `<rect x="${minX - bleed}" y="${minY - bleed}" ` +
    `width="${width + bleed * 2}" height="${height + bleed * 2}" fill="${palette.background}"/>`;
  return svg.replace(/(<svg\b[^>]*>)/, `$1${rect}`);
}

function normaliseSizing(svg) {
  return svg.replace(/(<svg\b[^>]*?)>/, (match, head) => {
    const viewBox = head.match(/viewBox="([-\d.\s]+)"/);
    const [, , width, height] = viewBox[1].trim().split(/\s+/).map(Number);
    const cleaned = head
      .replace(/\s(width|height)="[^"]*"/g, "")
      .replace(/\sstyle="[^"]*"/g, "")
      .replace(/\spreserveAspectRatio="[^"]*"/g, "");
    // Intrinsic width/height plus a viewBox is what lets an <img width="100%">
    // scale the diagram while keeping its aspect ratio. mermaid's own inline
    // `max-width` would cap it well below the README's column.
    return (
      `${cleaned} width="${Math.round(width)}" height="${Math.round(height)}"` +
      ` preserveAspectRatio="xMidYMid meet">`
    );
  });
}

async function renderOne(page, { slug, source, theme }) {
  const svg = await page.evaluate(
    async ([code, config, id]) => {
      window.mermaid.initialize(config);
      const { svg: rendered } = await window.mermaid.render(id, code);
      return rendered;
    },
    [source, mermaidConfig(theme), `d-${slug}`],
  );
  // The ER crow's-foot markers hardcode fill="white" for the "zero" ring,
  // ignoring the theme. It is meant to read as hollow, so give it the page's
  // own colour rather than a bright disc on a dark ground.
  let output = svg.replaceAll('fill="white"', `fill="${THEMES[theme].background}"`);
  output = stabiliseIdentifiers(output, slug);
  output = normaliseSizing(output);
  output = paintBackground(output, theme);
  return `${output.trim()}\n`;
}

async function main() {
  const { server, origin } = await startHostServer();
  const browser = await chromium.launch();
  let failures = 0;
  try {
    const page = await browser.newPage();
    await page.goto(`${origin}/index.html`);
    await page.waitForFunction(() => window.__mermaidReady === true);

    await mkdir(outputDirectory, { recursive: true });
    const sources = (await readdir(sourceDirectory)).filter((name) => name.endsWith(".mmd")).sort();
    if (sources.length === 0) {
      throw new Error(`no .mmd sources in ${sourceDirectory}`);
    }

    for (const name of sources) {
      const slug = name.replace(/\.mmd$/, "");
      const source = await readFile(join(sourceDirectory, name), "utf8");
      for (const theme of ["light", "dark"]) {
        const suffix = theme === "dark" ? ".dark.svg" : ".svg";
        const target = join(outputDirectory, `${slug}${suffix}`);
        try {
          await writeFile(target, await renderOne(page, { slug, source, theme }), "utf8");
        } catch (error) {
          failures += 1;
          console.error(`FAIL ${slug} (${theme}): ${error.message}`);
          continue;
        }
      }
      console.log(`rendered ${slug}`);
    }
  } finally {
    await browser.close();
    await new Promise((done) => server.close(done));
  }
  if (failures > 0) {
    throw new Error(`${failures} diagram render(s) failed`);
  }
}

await main();
