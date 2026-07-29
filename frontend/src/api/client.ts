import { API_URL } from "../config";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class UnauthorizedError extends ApiError {
  constructor(
    message = "Sign in again before continuing.",
    detail: string | null = null,
  ) {
    super(message, 401, detail);
    this.name = "UnauthorizedError";
  }
}

export class ForbiddenError extends ApiError {
  constructor(
    message = "You do not have access to this resource.",
    detail: string | null = null,
  ) {
    super(message, 403, detail);
    this.name = "ForbiddenError";
  }
}

type ApiErrorMessages = {
  errorMessage?: string;
  forbiddenMessage?: string;
  unauthorizedMessage?: string;
};

type ApiErrorPayload = {
  code: string | null;
  detail: string | null;
};

export type ConditionalJsonResponse<T> = {
  etag: string | null;
  headers: Headers;
  notModified: boolean;
  payload: T | null;
};

const ENCRYPTED_DATA_UNAVAILABLE_CODE = "encrypted_data_unavailable";
const LEGACY_ENCRYPTION_ERROR_DETAILS = new Set([
  "Application encryption key cannot decrypt stored data",
  "Application encryption key is not configured",
]);

function encryptedDataUnavailableMessage(): string {
  const locale = typeof document === "undefined"
    ? "en"
    : document.documentElement.lang.toLowerCase();
  if (locale.startsWith("ko")) {
    return "일부 활동 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (locale.startsWith("ja")) {
    return "一部のアクティビティデータを読み込めませんでした。しばらくしてからもう一度お試しください。";
  }
  return "Some activity data is temporarily unavailable. Please try again shortly.";
}

function userFacingErrorDetail(payload: ApiErrorPayload): string | null {
  if (
    payload.code === ENCRYPTED_DATA_UNAVAILABLE_CODE
    || (payload.detail && LEGACY_ENCRYPTION_ERROR_DETAILS.has(payload.detail))
  ) {
    return encryptedDataUnavailableMessage();
  }
  return payload.detail;
}

const apiUrl = (path: string) =>
  path.startsWith("http://") || path.startsWith("https://")
    ? path
    : `${API_URL}${path}`;

let sessionRefreshRequest: Promise<boolean> | null = null;

function fetchApi(path: string, init: RequestInit = {}) {
  return fetch(apiUrl(path), {
    ...init,
    credentials: init.credentials ?? "include",
  });
}

function refreshSession(): Promise<boolean> {
  if (!sessionRefreshRequest) {
    sessionRefreshRequest = fetchApi("/api/auth/refresh", { method: "POST" })
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        sessionRefreshRequest = null;
      });
  }
  return sessionRefreshRequest;
}

function canRefreshSession(path: string): boolean {
  return path !== "/api/auth/refresh" && path !== "/api/auth/logout";
}

async function readErrorPayload(response: Response): Promise<ApiErrorPayload> {
  return response
    .clone()
    .json()
    .then((payload) => ({
      code: typeof payload?.code === "string" ? payload.code : null,
      detail: typeof payload?.detail === "string" ? payload.detail : null,
    }))
    .catch(() => ({ code: null, detail: null }));
}

export async function readErrorDetail(response: Response): Promise<string | null> {
  return userFacingErrorDetail(await readErrorPayload(response));
}

async function assertOk(response: Response, messages: ApiErrorMessages = {}) {
  if (response.ok) {
    return;
  }

  const detail = userFacingErrorDetail(await readErrorPayload(response));
  if (response.status === 401) {
    throw new UnauthorizedError(messages.unauthorizedMessage, detail);
  }
  if (response.status === 403) {
    throw new ForbiddenError(
      detail ?? messages.forbiddenMessage ?? "You do not have access to this resource.",
      detail,
    );
  }

  throw new ApiError(
    detail ?? messages.errorMessage ?? `Request failed with HTTP ${response.status}`,
    response.status,
    detail,
  );
}

async function apiFetch(
  path: string,
  init: RequestInit = {},
  messages: ApiErrorMessages = {},
  acceptedStatuses: readonly number[] = [],
) {
  let response = await fetchApi(path, init);
  if (response.status === 401 && canRefreshSession(path) && await refreshSession()) {
    response = await fetchApi(path, init);
  }
  if (!acceptedStatuses.includes(response.status)) {
    await assertOk(response, messages);
  }
  return response;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  messages: ApiErrorMessages = {},
): Promise<T> {
  const response = await apiFetch(path, init, messages);
  return (await response.json()) as T;
}

export async function requestConditionalJson<T>(
  path: string,
  etag: string | null,
  init: RequestInit = {},
  messages: ApiErrorMessages = {},
): Promise<ConditionalJsonResponse<T>> {
  const headers = new Headers(init.headers);
  if (etag) {
    headers.set("If-None-Match", etag);
  }
  const response = await apiFetch(
    path,
    {
      ...init,
      headers,
    },
    messages,
    [304],
  );
  const responseEtag = response.headers.get("ETag");
  if (response.status === 304) {
    return {
      etag: responseEtag ?? etag,
      headers: response.headers,
      notModified: true,
      payload: null,
    };
  }
  return {
    etag: responseEtag,
    headers: response.headers,
    notModified: false,
    payload: (await response.json()) as T,
  };
}

export async function requestJsonBody<T>(
  path: string,
  method: "DELETE" | "PATCH" | "POST" | "PUT",
  body: unknown,
  messages: ApiErrorMessages = {},
): Promise<T> {
  return requestJson<T>(
    path,
    {
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
      method,
    },
    messages,
  );
}

export async function requestVoid(
  path: string,
  init: RequestInit = {},
  messages: ApiErrorMessages = {},
): Promise<void> {
  await apiFetch(path, init, messages);
}
