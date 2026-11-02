# Frontend dependency security assessment

Reviewed: 2026-10-14. Applies to both local Windows editions.

## Decision

Keep Tailwind CSS 3. Do not force a framework major-version migration solely to
make `npm audit` report zero findings. Apply compatible upstream fixes when
available; retain narrowly documented exceptions for verified build-only risks.
An exception is a risk acceptance, not a claim that a vulnerable dependency is fixed.

## Fixed independently

`source-map-js` is kept on the compatible 1.x line at version 1.2.2 or newer.
GHSA-68fv-2mgg-jv7q is fixed in 1.2.2. The supporter lock previously used 1.2.1;
the public lock already used 1.2.2. No Tailwind major upgrade is required for this fix.

## Pending upstream patches / accepted build-only findings

- `braces` <=3.0.3: GHSA-vfj7-8cjw-p6xm. Deeply nested untrusted glob patterns
  can exhaust the Node call stack. The reviewed advisory currently lists no
  patched version. Tailwind 3 brings it through its scanning/watching dependencies.
- `postcss-selector-parser` <7.1.6: GHSA-rj75-hqrm-r3gf. Untrusted large selectors
  can cause excessive CPU use. Version 7.1.6 is fixed, but the present Tailwind 3
  / postcss-nested dependency chain includes an older major line. Do not force
  a cross-major override without compatibility evidence; wait for a compatible
  upstream update or reviewed patch.

The full audit also reports dependents such as chokidar, micromatch, fast-glob,
postcss-nested and Tailwind. These are dependency-chain entries, not seven
independent root vulnerabilities. Preserve the raw audit output.

## Exposure boundaries required for this acceptance

- Tailwind and the affected packages are development dependencies, not production
  frontend dependencies. `npm audit --omit=dev` must remain clean.
- Tailwind scans only the frontend's `index.html` and `src/**/*.{js,ts,jsx,tsx}`.
  Video uploads, subtitle edits, translation outputs and task history are outside
  those source roots and do not become CSS compiler inputs.
- The deployed application serves prebuilt HTML/JS/CSS. It does not invoke the
  Tailwind/PostCSS compiler to handle user requests.
- Build from reviewed source. Isolate untrusted pull-request builds, do not give
  them release credentials, and do not expose the Vite development server publicly.

These are conditional, project-specific exceptions. Reassess immediately if an
affected package enters the production tree, untrusted CSS/globs reach a compiler,
the development server is exposed, the dependency chain changes, or an advisory
describes a different impact. New advisories are not automatically accepted.

## Verification and follow-up

Run both `npm audit --omit=dev` and the full `npm audit`, production builds and
regression tests. The full audit may exit nonzero for the findings above; record
them explicitly rather than using `npm audit fix --force`, lowering audit severity,
or calling the result zero-vulnerability. Recheck upstream fixes before each new
release and prefer a compatible patch when it is available.

Primary advisory references:

- https://github.com/advisories/GHSA-vfj7-8cjw-p6xm
- https://github.com/advisories/GHSA-rj75-hqrm-r3gf
- https://github.com/advisories/GHSA-68fv-2mgg-jv7q
