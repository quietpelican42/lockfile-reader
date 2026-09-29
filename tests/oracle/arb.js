const fs = require('fs'), path = require('path'), os = require('os');
const Arborist = require('@npmcli/arborist');
const { run } = require('./common');
run('npm(arborist)', process.argv[2], process.argv[3], async (text) => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ab-'));
  try {
    fs.writeFileSync(path.join(tmp, 'package-lock.json'), text);
    const arb = new Arborist({ path: tmp });
    const tree = await arb.loadVirtual();
    const nodes = [];
    for (const n of tree.inventory.values()) {
      if (n.isRoot) continue;
      nodes.push({
        location: n.location, name: n.name, packageName: n.packageName, version: n.version,
        resolved: n.resolved, integrity: n.integrity, isLink: n.isLink, target: n.isLink ? n.target.location : null,
        dev: n.dev, optional: n.optional, inBundle: n.inBundle,
        edges: [...n.edgesOut.values()].map((e) => ({ name: e.name, spec: e.spec, type: e.type, to: e.to ? e.to.location : null })),
      });
    }
    const root = [...tree.edgesOut.values()].map((e) => ({ name: e.name, spec: e.spec, type: e.type, to: e.to ? e.to.location : null }));
    return { nodes, root, lockfileVersion: tree.meta && tree.meta.originalLockfileVersion };
  } finally { fs.rmSync(tmp, { recursive: true }); }
});
