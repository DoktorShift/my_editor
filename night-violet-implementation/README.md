# Night Violet: actual native application evidence

Implementation PR: [rinbal/my_editor#60](https://github.com/rinbal/my_editor/pull/60).

The before and after images below are real Qt/Cocoa windows using the same fixture content and logical sizes. They are not HTML mockups. Native frame captures have the `native-` prefix; the corresponding client PNGs are included separately. Capture manifests record macOS, Qt, revision, script hash, logical size, actual 1×/2× scale and the Light/Dark/Automatic/fullscreen checks.

Before application code equals upstream `57a023e5aa660749ef842e4fd8c277e33045c217`; its checkout `f2225d751e0703d385190d308f05a088aa02f5ca` adds only the PR #59 planning documents. The complete after set is `a22a18f1709c46dade63abf8481e11422c5d6415`, with a clean working tree. The final correction set is `d6aaa7a66ad1a1aa9df767fdee092d5ff4bf33fa`, also clean: it replaces reader, media-server/relay list and image-review captures after the closing corrections, and includes an isolated payment QR check. Per-set manifests preserve exact provenance. All content is synthetic or from repository test fixtures. The payment QR encodes the fake fixture `LNBCRT1`.

HTML reference: [original design and labeled reference captures](../night-violet-plan/). See [contrast.json](contrast.json) and [file hashes](manifest.json).

## Major groups

| Group | Before Light | After Light | Before Dark | After Dark |
| --- | --- | --- | --- | --- |
| Imports | ![Imports before light](before/native-imports-light.png) | ![Imports after light](after/native-imports-light.png) | ![Imports before dark](before/native-imports-dark.png) | ![Imports after dark](after/native-imports-dark.png) |
| Publish as Article | ![Publish as Article before light](before/native-publishing-light.png) | ![Publish as Article after light](after/native-publishing-light.png) | ![Publish as Article before dark](before/native-publishing-dark.png) | ![Publish as Article after dark](after/native-publishing-dark.png) |
| Editor and Drafts | ![Editor and Drafts before light](before/native-editor-drafts-light.png) | ![Editor and Drafts after light](after/native-editor-drafts-light.png) | ![Editor and Drafts before dark](before/native-editor-drafts-dark.png) | ![Editor and Drafts after dark](after/native-editor-drafts-dark.png) |
| Media Library | ![Media Library before light](before/native-media-light.png) | ![Media Library after light](after/native-media-light.png) | ![Media Library before dark](before/native-media-dark.png) | ![Media Library after dark](after/native-media-dark.png) |
| Membership | ![Membership before light](before/native-membership-light.png) | ![Membership after light](after/native-membership-light.png) | ![Membership before dark](before/native-membership-dark.png) | ![Membership after dark](after/native-membership-dark.png) |

## All implemented fixtures

Rows link native screenshots unless explicitly labeled as client captures. Embedded panels/popovers are isolated real components in these fixtures; their standalone fixture frame is not an added application window. Some native title bars are inactive. English example content remains unchanged in the German layout checks.

| Fixture | Light | Dark |
| --- | --- | --- |
| article-details | [Light](after/native-article-details-light.png) | [Dark](after/native-article-details-dark.png) |
| backup-account | [Light](after/native-backup-account-light.png) | [Dark](after/native-backup-account-dark.png) |
| connect | [Light](after/native-connect-light.png) | [Dark](after/native-connect-dark.png) |
| cover-crop | [Light](after/native-cover-crop-light.png) | [Dark](after/native-cover-crop-dark.png) |
| create-account | [Light](after/native-create-account-light.png) | [Dark](after/native-create-account-dark.png) |
| draft-conflict | [Light](after/native-draft-conflict-light.png) | [Dark](after/native-draft-conflict-dark.png) |
| draft-preview | [Light](after/native-draft-preview-light.png) | [Dark](after/native-draft-preview-dark.png) |
| draft-preview-full | [Light](after/native-draft-preview-full-light.png) | [Dark](after/native-draft-preview-full-dark.png) |
| draft-relays | [Light client (native capture distorted)](corrections/draft-relays-light.png) | [Dark](corrections/native-draft-relays-dark.png) |
| editor-drafts | [Light](after/native-editor-drafts-light.png) | [Dark](after/native-editor-drafts-dark.png) |
| follow-source | [Light](after/native-follow-source-light.png) | [Dark](after/native-follow-source-dark.png) |
| import-file | [Light](after/native-import-file-light.png) | [Dark](after/native-import-file-dark.png) |
| import-link | [Light](after/native-import-link-light.png) | [Dark](after/native-import-link-dark.png) |
| import-options | [Light client (native capture distorted)](after/import-options-light.png) | [Dark](after/native-import-options-dark.png) |
| import-progress | [Light](after/native-import-progress-light.png) | [Dark](after/native-import-progress-dark.png) |
| imports | [Light](after/native-imports-light.png) | [Dark](after/native-imports-dark.png) |
| live-publishing | [Light](after/native-live-publishing-light.png) | [Dark](after/native-live-publishing-dark.png) |
| live-reader | [Light](after/native-live-reader-light.png) | [Dark](after/native-live-reader-dark.png) |
| media | [Light](after/native-media-light.png) | [Dark](after/native-media-dark.png) |
| media-servers | [Light](corrections/native-media-servers-light.png) | [Dark client (native capture distorted)](corrections/media-servers-dark.png) |
| media-viewer-local | [Light](after/native-media-viewer-local-light.png) | [Dark](after/native-media-viewer-local-dark.png) |
| media-viewer-nostr | [Light](after/native-media-viewer-nostr-light.png) | [Dark](after/native-media-viewer-nostr-dark.png) |
| membership | [Light](after/native-membership-light.png) | [Dark](after/native-membership-dark.png) |
| membership-payment | [Light client (native capture distorted)](corrections/membership-payment-light.png) | [Dark](corrections/native-membership-payment-dark.png) |
| mentions | [Light](after/native-mentions-light.png) | [Dark](after/native-mentions-dark.png) |
| move-key | [Light](after/native-move-key-light.png) | [Dark](after/native-move-key-dark.png) |
| nostr-alert | [Light](after/native-nostr-alert-light.png) | [Dark](after/native-nostr-alert-dark.png) |
| profile | [Light](after/native-profile-light.png) | [Dark](after/native-profile-dark.png) |
| publish-note | [Light](after/native-publish-note-light.png) | [Dark](after/native-publish-note-dark.png) |
| published | [Light](after/native-published-light.png) | [Dark](after/native-published-dark.png) |
| publishing | [Light](after/native-publishing-light.png) | [Dark](after/native-publishing-dark.png) |
| quick-look | [Light](after/native-quick-look-light.png) | [Dark](after/native-quick-look-dark.png) |
| reader | [Light](corrections/native-reader-light.png) | [Dark](corrections/native-reader-dark.png) |
| reference | [Light](after/native-reference-light.png) | [Dark](after/native-reference-dark.png) |
| restore-account | [Light](after/native-restore-account-light.png) | [Dark](after/native-restore-account-dark.png) |
| review-images | [Light client (native capture distorted)](corrections/review-images-light.png) | [Dark](corrections/native-review-images-dark.png) |
| save-destination | [Light](after/native-save-destination-light.png) | [Dark](after/native-save-destination-dark.png) |
| save-draft | [Light](after/native-save-draft-light.png) | [Dark](after/native-save-draft-dark.png) |
| settings | [Light](after/native-settings-light.png) | [Dark](after/native-settings-dark.png) |
| share-media | [Light](after/native-share-media-light.png) | [Dark](after/native-share-media-dark.png) |
| source-settings | [Light](after/native-source-settings-light.png) | [Dark](after/native-source-settings-dark.png) |

## German layout checks

| Group | Light | Dark |
| --- | --- | --- |
| Imports | [Light](german/native-imports-light.png) | [Dark](german/native-imports-dark.png) |
| Publish as Article | [Light](german/native-publishing-light.png) | [Dark](german/native-publishing-dark.png) |
| Editor and Drafts | [Light](german/native-editor-drafts-light.png) | [Dark](german/native-editor-drafts-dark.png) |
| Media Library | [Light](german/native-media-light.png) | [Dark](german/native-media-dark.png) |
| Membership | [Light](german/native-membership-light.png) | [Dark](german/native-membership-dark.png) |

## Readiness

Native screenshot review corrected mixed Light/Dark regions, selection tint on source images, non-neutral readers/profile cards, stale icons, and a combo-arrow defect. Delayed native exposure capture checks black-on-white QR modules in both appearances. An earlier full-batch Dark payment native capture still has an empty QR backing rectangle despite correct client pixels; it is retained as an explicitly failed capture, not evidence of successful native QR rendering. The table links the separate final payment capture. No screenshot was recolored or retouched.

One native capture command failed, and some final native frame captures caught a small, transformed compositor window. These are recorded in `native_capture_limitations` in the root manifest; the table explicitly links the matching real Qt client capture instead. They are not claimed as successful native-frame acceptance. The final isolated Dark payment capture visibly contains black QR modules on white.

Two earlier native batches exited with SIGSEGV; the first recorded stack was inside Qt `QScreen::handle()` while constructing a dialog button box. The capture helper now keeps QApplication alive between independent fixtures and explicitly deletes closed windows. The recorded final batch completed. The precise cause of the earlier screen-handling crashes was not established; manual multi-display transitions remain part of outstanding acceptance. No crash report containing machine-specific metadata is published here.

The PR remains draft for manual VoiceOver/Full Keyboard Access, actual system preference changes, Windows/Linux desktop composition and physical fractional-scale checks. Offscreen tests and CI are described by their actual platform and do not substitute for those manual checks.
