const { run } = require('./common'); const { parseSyml } = require('@yarnpkg/parsers');
run('yarn-berry', process.argv[2], process.argv[3], (t) => parseSyml(t));
