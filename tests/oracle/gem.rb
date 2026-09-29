require 'bundler'; require 'json'
# record how Bundler normalises platform strings (Gem::Platform.new)
$pmap = {}
module RecordPlatform
  def new(arch)
    r = super
    $pmap[arch.to_s] = r.to_s
    r
  end
end
Gem::Platform.singleton_class.prepend(RecordPlatform)
dir, out = ARGV
Dir.mkdir(out) unless Dir.exist?(out)
ok = 0; bad = {}
Dir.children(dir).sort.each do |f|
  begin
    txt = File.read(File.join(dir, f))
    txt = txt.sub(/^BUNDLED WITH\n.*\n?\z/m, "")
    txt = txt.sub(/^CHECKSUMS\n(?:[ ]+.*\n)*/, "")
    $pmap = {}
    p = Bundler::LockfileParser.new(txt)
    specs = p.specs.map do |s|
      src = s.source
      info = { 'class' => src.class.name }
      info['remotes'] = src.remotes.map(&:to_s) if src.respond_to?(:remotes)
      info['uri'] = src.uri.to_s if src.respond_to?(:uri)
      info['revision'] = src.options['revision'] if src.respond_to?(:options) && src.options
      info['ref'] = src.options['ref'] if src.respond_to?(:options) && src.options
      info['branch'] = src.options['branch'] if src.respond_to?(:options) && src.options
      info['tag'] = src.options['tag'] if src.respond_to?(:options) && src.options
      info['path'] = src.path.to_s if src.respond_to?(:path)
      { 'name' => s.name, 'version' => s.version.to_s, 'platform' => s.platform.to_s,
        'deps' => s.dependencies.map { |d| [d.name, d.requirement.to_s] }, 'source' => info }
    end
    roots = p.dependencies.map { |k, d| [d.name, d.requirement.to_s, d.respond_to?(:source) && !d.source.nil?] }
    File.write(File.join(out, f + '.json'), JSON.generate({ 'specs' => specs, 'roots' => roots, 'platforms' => p.platforms.map(&:to_s), 'bundler' => p.bundler_version.to_s, 'platform_map' => $pmap }))
    ok += 1
  rescue Exception => e
    bad[f] = e.message[0, 100]
  end
end
puts "gem(bundler #{Bundler::VERSION}) ok #{ok} fail #{bad.size} #{bad.first(6).inspect}"
