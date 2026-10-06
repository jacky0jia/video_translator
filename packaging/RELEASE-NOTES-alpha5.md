# Video Translator v0.1.0-alpha.5

Windows x64 portable public core. This Alpha prerelease corrects packaging privacy traces in older portable downloads; application behavior is unchanged from alpha.4.

## Privacy correction

- Removed Conda package-manager metadata/history and pip direct-install provenance from the bundled Python runtime. These contained developer-local usernames/cache/build paths, not application authentication cookies or API credentials. Python does not need these files to run.
- Added regression tests and privacy gates to public builds and independent bundle verification. Third-party licenses and contributor attribution are retained.
- Older affected portable ZIP downloads are withdrawn. Existing downloaded copies cannot be recalled; replace them with this corrected package before redistributing.

## Download and setup

Download `VideoTranslator-v0.1.0-alpha.5-win-x64.zip` and verify its SHA-256 against `SHA256SUMS.txt`. Extract into a new folder, then run `start-portable.bat`. Do not overwrite your working installation or copy its old Python runtime into this package.

The ZIP includes Python, the built frontend, ASR small, eight reference voices and third-party license records. Run `install-upstream.bat` for pinned upstream FFmpeg/Kokoro/Qwen dependencies; LM Studio and translation models are installed separately. Read `README-PORTABLE.md` first.

## Verification and limitations

Source regressions, frontend build, manifest/checksum validation, privacy scanning and portable startup smoke checks are required before publication. This packaging-only revision does not claim a new GPU/CPU voice acceptance matrix. Workflow improvements and dependency-audit limitations from [alpha.4](https://github.com/jacky0jia/video_translator/releases/tag/v0.1.0-alpha.4) still apply.

The privacy gate focuses on personal paths and developer installation/session metadata; it is not a guarantee against every possible secret or binary embedding. Never share settings, cookies, uploaded media or task history as release assets.
