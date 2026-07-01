"use strict";

// Instrument JavaScript in an already isolated source-project copy.
const fs = require("fs");
const path = require("path");
const { createInstrumenter } = require("istanbul-lib-instrument");

const appRoot = path.resolve(process.argv[2] || "");
const manifestPath = path.resolve(process.argv[3] || "");
if (!appRoot || !manifestPath || !fs.statSync(appRoot).isDirectory()) {
  throw new Error("Usage: node instrument_javascript_coverage.js APP_ROOT MANIFEST");
}

const ignoredDirectories = new Set([
  ".git", "artifacts", "coverage", "dist", "node_modules", "vendor",
]);

function discover(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...discover(absolute));
    } else if (
      entry.isFile()
      && entry.name.toLowerCase().endsWith(".js")
      && !entry.name.toLowerCase().endsWith(".min.js")
    ) {
      files.push(absolute);
    }
  }
  return files.sort();
}

const instrumented = [];
const sourceBackupRoot = path.join(path.dirname(manifestPath), "original_sources");
for (const filename of discover(appRoot)) {
  const source = fs.readFileSync(filename, "utf8");
  const relative = path.relative(appRoot, filename).split(path.sep).join("/");
  const backup = path.join(sourceBackupRoot, ...relative.split("/"));
  fs.mkdirSync(path.dirname(backup), { recursive: true });
  fs.writeFileSync(backup, source, "utf8");
  const instrumenter = createInstrumenter({
    compact: false,
    preserveComments: true,
    produceSourceMap: false,
  });
  const output = instrumenter.instrumentSync(source, relative);
  fs.writeFileSync(filename, output, "utf8");
  instrumented.push(relative);
}

fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
fs.writeFileSync(
  manifestPath,
  JSON.stringify({
    adapter: "selenium_istanbul",
    files: instrumented,
    original_source_root: sourceBackupRoot,
  }, null, 2),
  "utf8",
);
