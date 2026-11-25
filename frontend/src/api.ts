const TOKEN_KEY = "subsphere_access_token";

export function getStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Ignore storage errors in restricted environments
  }
}

export function formatMinorCurrency(
  minor: number | null | undefined,
  currency: string = "USD"
): string {
  if (minor === null || minor === undefined) {
    return "Unavailable";
  }
  const amount = (minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${currency.toUpperCase()} ${amount}`;
}

export function formatNullableMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "Unavailable";
  }
  return value.toLocaleString();
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function apiRequest<T = any>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    token?: string;
  } = {}
): Promise<T> {
  const token = options.token ?? getStoredToken();
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const resp = await fetch(path, {
    method: options.method || "GET",
    headers,
    credentials: "include",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (resp.status === 204) {
    return undefined as T;
  }

  const contentType = resp.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await resp.json() : await resp.text();

  if (!resp.ok) {
    const errObj = typeof payload === "object" && payload ? (payload as any) : {};
    throw new ApiError(
      resp.status,
      errObj.code || `HTTP_${resp.status}`,
      errObj.message || errObj.detail || `Request failed (${resp.status})`,
      errObj.details
    );
  }
  return payload as T;
}
