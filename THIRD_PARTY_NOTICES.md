# Third-party materials

ResearchFlow source is MIT; dependencies, base images and models retain their own terms.
See docs/license-review.md and docs/license-inventory.json for the locked inventory
(119 Python packages and 94 npm packages). The inventory is not an OS-image SBOM.

Psycopg/psycopg-binary 3.3.6 and psycopg-pool 3.3.3 are LGPL-3.0-only, used unmodified as
independently replaceable Python packages. License files remain in site-packages.
Upstream corresponding source: https://github.com/psycopg/psycopg/tree/3.3.6 .
Review bundled libpq/OpenSSL notices and corresponding-source obligations before distributing
images; this file does not replace complete license texts or provide legal clearance.

MPL/compound-license libraries retain wheel-installed LICENSE/NOTICE files. React, React DOM
and scheduler notices are copied from installed packages into the static Web distribution.
No model weights, llama.cpp runtime, downloaded fonts/icons or training datasets are distributed
in this source candidate. Core Docker/CI dependency selection excludes the AI group; the default
uv groups still include AI and may install NVIDIA dependencies on some platforms.

Five locked entries remain NEEDS REVIEW: cuda-toolkit 13.0.3.0, nvidia-cuda-runtime 13.0.96,
nvidia-cudnn-cu13 9.24.0.43, nvidia-nccl-cu13 2.30.7 and nvidia-nvshmem-cu13 3.4.5.
Package references and hashes in a lockfile are not the packages themselves. This candidate
does not distribute those packages, but it does not clear their future redistribution.

Container OS packages and future binary/model redistribution require separate SBOM/notice
review. Do not replace upstream copyrights with ResearchFlow's MIT statement.
