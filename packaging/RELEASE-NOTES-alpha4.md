# Video Translator v0.1.0-alpha.4

Windows x64 portable public core. This is an Alpha prerelease.

## Changes

- Edited translations are preserved when dubbing starts. Task status and progress update without reselecting the task.
- Subtitle overlap and translation alignment warnings identify rows to review without stopping otherwise usable translations. Preview positioning better matches rendered subtitles.
- Initial translations request accurate, concise, clear wording. Measured overlong speech is uniformly accelerated first (Qwen up to 1.60x; Kokoro/Edge up to 1.40x). Text shortening requires your approval and preserves meaning as far as possible; subtitles use the resulting spoken text.
- After text edits or approved shortening, only changed utterances are synthesized again. Unchanged verified audio is reused; the full timeline and final audio/video are rebuilt. Connected subtitle fragments may form one utterance, so editing one fragment can regenerate its whole sentence.
- Reading video duration no longer decodes the entire source. WAV headers or bounded metadata inspection avoid the long CPU-heavy pause before dubbing.
- An optional, default-on visual AI credit and GitHub link appears during the final three seconds, with no additional narration.
- Refreshed settings, responsive layout, localized progress, and overall-stage/current-stage display.

## Download and setup

Download `VideoTranslator-v0.1.0-alpha.4-win-x64.zip` and verify it against `SHA256SUMS.txt`. Extract into a new folder and run `start-portable.bat`. The ZIP includes Python, the built frontend, ASR small, eight reference voices, and third-party license records. Python and Node.js do not need separate installation.

Use `install-upstream.bat` for FFmpeg, Kokoro and Qwen dependencies. Those assets are fetched from pinned upstream sources after their terms are shown; they are not mirrored in this ZIP. LM Studio and translation models are installed separately. Read `README-PORTABLE.md` before setup.

Keep your previous installation for rollback. Do not overwrite it with this ZIP. Copy only reviewed settings and reinstall/reuse upstream assets through documented installation paths; do not copy the old Python runtime or frontend. Older tasks without cached utterance audio need one new synthesis run to populate the cache.

## Verification and limitations

Source backend and frontend regressions, public core manifest/license verification, startup smoke checks and dependency audits are required before publication. Real Qwen testing verified unchanged raw audio hashes, selective sentence resynthesis and complete audio/video rebuilds. Existing unchanged model/runtime hardware acceptance is reused; this release does not claim a new full CUDA/CPU/AMD voice matrix or subjective listening evaluation.

The application is local-only and blocks LAN access. Edge TTS is an online service. Translation quality, timing and speed depend on the chosen models and hardware. Review highlighted subtitle rows and any shortened translations before sharing a video. Never share your settings, cookies, uploaded media or task history as release assets.

### Dependency audit scope

The release runtime updates urllib3 to 2.8.0 and multidict to 6.9.1, and the frontend build updates source-map-js to 1.2.2. The Python inventory audit retains the documented setuptools 80.9.0 exception for jieba compatibility: its Unicode `sdist` exclusion advisory concerns source-distribution creation, not this Windows ZIP workflow. Do not use the bundled runtime to publish source distributions.

The production npm audit reports no findings. The full development dependency audit still reports build-only denial-of-service advisories through Tailwind 3's braces and postcss-selector-parser dependency chains. These Node build tools and node_modules are not shipped in the portable core; builds use this project's trusted checked-in inputs. This is not a zero-finding full dependency audit. A Tailwind major-version migration is deferred rather than mixed into this release. Sources: [braces advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), [PostCSS selector advisory](https://github.com/advisories/GHSA-rj75-hqrm-r3gf).
