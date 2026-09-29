const fs = require('fs'), path = require('path'), os = require('os');
const { run } = require('./common');
const impls = {
  5: { lf: require('lf7'), dp: require('dp7'), label: 'lockfile-file 6.0.5 (pnpm 7)' },
    9: { lf: require('@pnpm/lockfile-file'), dp: require('@pnpm/dependency-path'), label: 'lockfile-file 9.1.3 (pnpm 9)' },
};
run('pnpm', process.argv[2], process.argv[3], async (text) => {
  // multi-document files (pnpm 10+): the oracle reads single documents, so feed it the last one
  const docs = text.split(/^---\s*$/m).filter((d) => d.trim());
  const last = docs[docs.length - 1];
  const major = Number((/^lockfileVersion:\s*['"]?(\d+)/m.exec(last) || [])[1]);
  const impl = impls[major >= 6 ? 9 : 5];
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pn-'));
  try {
    fs.writeFileSync(path.join(tmp, 'pnpm-lock.yaml'), last);
    const lf = await impl.lf.readWantedLockfile(tmp, { ignoreIncompatible: false });
    const parsed = {}, edges = {};
    for (const [k, pkg] of Object.entries(lf.packages || {})) {
      let p; try { p = impl.dp.parse(k); } catch (e) { p = { error: String(e.message) }; }
      parsed[k] = { name: p.name, version: p.version, nonSemver: p.nonSemverVersion };
      edges[k] = {};
      for (const f of ['dependencies', 'optionalDependencies']) {
        for (const [n, ref] of Object.entries(pkg[f] || {})) {
          let rel = null; try { rel = impl.dp.refToRelative(ref, n); } catch (e) { rel = 'ERR ' + e.message; }
          edges[k][f + ':' + n] = rel;
        }
      }
    }
    const impEdges = {};
    for (const [ip, imp] of Object.entries(lf.importers || {})) {
      impEdges[ip] = {};
      for (const f of ['dependencies', 'devDependencies', 'optionalDependencies']) {
        for (const [n, ref] of Object.entries(imp[f] || {})) {
          let rel = null; try { rel = impl.dp.refToRelative(ref, n); } catch (e) { rel = 'ERR ' + e.message; }
          impEdges[ip][n] = rel;
        }
      }
    }
    return { lf, parsed, edges, impEdges, docs: docs.length, impl: impl.label };
  } finally { fs.rmSync(tmp, { recursive: true }); }
});
