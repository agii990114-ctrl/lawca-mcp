// 백엔드 API 클라이언트. 세션은 HttpOnly 쿠키, 상태 변경 요청에는 CSRF 토큰 헤더를 붙인다.
let csrfToken = "";

export function setCsrf(token: string) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailToMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
    return `입력값이 올바르지 않습니다${field ? ` (${field})` : ""}.`;
  }
  if (status === 413) return "요청이 너무 큽니다. 나누어 올려 주세요.";
  if (status >= 500) return "서버에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
  return "요청을 처리하지 못했습니다.";
}

async function parseError(res: Response): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = (await res.json()).detail;
  } catch {
    /* 본문이 JSON이 아님 */
  }
  return new ApiError(res.status, detailToMessage(detail, res.status));
}

function notifyUnauthorized(path: string) {
  if (!path.startsWith("/auth/login")) window.dispatchEvent(new Event("lawca:unauthorized"));
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized(path);
    throw await parseError(res);
  }
  return (await res.json()) as T;
}

export const get = <T,>(path: string) => api<T>("GET", path);
export const post = <T,>(path: string, body?: unknown) => api<T>("POST", path, body ?? {});
export const put = <T,>(path: string, body?: unknown) => api<T>("PUT", path, body ?? {});
export const patch = <T,>(path: string, body?: unknown) => api<T>("PATCH", path, body ?? {});
export const del = <T,>(path: string) => api<T>("DELETE", path);

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** 업로드: 진행률을 알려면 fetch 대신 XMLHttpRequest가 필요하다. */
export function uploadForm<T>(
  path: string,
  form: FormData,
  onProgress: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded, e.total);
    xhr.onload = () => {
      let json: { detail?: unknown } & Record<string, unknown> = {};
      try {
        json = JSON.parse(xhr.responseText);
      } catch {
        /* 본문이 JSON이 아님 */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(json as T);
      if (xhr.status === 401) notifyUnauthorized(path);
      reject(new ApiError(xhr.status, detailToMessage(json.detail, xhr.status)));
    };
    xhr.onerror = () => reject(new ApiError(0, "네트워크 오류로 업로드하지 못했습니다."));
    xhr.onabort = () => reject(new ApiError(0, "업로드가 취소되었습니다."));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}
