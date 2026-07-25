import { useEffect } from "react";
import pages from "../seo-pages.json";
import type { AppRoute } from "./routing";

const PRODUCTION_ORIGIN = "https://promty.org";

type SeoPage = {
  route: AppRoute;
  path: string;
  canonicalPath?: string;
  index: boolean;
  title: string;
  description: string;
  image: string;
};

const seoPages = pages as SeoPage[];

function upsertMeta(attribute: "name" | "property", value: string, content: string) {
  let element = document.querySelector<HTMLMetaElement>(
    `meta[${attribute}="${value}"]`,
  );
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute(attribute, value);
    document.head.append(element);
  }
  element.content = content;
}

function upsertCanonical(href: string) {
  let element = document.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!element) {
    element = document.createElement("link");
    element.rel = "canonical";
    document.head.append(element);
  }
  element.href = href;
}

function removeCanonical() {
  document.querySelector<HTMLLinkElement>('link[rel="canonical"]')?.remove();
}

export function applyRouteSeo(route: AppRoute) {
  const page = seoPages.find((candidate) => candidate.route === route);

  if (!page) {
    document.title = "Page not found · Promty";
    document.documentElement.lang = "en";
    upsertMeta("name", "description", "The requested Promty page could not be found.");
    upsertMeta("name", "robots", "noindex, nofollow, noarchive");
    upsertMeta("property", "og:title", "Page not found · Promty");
    upsertMeta("property", "og:description", "The requested Promty page could not be found.");
    upsertMeta("name", "twitter:title", "Page not found · Promty");
    upsertMeta("name", "twitter:description", "The requested Promty page could not be found.");
    removeCanonical();
    document.querySelector<HTMLMetaElement>('meta[property="og:url"]')?.remove();
    return;
  }

  const canonicalUrl = new URL(
    page.canonicalPath ?? page.path,
    PRODUCTION_ORIGIN,
  ).href;
  const imageUrl = new URL(page.image, PRODUCTION_ORIGIN).href;

  document.title = page.title;
  document.documentElement.lang = "en";
  upsertCanonical(canonicalUrl);
  upsertMeta("name", "description", page.description);
  upsertMeta(
    "name",
    "robots",
    page.index
      ? "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
      : "noindex, nofollow, noarchive",
  );
  upsertMeta("property", "og:title", page.title);
  upsertMeta("property", "og:description", page.description);
  upsertMeta("property", "og:url", canonicalUrl);
  upsertMeta("property", "og:image", imageUrl);
  upsertMeta("name", "twitter:title", page.title);
  upsertMeta("name", "twitter:description", page.description);
  upsertMeta("name", "twitter:image", imageUrl);
}

export function useRouteSeo(route: AppRoute) {
  useEffect(() => {
    applyRouteSeo(route);
  }, [route]);
}
