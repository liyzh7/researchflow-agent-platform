# Frontend dependency notices

ResearchFlow's UI is independently authored. Installed framework/test/build dependencies are
recorded in pnpm-lock.yaml and ../docs/license-inventory.json. The 50-package platform-specific
metadata is retained in ../docs/frontend-license-inventory.json; the full lock has 94 packages.
Direct dependencies use MIT or Apache-2.0; transitive metadata also includes MPL-2.0, ISC and
BSD-3-Clause. These inventories do not replace the actual package license texts.

The production build copies the actual React, React DOM and scheduler LICENSE files into
dist/third-party-licenses.txt. Nginx serves this file with the static application. Their original
copyright and license text are retained, not replaced by ResearchFlow's MIT statement.

Lightning CSS 1.33.0 and its platform binding are unmodified MPL-2.0 build dependencies:
https://github.com/parcel-bundler/lightningcss . They are not runtime UI dependencies or copied
source modules in this repository. Preserve upstream notices and review its terms before
redistributing build tooling. The final Nginx stage copies only dist, not build node_modules.

This dependency inventory is not a complete legal or supply-chain security audit. No complete
third-party UI application/template was incorporated. Model/Python dependency notices remain
in ../docs/license-review.md, ../docs/models.md and installed package metadata.
