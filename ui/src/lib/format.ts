/** Misc UI helpers — short on purpose. */

export function classNames(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ")
}

export function relativeTime(iso: string | null): string {
  if (!iso) return ""
  const d = new Date(iso).getTime()
  const diff = Date.now() - d
  if (diff < 0) return "just now"
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function shortId(id: string, len = 6): string {
  return id.replace(/[^a-z0-9]/gi, "").slice(0, len).toUpperCase()
}

export function fmtTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`
  return `${(n / 1_000_000).toFixed(2)}M`
}

export function fmtCost(s: string | number): string {
  const n = typeof s === "string" ? parseFloat(s) : s
  if (isNaN(n)) return "—"
  if (n < 0.01) return `<$0.01`
  if (n < 1) return `$${n.toFixed(3)}`
  return `$${n.toFixed(2)}`
}
