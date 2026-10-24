# Preview Layer and Dub Completion Design

## Goal

Keep preview subtitles below application dialogs and mark the Dub stage complete immediately when verified dubbing artifacts are ready.

## Design

- The subtitle overlay remains inside the video preview stacking layer. It must not use a page-global z-index that can exceed modal dialogs.
- Dubbing completion is an explicit backend event boundary. After the audio and raw dubbed video paths are persisted, the `dubbing_completed` event must include the persisted artifact fields needed by the frontend completion predicate.
- The frontend continues to color stages from verified persisted state via `completedStageIds`; it must not infer completion merely because a later stage started.
- Rendering remains a separate stage and is not responsible for reporting Dub completion.

## Verification

- A source-level UI regression test ensures the subtitle overlay cannot outrank the settings modal.
- Event snapshot tests ensure Dub completion carries its status, language, and audio/video artifact paths.
- Existing workflow progress tests continue to verify that stage colors reflect actual artifacts.

