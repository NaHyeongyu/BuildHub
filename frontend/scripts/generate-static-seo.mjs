import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const distDirectory = path.join(frontendDirectory, "dist");
const productionOrigin = "https://promty.org";
const pages = JSON.parse(
  await readFile(path.join(frontendDirectory, "seo-pages.json"), "utf8"),
);
const template = await readFile(path.join(distDirectory, "index.html"), "utf8");

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapePattern(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function replaceElement(html, pattern, replacement, label) {
  if (!pattern.test(html)) {
    throw new Error(`Unable to find ${label} in the Vite HTML output.`);
  }
  return html.replace(pattern, replacement);
}

function replaceMeta(html, attribute, value, content) {
  const pattern = new RegExp(
    `<meta\\s+[^>]*${attribute}=["']${escapePattern(value)}["'][^>]*>`,
    "i",
  );
  return replaceElement(
    html,
    pattern,
    `<meta ${attribute}="${escapeHtml(value)}" content="${escapeHtml(content)}" />`,
    `${attribute}=${value}`,
  );
}

function renderFallback(page) {
  const highlights = page.highlights.length
    ? `<ul>${page.highlights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "";
  const nav = pages
    .filter((candidate) => candidate.index)
    .slice(0, 4)
    .map(
      (candidate) =>
        `<a href="${escapeHtml(candidate.path)}">${escapeHtml(candidate.route === "landing" ? "Home" : candidate.heading)}</a>`,
    )
    .join("");

  return `<div id="root"><main class="seo-static-shell"><nav aria-label="Primary"><a class="seo-static-brand" href="/">Promty</a><div>${nav}</div></nav><article><span>${escapeHtml(page.eyebrow)}</span><h1>${escapeHtml(page.heading)}</h1><p>${escapeHtml(page.summary)}</p>${highlights}<a class="seo-static-cta" href="${page.index ? "/app" : "/"}">${page.index ? "Open Promty" : "Return to Promty"}</a></article><footer>Project Memory for continuous AI-assisted development.</footer></main></div>`;
}

function renderStructuredData(page, canonicalUrl) {
  const data = {
    "@context": "https://schema.org",
    "@type": page.route === "landing" ? "WebSite" : "WebPage",
    name: page.title,
    description: page.description,
    url: canonicalUrl,
    isPartOf:
      page.route === "landing"
        ? undefined
        : {
            "@type": "WebSite",
            name: "Promty",
            url: `${productionOrigin}/`,
          },
  };
  return `<script type="application/ld+json">${JSON.stringify(data).replaceAll("<", "\\u003c")}</script>`;
}

function renderPage(page) {
  const canonicalPath = page.canonicalPath ?? page.path;
  const canonicalUrl = new URL(canonicalPath, productionOrigin).href;
  const imageUrl = new URL(page.image, productionOrigin).href;
  const robots = page.index
    ? "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
    : "noindex, nofollow, noarchive";
  let html = template;

  html = replaceElement(
    html,
    /<title>[\s\S]*?<\/title>/i,
    `<title>${escapeHtml(page.title)}</title>`,
    "document title",
  );
  html = replaceMeta(html, "name", "description", page.description);
  html = replaceMeta(html, "name", "robots", robots);
  html = replaceMeta(html, "property", "og:title", page.title);
  html = replaceMeta(html, "property", "og:description", page.description);
  html = replaceMeta(html, "property", "og:url", canonicalUrl);
  html = replaceMeta(html, "property", "og:image", imageUrl);
  html = replaceMeta(html, "name", "twitter:title", page.title);
  html = replaceMeta(html, "name", "twitter:description", page.description);
  html = replaceMeta(html, "name", "twitter:image", imageUrl);
  html = replaceElement(
    html,
    /<link\s+[^>]*rel=["']canonical["'][^>]*>/i,
    `<link rel="canonical" href="${escapeHtml(canonicalUrl)}" />`,
    "canonical link",
  );
  html = replaceElement(
    html,
    /<div\s+id=["']root["']><\/div>/i,
    renderFallback(page),
    "root element",
  );
  html = replaceElement(
    html,
    /<\/head>/i,
    `<style>.seo-static-shell{box-sizing:border-box;min-height:100vh;padding:28px 5vw 48px;background:#09090b;color:#f5f5f4;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.seo-static-shell *{box-sizing:border-box}.seo-static-shell nav{display:flex;align-items:center;justify-content:space-between;gap:24px;max-width:1120px;margin:0 auto}.seo-static-shell nav div{display:flex;gap:20px}.seo-static-shell a{color:inherit;text-decoration:none}.seo-static-brand{font-size:20px;font-weight:760}.seo-static-shell nav div a{color:#a1a1aa;font-size:13px}.seo-static-shell article{max-width:820px;margin:clamp(88px,14vw,180px) auto}.seo-static-shell article>span{color:#a8a29e;font-size:12px;font-weight:700;letter-spacing:.14em}.seo-static-shell h1{max-width:780px;margin:20px 0;font-size:clamp(42px,7vw,82px);line-height:1.02;letter-spacing:-.055em}.seo-static-shell p{max-width:680px;color:#c4c4c8;font-size:clamp(17px,2vw,21px);line-height:1.65}.seo-static-shell ul{display:grid;gap:10px;margin:28px 0;padding-left:20px;color:#d6d3d1;line-height:1.55}.seo-static-cta{display:inline-flex;margin-top:24px;padding:12px 18px;border-radius:8px;background:#f5f5f4;color:#18181b!important;font-size:14px;font-weight:700}.seo-static-shell footer{max-width:1120px;margin:0 auto;color:#71717a;font-size:12px}@media(max-width:720px){.seo-static-shell nav div{display:none}.seo-static-shell article{margin-top:80px}}</style>${renderStructuredData(page, canonicalUrl)}</head>`,
    "closing head tag",
  );

  return html;
}

for (const page of pages) {
  if (!page.path.startsWith("/") || page.path.includes("..")) {
    throw new Error(`Unsafe SEO path: ${page.path}`);
  }
  const outputPath =
    page.path === "/"
      ? path.join(distDirectory, "index.html")
      : path.join(distDirectory, page.path.slice(1), "index.html");
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, renderPage(page));
}

console.log(`static_seo_pages=${pages.length}`);
