// Entry point so `node --test TOOLS/tests/` works: Node's test runner treats a directory
// argument as a module to resolve, which lands here. It loads every *.test.mjs of this
// directory; `node --test "TOOLS/tests/*.test.mjs"` runs the same tests one file per process.
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

(async () => {
  for (const f of fs.readdirSync(__dirname).filter((n) => n.endsWith('.test.mjs')).sort()) {
    await import(pathToFileURL(path.join(__dirname, f)).href);
  }
})();
