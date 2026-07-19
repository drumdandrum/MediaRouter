const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("app/static/app.js", "utf8");

assert.match(source, /return decision\.runtime_url \|\| decision\.stream_url \|\| "";/,
  "ticketed runtime_url must take precedence over compatibility URLs");
assert.match(source, /bindExactRuntimeActions\(target, resolvedUrl\)/,
  "preview actions must receive the exact resolve response URL");
assert.match(source, /window\.open\(url, "_blank", "noopener"\)/,
  "open must use the exact URL supplied to the action binder");
assert.match(source, /navigator\.clipboard\.writeText\(url\)/,
  "copy must use the same exact URL");
assert.match(source, /searchParams\.has\("ticket"\)/,
  "the tester must visibly distinguish ticketed URLs");
assert.match(source, /renderBrokerRuntimePreview\(\);\s*renderBrokerDecision\(\);\s*toast\("Broker reservation created"\)/,
  "resolve success must immediately re-render ticket-aware controls");
assert.doesNotMatch(source, /data-broker-runtime-open="\$\{runtimeUrlFor/,
  "resolved open actions must not reconstruct URLs from catalog data");

console.log("Source Decision Tester frontend tests passed");
