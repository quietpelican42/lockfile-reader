const { run } = require('./common'); const toml = require('smol-toml');
run('cargo', process.argv[2], process.argv[3], (t) => toml.parse(t));
