import {
  faviconBytes,
  restNotFound,
  settingsJson,
  signinHtml,
} from "./n8n.mjs";
import { oastFetch } from "./oast.mjs";

const html = (body, status = 200) => ({
  statusCode: status,
  headers: {
    "content-type": "text/html; charset=utf-8",
    "x-powered-by": "Express",
    "cache-control": "no-store",
  },
  body,
});

const redirect = (location) => ({
  statusCode: 302,
  headers: { location, "cache-control": "no-store" },
  body: "",
});

const jsonOk = (jsonBody) => ({
  statusCode: 200,
  headers: { "content-type": "application/json; charset=utf-8" },
  body: typeof jsonBody === "string" ? jsonBody : JSON.stringify(jsonBody),
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

const getQueryParam = (event, key) => {
  const raw = event?.rawQueryString;
  if (!raw) return undefined;
  return new URLSearchParams(raw).get(key) ?? undefined;
};

const isRestPath = (path) => path.startsWith("/rest/");
const isWebhookPath = (path) =>
  path.startsWith("/webhook/") || path.startsWith("/webhook-test/");

export const handler = async (event) => {
  const method = event?.requestContext?.http?.method ?? "GET";
  const path = event?.rawPath ?? "/";

  if (isWebhookPath(path)) {
    const target = getQueryParam(event, "url");
    if (target) {
      await oastFetch(target);
      return jsonOk({ workflowName: "demo", executed: true });
    }
    return restNotFound();
  }

  if (method === "GET") {
    if (path === "/") return redirect("/signin");
    if (path === "/signin") return html(signinHtml);
    if (path === "/favicon.ico") return favicon();
    if (path === "/rest/settings") return jsonOk(settingsJson);
    if (isRestPath(path)) return restNotFound();
    return html(signinHtml);
  }

  if (method === "POST" && path === "/rest/login") {
    return {
      statusCode: 401,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({ code: 401, message: "Unauthorized" }),
    };
  }

  if (method === "OPTIONS") {
    return { statusCode: 204, headers: {}, body: "" };
  }

  return restNotFound();
};
