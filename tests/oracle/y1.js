const { run } = require('./common'); const lf = require('@yarnpkg/lockfile');
run('yarn-v1', process.argv[2], process.argv[3], (t) => { const r = lf.parse(t); if (r.type !== 'success') throw new Error(r.type); return r.object; });
