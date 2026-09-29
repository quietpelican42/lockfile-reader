const fs = require('fs'), path = require('path');
exports.run = async function (name, dir, out, fn) {
  fs.mkdirSync(out, { recursive: true });
  let ok = 0; const bad = {};
  for (const f of fs.readdirSync(dir).sort()) {
    try {
      const text = fs.readFileSync(path.join(dir, f), 'utf8');
      const res = await fn(text, f, path.join(dir, f));
      fs.writeFileSync(path.join(out, f + '.json'), JSON.stringify(res));
      ok++;
    } catch (e) { bad[f] = String(e && e.message).slice(0, 140); }
  }
  console.log(name, 'ok', ok, 'fail', Object.keys(bad).length, JSON.stringify(Object.entries(bad).slice(0, 6)));
};
