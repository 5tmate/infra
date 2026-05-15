export const LANGFLOW_VERSION = process.env.LANGFLOW_FAKE_VERSION ?? "1.2.0";

export const homepageHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Langflow</title>
</head>
<body>
  <main>
    <h1>Langflow</h1>
  </main>
</body>
</html>`;

export const etcPasswdResult = {
  result: "root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin",
};

export const commandOutputResult = {
  outputs: [{ result: "uid=0(root) gid=0(root) groups=0(root)" }],
};
