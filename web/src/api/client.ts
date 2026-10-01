import { QueryClient } from '@tanstack/react-query'

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
})

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (MUTATING.has(method)) {
    headers.set('X-Corsa', '1')
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(path, { ...init, method, headers })
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`)
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}
