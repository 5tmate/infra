import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const N8N_VERSION = process.env.N8N_FAKE_VERSION ?? "1.100.0";

const sentryDsn =
  `https://abc123def456@o4505000000000000.ingest.sentry.io/4505000000000001` +
  `?release=n8n@${N8N_VERSION}`;

const sentryConfigBase64 = Buffer.from(sentryDsn, "utf8").toString("base64");

export const signinHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="viewport" content="width=device-width,initial-scale=1.0" />
  <title>n8n.io - Workflow Automation</title>
  <meta name="n8n:config:sentry" content="${sentryConfigBase64}" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
</head>
<body>
  <div id="app"></div>
  <noscript>This page requires JavaScript.</noscript>
</body>
</html>`;

export const restNotFound = () => ({
  statusCode: 404,
  headers: { "content-type": "application/json; charset=utf-8" },
  body: JSON.stringify({ code: 404, message: "Not Found" }),
});

const __dirname = dirname(fileURLToPath(import.meta.url));
export const faviconBytes = readFileSync(join(__dirname, "favicon.ico"));

// Fake unauthenticated /rest/settings response. Must contain (as literal
// substrings) all five words required by the n8n-config nuclei template:
//   isDocker, databaseType, nodeJsVersion, versionCli, instanceId
export const settingsJson = JSON.stringify({
  data: {
    versionCli: N8N_VERSION,
    isDocker: true,
    databaseType: "sqlite",
    nodeJsVersion: "20.10.0",
    instanceId: "5e9a2c0b8d4f47c0a5b9e1f3a7c2d5e8",
    endpointWebhook: "webhook",
    endpointWebhookTest: "webhook-test",
    endpointForm: "form",
    endpointFormTest: "form-test",
    timezone: "Asia/Singapore",
    telemetry: { enabled: false },
    userManagement: { showSetupOnFirstLoad: false },
    publicApi: { enabled: false },
  },
});
