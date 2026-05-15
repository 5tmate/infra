import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const LANGFLOW_VERSION = process.env.LANGFLOW_FAKE_VERSION ?? "1.2.0";

// Mirror of langflow-ai/langflow frontend index.html minus the vite dev <script> tags.
export const homepageHtml = `<!doctype html>
<html lang="en">
  <head>
    <base href="/" />
    <meta charset="UTF-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" href="/favicon.ico" />
    <link rel="manifest" href="/manifest.json" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Chivo:ital,wght@0,100..900;1,100..900&family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap"
      rel="stylesheet"
    />
    <title>Langflow</title>
  </head>
  <body id="body" class="dark" style="width: 100%; height: 100%">
    <noscript>You need to enable JavaScript to run this app.</noscript>
    <div style="width: 100vw; height: 100vh" id="root"></div>
  </body>
</html>`;

export const etcPasswdResult = {
  result: "root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin",
};

export const commandOutputResult = {
  outputs: [{ result: "uid=0(root) gid=0(root) groups=0(root)" }],
};

const __dirname = dirname(fileURLToPath(import.meta.url));
export const faviconBytes = readFileSync(join(__dirname, "favicon.ico"));
