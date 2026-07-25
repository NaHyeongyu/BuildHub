import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const functionPath = new URL("./promty-cloudfront-spa-rewrite.js", import.meta.url);
const source = readFileSync(functionPath, "utf8");
const context = vm.createContext({});
vm.runInContext(`${source}\nthis.rewrite = handler;`, context);

const staticRoutes = {
  "/": "/index.html",
  "/about": "/about/index.html",
  "/about/": "/about/index.html",
  "/product": "/product/index.html",
  "/docs/collector": "/docs/collector/index.html",
  "/docs/collector/ai": "/docs/collector/ai/index.html",
  "/privacy": "/privacy/index.html",
  "/terms": "/terms/index.html",
  "/acceptable-use": "/acceptable-use/index.html",
  "/security": "/security/index.html",
  "/app": "/app/index.html",
  "/admin": "/admin/index.html",
  "/cli/login": "/cli/login/index.html",
};

for (const [route, expected] of Object.entries(staticRoutes)) {
  const request = { uri: route, querystring: {} };
  assert.equal(context.rewrite({ request }).uri, expected, route);
}

for (const asset of ["/promty.svg", "/assets/app.js", "/assets/app.css"]) {
  const request = { uri: asset, querystring: {} };
  assert.equal(context.rewrite({ request }).uri, asset, asset);
}

assert.equal(
  context.rewrite({
    request: { uri: "/", querystring: { project: { value: "project-id" } } },
  }).uri,
  "/app/index.html",
);
assert.equal(
  context.rewrite({
    request: { uri: "/", querystring: { utm_source: { value: "launch" } } },
  }).uri,
  "/index.html",
);
assert.equal(
  context.rewrite({ request: { uri: "/project/example", querystring: {} } }).uri,
  "/index.html",
);

const distributionPath = new URL("./promty-cloudfront-distribution.json", import.meta.url);
const distribution = JSON.parse(readFileSync(distributionPath, "utf8"));
assert.equal(distribution.HttpVersion, "http2and3");
assert.equal(distribution.CustomErrorResponses.Quantity, 0);
assert.equal(distribution.DefaultCacheBehavior.FunctionAssociations.Quantity, 1);

console.log("cloudfront_configuration=valid");
