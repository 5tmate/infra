const ALLOWLIST = [
  /^[a-z0-9.-]+\.interactsh\.com$/i,
  /^[a-z0-9.-]+\.oast\.fun$/i,
  /^[a-z0-9.-]+\.oast\.site$/i,
  /^[a-z0-9.-]+\.oast\.pro$/i,
  /^[a-z0-9.-]+\.oast\.live$/i,
  /^[a-z0-9.-]+\.oast\.me$/i,
  /^[a-z0-9.-]+\.oastify\.com$/i,
  /^[a-z0-9.-]+\.canarytokens\.com$/i,
];

const TIMEOUT_MS = 3000;

export function isAllowedOastHost(hostname) {
  return ALLOWLIST.some((re) => re.test(hostname));
}

export async function oastFetch(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return { ok: false, reason: "invalid-url" };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, reason: "invalid-protocol" };
  }
  if (!isAllowedOastHost(parsed.hostname)) {
    return { ok: false, reason: "host-not-allowlisted" };
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    await fetch(parsed.toString(), {
      method: "GET",
      signal: ctrl.signal,
      redirect: "manual",
    });
    console.log("oast: fetched", parsed.host);
    return { ok: true };
  } catch (err) {
    console.log("oast: fetch-error", parsed.host, err?.name ?? "unknown");
    return { ok: false, reason: err?.name ?? "fetch-error" };
  } finally {
    clearTimeout(timer);
  }
}
