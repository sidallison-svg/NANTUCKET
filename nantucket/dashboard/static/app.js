/**
 * NANTUCKET — Shared dashboard utilities
 *
 * Loaded on every page. Provides:
 * - fetchJSON(url) — wrapper around fetch() with error handling
 * - formatCurrency(n) — "$1,234.56"
 * - formatVolume(n) — "1.2M", "450K"
 * - formatMarketCap(n) — "$1.2T", "$450B", "$12M"
 * - coloredChangePct(n) — HTML span with green/red coloring
 * - coloredChange(n) — same for dollar amounts
 * - truncate(str, n) — truncate with ellipsis
 */

// ── Data Fetching ────────────────────────────────────────────────────────────

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Number Formatting ────────────────────────────────────────────────────────

function formatCurrency(n, decimals = 2) {
  if (n == null || isNaN(n)) return '—';
  const abs = Math.abs(n);
  let formatted;
  if (abs >= 1000) {
    formatted = n.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  } else {
    formatted = n.toFixed(decimals);
  }
  return '$' + formatted;
}

function formatVolume(n) {
  if (!n) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
  return n.toString();
}

function formatMarketCap(n) {
  if (!n) return '—';
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(0) + 'M';
  return '$' + n.toLocaleString();
}

// ── Color-coded Changes ──────────────────────────────────────────────────────

function coloredChangePct(pct) {
  if (pct == null || isNaN(pct)) return '<span class="dim">—</span>';
  const cls = pct >= 0 ? 'up' : 'down';
  const sign = pct >= 0 ? '+' : '';
  return `<span class="${cls}">${sign}${pct.toFixed(2)}%</span>`;
}

function coloredChange(val, isDollar = false) {
  if (val == null || isNaN(val)) return '<span class="dim">—</span>';
  const cls = val >= 0 ? 'up' : 'down';
  const sign = val >= 0 ? '+' : '';
  const formatted = isDollar ? formatCurrency(Math.abs(val)) : val.toFixed(2);
  const prefix = isDollar ? (val >= 0 ? '+$' : '-$') : sign;
  const display = isDollar ? prefix + formatted.replace('$', '') : prefix + formatted;
  return `<span class="${cls}">${display}</span>`;
}

// ── String Utilities ─────────────────────────────────────────────────────────

function truncate(str, n) {
  if (!str) return '—';
  return str.length > n ? str.slice(0, n - 1) + '…' : str;
}

// ── Chart.js Global Defaults ─────────────────────────────────────────────────

if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#94a3b8';
  Chart.defaults.borderColor = 'rgba(51,65,85,0.5)';
  Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
  Chart.defaults.font.size = 12;
}
