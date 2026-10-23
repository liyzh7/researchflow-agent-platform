// Preserve actual runtime-library notices in the static distribution, not just node_modules.
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
if (process.argv.includes('--metadata')) {
  const store = resolve('node_modules/.pnpm');
  const packages = new Map();
  for (const entry of readdirSync(store, { withFileTypes: true })) {
    const modules = join(store, entry.name, 'node_modules');
    if (!entry.isDirectory() || !existsSync(modules)) continue;
    for (const name of readdirSync(modules)) {
      const paths = name.startsWith('@')
        ? readdirSync(join(modules, name)).map((child) => join(modules, name, child))
        : [join(modules, name)];
      for (const path of paths) {
        const filename = join(path, 'package.json');
        if (!existsSync(filename)) continue;
        const metadata = JSON.parse(readFileSync(filename, 'utf8'));
        packages.set(`${metadata.name}@${metadata.version}`, {
          name: metadata.name,
          version: metadata.version,
          license: metadata.license ?? 'UNKNOWN',
        });
      }
    }
  }
  process.stdout.write(
    JSON.stringify([...packages.values()].sort((a, b) => a.name.localeCompare(b.name))),
  );
} else {
  const domRequire = createRequire(require.resolve('react-dom/package.json'));
  const notices = ['ResearchFlow bundled runtime dependency notices\n'];
  for (const name of ['react', 'react-dom', 'scheduler']) {
    const root = dirname(domRequire.resolve(`${name}/package.json`));
    const metadata = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
    notices.push(`${name} ${metadata.version}\n${readFileSync(join(root, 'LICENSE'), 'utf8')}`);
  }
  writeFileSync(resolve('dist/third-party-licenses.txt'), notices.join('\n\n'), 'utf8');
}
