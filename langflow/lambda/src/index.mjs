import { oastFetch, isAllowedOastHost } from "./oast.mjs";
import {
  LANGFLOW_VERSION,
  commandOutputResult,
  etcPasswdResult,
  faviconBytes,
  homepageHtml,
} from "./langflow.mjs";

const json = (body, status = 200) => ({
  statusCode: status,
  headers: { "content-type": "application/json; charset=utf-8" },
  body: typeof body === "string" ? body : JSON.stringify(body),
});

const html = (body) => ({
  statusCode: 200,
  headers: {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
  },
  body,
});

const favicon = () => ({
  statusCode: 200,
  headers: {
    "content-type": "image/x-icon",
    "cache-control": "public, max-age=86400",
  },
  body: faviconBytes.toString("base64"),
  isBase64Encoded: true,
});

const getBody = (event) => {
  if (!event?.body) return "";
  if (event.isBase64Encoded) return Buffer.from(event.body, "base64").toString("utf8");
  return event.body;
};

const fireOastFromBody = async (rawBody) => {
  if (!rawBody) return;

  const candidates = rawBody.match(/https?:\/\/[^\s"'<>]+/g) ?? [];
  for (const raw of candidates) {
    try {
      const parsed = new URL(raw);
      if (isAllowedOastHost(parsed.hostname)) {
        await oastFetch(parsed.toString());
      }
    } catch {
      // Ignore malformed URL-like strings in scanner payloads.
    }
  }
};

export const handler = async (event) => {
  const method = event?.requestContext?.http?.method ?? "GET";
  const path = event?.rawPath ?? "/";

  if (method === "GET" && path === "/api/v1/version") {
    return json({ package: "Langflow", version: LANGFLOW_VERSION });
  }

  if (method === "GET" && path === "/favicon.ico") {
    return favicon();
  }

  if (method === "GET") {
    return html(homepageHtml);
  }

  if (method === "POST" && path === "/api/v1/validate/code") {
    const body = getBody(event);
    await fireOastFromBody(body);
    return json(etcPasswdResult);
  }

  if (method === "POST" && /^\/api\/v1\/build_public_tmp\/[^/]+\/flow$/.test(path)) {
    const body = getBody(event);
    await fireOastFromBody(body);
    return json(commandOutputResult);
  }

  if (method === "POST" && path === "/api/v1/login") {
    return json({ detail: "Unauthorized" }, 401);
  }

  if (method === "OPTIONS") {
    return { statusCode: 204, headers: {}, body: "" };
  }

  return json({ detail: "Not Found" }, 404);
};

export default handler;
