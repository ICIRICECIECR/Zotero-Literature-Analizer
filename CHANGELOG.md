# Changelog

## [1.0.0] - 2026-09-08

### Added
- Initial release
- Auto interpretation mode via DeepSeek API (10-section structured report)
- Manual mode: PDF text + figure extraction with prompt generation
- HTML report import as Zotero child notes
- 10-section interpretation standard with `<details>/<summary>` accordion layout
- Figure extraction: PyMuPDF 4x zoom, 8-10pt margins, caption-only cropping
- Base64 embedded images in single self-contained HTML files
- Zotero-compatible HTML: native `<details>/<summary>`, `<a>` tag onclick controls
- Update mechanism: GitHub-hosted `update.json` + local fallback
- Settings dialog: API Key, Python path, model, API base URL, update source
- Plugin icons (16/48/96/128px)
- MenuManager API support (Zotero 8+) with legacy fallback
- MIT License
