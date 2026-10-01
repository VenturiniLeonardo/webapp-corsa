import { QueryClient } from '@tanstack/react-query'

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
})

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (MUTATING.has(method)) {
    headers.set('X-Corsa', '1')
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(path, { ...init, method, headers })
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail, () => undefined)
    throw new ApiError(`${method} ${path} → ${res.status}`, res.status, detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}
