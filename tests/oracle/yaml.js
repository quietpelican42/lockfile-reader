// Raw YAML -> JSON with js-yaml (JSON schema: null/bool/int/float only), used to test the YAML-subset reader.
const { run } = require('./common'); const yaml = require('js-yaml');
run('yaml(js-yaml)', process.argv[2], process.argv[3], (t) => {
  const docs = yaml.loadAll(t, null, { schema: yaml.JSON_SCHEMA });
  return { docs };
});
