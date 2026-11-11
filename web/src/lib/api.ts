export async function api<T>(resource: string, method = "GET", data?: unknown): Promise<T> {
  const response = await fetch(`/api/${resource}`, {
    method,
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
    cache: "no-store",
    signal: AbortSignal.timeout(25000),
  });
  if (!response.ok) {
    const body: { error?: string } = await response.json();
    throw new Error(body.error ?? `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected request failure.";
}
