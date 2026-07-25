function handler(event) {
  var request = event.request;
  var uri = request.uri;
  var normalizedUri = uri.length > 1 && uri.endsWith("/") ? uri.slice(0, -1) : uri;
  var lastSegment = uri.substring(uri.lastIndexOf("/") + 1);
  var staticRoutes = {
    "/": "/index.html",
    "/about": "/about/index.html",
    "/product": "/product/index.html",
    "/docs/collector": "/docs/collector/index.html",
    "/docs/collector/ai": "/docs/collector/ai/index.html",
    "/privacy": "/privacy/index.html",
    "/terms": "/terms/index.html",
    "/acceptable-use": "/acceptable-use/index.html",
    "/security": "/security/index.html",
    "/app": "/app/index.html",
    "/admin": "/admin/index.html",
    "/cli/login": "/cli/login/index.html"
  };
  var legacyWorkspaceKeys = [
    "activity",
    "auth_error",
    "author",
    "community",
    "file",
    "profile",
    "project",
    "prompt",
    "preview",
    "public_project",
    "session",
    "tab",
    "view"
  ];

  if (normalizedUri === "/") {
    for (var index = 0; index < legacyWorkspaceKeys.length; index += 1) {
      if (request.querystring && request.querystring[legacyWorkspaceKeys[index]]) {
        request.uri = "/app/index.html";
        return request;
      }
    }
  }

  if (staticRoutes[normalizedUri]) {
    request.uri = staticRoutes[normalizedUri];
    return request;
  }

  if (uri.endsWith("/") || lastSegment.indexOf(".") === -1) {
    request.uri = "/index.html";
  }

  return request;
}
