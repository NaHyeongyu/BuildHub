import { describe, expect, it } from "vitest";
import indexHtml from "../index.html?raw";
import seoPages from "../seo-pages.json";
import robotsTxt from "../public/robots.txt?raw";
import siteManifest from "../public/site.webmanifest?raw";
import sitemapXml from "../public/sitemap.xml?raw";
import generatorSource from "../scripts/generate-static-seo.mjs?raw";
import routeSeoSource from "./seo.ts?raw";

const productionOrigin = "https://promty.org";
const description =
  "Promty captures completed AI coding work and compiles it into durable, reviewable Project Memory for the next human or coding agent.";

describe("static search metadata", () => {
  it("publishes one consistent canonical identity and share image", () => {
    expect(indexHtml).toContain(
      "<title>Promty — Project Memory for AI-Assisted Development</title>",
    );
    expect(indexHtml).toContain(
      `<link rel="canonical" href="${productionOrigin}/" />`,
    );
    expect(indexHtml).toContain(`name="description"\n      content="${description}"`);
    expect(indexHtml).toContain(
      `<meta property="og:url" content="${productionOrigin}/" />`,
    );
    expect(indexHtml).toContain(
      `property="og:image"\n      content="${productionOrigin}/marketing/promty-product-memory.png"`,
    );
    expect(indexHtml).toContain(
      `name="twitter:image"\n      content="${productionOrigin}/marketing/promty-product-memory.png"`,
    );
    expect(indexHtml).toContain('name="twitter:card" content="summary_large_image"');
  });

  it("points crawlers at the production sitemap and keeps private routes out", () => {
    expect(robotsTxt).toContain(`Sitemap: ${productionOrigin}/sitemap.xml`);
    expect(robotsTxt).toContain("Disallow: /admin");
    expect(robotsTxt).toContain("Disallow: /app");
    expect(sitemapXml).toContain(`<loc>${productionOrigin}/</loc>`);
    expect(sitemapXml).toContain(`<loc>${productionOrigin}/about</loc>`);
    expect(sitemapXml).not.toContain(`${productionOrigin}/app`);
    expect(sitemapXml).not.toContain(`${productionOrigin}/admin`);
  });

  it("keeps every indexed route in the sitemap with unique metadata", () => {
    const indexedPages = seoPages.filter((page) => page.index);
    const privatePages = seoPages.filter((page) => !page.index);

    expect(new Set(seoPages.map((page) => page.path)).size).toBe(seoPages.length);
    expect(new Set(indexedPages.map((page) => page.title)).size).toBe(
      indexedPages.length,
    );
    expect(new Set(indexedPages.map((page) => page.description)).size).toBe(
      indexedPages.length,
    );

    for (const page of indexedPages) {
      expect(sitemapXml).toContain(
        `<loc>${productionOrigin}${page.path === "/" ? "/" : page.path}</loc>`,
      );
    }
    for (const page of privatePages) {
      expect(sitemapXml).not.toContain(`<loc>${productionOrigin}${page.path}</loc>`);
    }
  });

  it("generates route-specific source HTML and updates metadata after SPA navigation", () => {
    expect(generatorSource).toContain('path.join(distDirectory, page.path.slice(1), "index.html")');
    expect(generatorSource).toContain('"application/ld+json"');
    expect(generatorSource).toContain("noindex, nofollow, noarchive");
    expect(routeSeoSource).toContain("export function useRouteSeo");
    expect(routeSeoSource).toContain("page.canonicalPath ?? page.path");
  });

  it("describes the installed site without claiming offline support", () => {
    const manifest = JSON.parse(siteManifest) as {
      name: string;
      start_url: string;
      display: string;
      theme_color: string;
    };

    expect(manifest.name).toContain("Promty");
    expect(manifest.start_url).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.theme_color).toBe("#09090b");
    expect(siteManifest).not.toContain("serviceworker");
  });
});
