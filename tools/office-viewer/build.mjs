// Development-only build. Runtime previews use checked-in, offline assets.
import { build } from 'esbuild';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(root, '../../src/assets/office_viewer');
await mkdir(path.join(output, 'licenses'), { recursive: true });
const bundled = await build({
  stdin: { contents: "export {loadPresentation,getSlides,getSlideNotes} from '@office-kit/pptx'; export {renderSlideToSvg} from '@office-kit/pptx-preview';", resolveDir: root },
  bundle: true, minify: true, platform: 'browser', format: 'iife',
  globalName: 'UthPptx', outfile: path.join(output, 'pptx_engine.js'), metafile: true,
});
for (const [source, target] of [
  ['docx-preview/dist/docx-preview.min.js', 'docx-preview.min.js'],
  ['jszip/dist/jszip.min.js', 'jszip.min.js'],
  ['docx-preview/LICENSE', 'licenses/docx-preview.txt'],
  ['jszip/LICENSE.markdown', 'licenses/jszip.txt'],
  ['fflate/LICENSE', 'licenses/fflate.txt'],
  ['@office-kit/pptx/LICENSE', 'licenses/office-kit.txt'],
  ['@office-kit/pptx/THIRD_PARTY_NOTICES.md', 'licenses/office-kit-notices.txt'],
]) await copyFile(path.join(root, 'node_modules', source), path.join(output, target));
const hashes = {};
for (const name of ['docx-preview.min.js', 'jszip.min.js', 'pptx_engine.js']) {
  hashes[name] = createHash('sha256').update(await readFile(path.join(output, name))).digest('hex');
}
await writeFile(path.join(output, 'manifest.json'), JSON.stringify({
  packages: { 'docx-preview': '0.4.1', jszip: '3.10.2', '@office-kit/pptx': '0.28.0', '@office-kit/pptx-preview': '0.21.0' },
  hashes, inputs: Object.keys(bundled.metafile.inputs).filter(name => name.includes('node_modules')).map(name => name.replaceAll('\\', '/').split('node_modules/').pop()),
}, null, 2) + '\n');
