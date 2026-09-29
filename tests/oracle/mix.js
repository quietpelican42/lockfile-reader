// Independent line-based extraction for mix.lock (no Elixir/Erlang on this machine).
// mix writes one entry per line: `  "app": {:tag, ...},`. This regex reader is deliberately
// unrelated to the term parser in lockfile_reader.
const { run } = require('./common');
run('mix(regex)', process.argv[2], process.argv[3], (t) => {
  const entries = [], unmatched = [];
  // old files put the first entry on the `%{` line and close the map on the last entry's line
  const body = t.replace(/^\s*%\{/, '').replace(/\}\s*$/, '');
  for (const line of body.split('\n')) {
    let m = /^\s*"([^"]+)"\s*(?::|=>)\s*\{:(\w+),\s*(.*?)\},?\s*$/.exec(line);
    if (!m) { if (/^\s*"[^"]+"\s*(?::|=>)/.test(line)) unmatched.push(line.slice(0, 80)); continue; }
    const [, app, tag, rest] = m;
    const e = { app, tag };
    if (tag === 'hex') {
      const h = /^:([\w]+),\s*"([^"]*)"(?:,\s*"([0-9a-f]*)")?(?:,\s*\[([^\]]*)\])?(?:,\s*\[(.*)\])?(?:,\s*"([^"]*)")?(?:,\s*"([0-9a-f]*)")?\s*$/.exec(rest);
      if (!h) { unmatched.push(line.slice(0, 80)); continue; }
      e.name = h[1]; e.version = h[2]; e.inner = h[3] || null; e.repo = h[6] || null; e.outer = h[7] || null;
      e.deps = [];
      const re = /\{:(\w+),\s*"([^"]*)",\s*\[([^\]]*)\]\}/g; let d;
      while ((d = re.exec(h[5] || ''))) e.deps.push([d[1], d[2], /optional:\s*true/.test(d[3])]);
    } else if (tag === 'git') {
      const g = /^"([^"]*)",\s*"([^"]*)"/.exec(rest);
      if (!g) { unmatched.push(line.slice(0, 80)); continue; }
      e.url = g[1]; e.sha = g[2];
    } else if (tag === 'path') {
      const g = /^"([^"]*)"/.exec(rest); e.path = g && g[1];
    }
    entries.push(e);
  }
  return { entries, unmatched };
});
