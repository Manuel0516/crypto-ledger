export const API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  } catch {
    /* not JSON */
  }
  return response.statusText || `Request failed (${response.status})`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + path, init);
  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status);
  }
  return response.json() as Promise<T>;
}

export const getJson = <T>(path: string): Promise<T> => apiFetch<T>(path);

export const postJson = <T>(path: string, body: unknown): Promise<T> =>
  apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const uploadFile = <T>(path: string, file: File): Promise<T> => {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<T>(path, { method: "POST", body: form });
};

export const uploadFileWithFields = <T>(path: string, file: File, fields: Record<string, string>): Promise<T> => {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(fields)) {
    if (value) form.append(key, value);
  }
  return apiFetch<T>(path, { method: "POST", body: form });
};

export const patchJson = <T>(path: string, body: unknown): Promise<T> =>
  apiFetch<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const putJson = <T>(path: string, body: unknown): Promise<T> =>
  apiFetch<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const deleteJson = <T>(path: string, body?: unknown): Promise<T> =>
  apiFetch<T>(path, { method: "DELETE", ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });

export const downloadUrl = (path: string): string => API_BASE + path;

export function triggerDownload(path: string): void {
  window.location.href = downloadUrl(path);
}
