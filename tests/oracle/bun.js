const { run } = require('./common'); const jp = require('jsonc-parser');
run('bun', process.argv[2], process.argv[3], (t) => { const errs = []; const v = jp.parse(t, errs, { allowTrailingComma: true }); if (errs.length) throw new Error('jsonc error ' + errs[0].error); return v; });
