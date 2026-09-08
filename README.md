# Trae Literature Interpretation | Trae 文献解读

A Zotero plugin that generates structured literature interpretation HTML reports directly from PDF attachments, with figures/tables embedded as base64, 10-section accordion layout, and full Zotero note compatibility.

一个 Zotero 插件，直接从 PDF 附件生成结构化文献解读 HTML 报告，图表 base64 内嵌，10 板块折叠布局，兼容 Zotero 笔记展开/折叠和 PDF 转换。

## Features | 功能

- **Auto Interpretation** | **自动解读**: One-click PDF → 10-section HTML report via DeepSeek API, saved as Zotero child note
- **Manual Mode** | **手动模式**: Extract PDF text + figures + generate prompt for external LLM (TRAE / ChatGPT / Claude)
- **Import HTML** | **导入 HTML**: Import external HTML reports as Zotero notes
- **Update Check** | **更新检查**: Built-in update mechanism via GitHub releases
- **Figure Extraction** | **图表提取**: PyMuPDF-based precise cropping (4x zoom, 8-10pt margins, caption-only, no surrounding text)
- **Zotero-compatible HTML** | **Zotero 兼容 HTML**: Native `<details>/<summary>` accordion, `<a>` tag onclick controls, base64 embedded images

## Screenshots | 截图

> Add screenshots here after testing

## Requirements | 前置条件

- **Zotero** 7.0+ (tested on Zotero 10.0.1)
- **Python** 3.8+
- **PyMuPDF** (`pip install pymupdf`)
- **DeepSeek API Key** (only for auto mode) — get one at [platform.deepseek.com](https://platform.deepseek.com/)

## Installation | 安装

1. Download the latest `.xpi` from [Releases](../../releases)
2. Zotero → Tools → Add-ons (Ctrl+Shift+A)
3. Gear icon → Install Add-on From File
4. Select the `.xpi` file
5. Restart Zotero

## Usage | 使用方法

### Auto Mode | 自动模式

1. Select a library item with a PDF attachment
2. Right-click → 文献解读 → 自动生成解读 (DeepSeek API)
3. Wait 1-3 minutes (depends on PDF length and API response time)
4. The interpretation HTML is automatically saved as a child note

### Manual Mode | 手动模式

1. Right-click → 文献解读 → 导出PDF+提取图表 (手动模式)
2. The script extracts PDF text and figures, generating:
   - `template.html` — HTML template with embedded figures
   - `prompt.txt` — structured interpretation prompt
   - `figures/` — individual figure files
3. Paste `prompt.txt` content into TRAE / ChatGPT / Claude
4. Save the LLM response as `response.txt`
5. Run: `python lit_interp_engine.py --mode build-final --response response.txt --output final.html --pdf original.pdf`
6. Right-click → 文献解读 → 导入HTML报告 → select `final.html`

### Settings | 设置

Right-click → 文献解读 → 设置...

| Parameter | Default | Description |
|-----------|---------|-------------|
| DeepSeek API Key | (empty) | Leave empty for manual mode only |
| API Base URL | `https://api.deepseek.com/v1` | API endpoint |
| Model | `deepseek-chat` | Also supports `deepseek-reasoner` |
| Python Path | `python` | Full path like `C:\Python311\python.exe` |
| Update Source | (auto) | Local path to update.json |

## 10-Section Interpretation Standard | 10 板块解读标准

Generated HTML reports follow this structure with `<details>/<summary>` accordion layout:

| # | Section | Description |
|---|---------|-------------|
| 1 | 论文基本信息 | Title, authors, journal, DOI, core finding |
| 2 | 研究背景与问题 | Field status, gaps, hypothesis |
| 3 | 实验设计 | Study type, model, groups, timeline, techniques |
| 4 | 核心结果 | Per Figure/Table: observation → data → statistics |
| 5 | 讨论要点 | Conclusions, comparisons, mechanisms |
| 6 | 方法学评价 | 10-dimension rating (A/B/C) |
| 7 | 写作提炼 | Narrative structure, templates with examples |
| 8 | 局限性与未解决问题 | Original + reviewer-added limitations |
| 9 | 临床/研究意义 | Clinical value, future directions |
| 10 | 总结 | Full logic chain: background → hypothesis → design → results → conclusion |

## File Structure | 文件结构

```
zotero-lit-interp/
├── manifest.json              # Plugin manifest (Zotero 7+ WebExtension)
├── bootstrap.js               # Lifecycle + menu + main logic
├── prefs.js                   # Default preferences
├── update.json                # Update manifest for GitHub-hosted updates
├── icons/
│   ├── icon-16.png            # Toolbar icon
│   ├── icon-48.png            # Add-ons list
│   ├── icon-96.png            # High-res
│   └── icon-128.png           # Large icon
├── scripts/
│   └── lit_interp_engine.py   # Python backend (PDF extraction + LLM + HTML)
├── README.md
├── LICENSE
├── CHANGELOG.md
└── .gitignore
```

## Technical Details | 技术细节

- **PDF Text Extraction**: PyMuPDF `page.get_text()`
- **Figure Detection**: Regex-based caption matching (Fig./Table), text block coordinate positioning
- **Figure Cropping**: 4x zoom (Matrix(4.0, 4.0)), 8-10pt white margins, excludes surrounding body text
- **LLM Call**: OpenAI-compatible API (DeepSeek), temperature=0.3, max_tokens=8000
- **HTML Layout**: Native `<details>/<summary>` accordion — works in Zotero note viewer and PDF converters without JavaScript
- **Image Embedding**: base64 data URIs — single self-contained file, no external dependencies
- **Batch Controls**: `<a href="javascript:void(0)" onclick="...">` format (Zotero strips `onclick` from `<button>` but preserves on `<a>` tags)

## Compatibility | 兼容性

- Zotero 7.0+ (tested on 10.0.1)
- Windows / macOS / Linux
- Python 3.8+
- PyMuPDF (pymupdf) 1.23+

## FAQ | 常见问题

**Q: "Python script execution failed" | Python 脚本执行失败**

Check Python path in Settings. Fill the full path like `C:\Python311\python.exe`. Run `python -c "import pymupdf"` in terminal to verify PyMuPDF installation.

**Q: Auto mode output is incomplete | 自动模式内容不完整**

DeepSeek API has token limits (default max_tokens=8000). Long papers may be truncated. Adjust `max_text_len` and `max_tokens` in `lit_interp_engine.py`.

**Q: No figures detected | 图表未检测到**

Some PDFs store figures as vector graphics rather than embedded images, which may cause detection failures. Check the `figures/` directory in manual mode output.

**Q: Notes won't expand in Zotero | 笔记无法展开**

This plugin generates HTML with native `<details>/<summary>` elements, which work without JavaScript. If you encounter this issue, ensure you're using the latest version of the plugin.

## Development | 开发

### Build from Source | 从源码构建

```bash
# Clone the repository
git clone https://github.com/ICIRICECIECR/Zotero-Literature-Analizer.git
cd Zotero-Literature-Analizer

# Create XPI
python -c "
import zipfile, os
with zipfile.ZipFile('trae-lit-interp.xpi', 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('zotero-lit-interp'):
        for f in files:
            filepath = os.path.join(root, f)
            arcname = os.path.relpath(filepath, 'zotero-lit-interp').replace('\\\\', '/')
            zf.write(filepath, arcname)
"
```

### Publish a New Release | 发布新版本

1. Update `version` in `manifest.json`
2. Update `CHANGELOG.md`
3. Add new version entry in `update.json`
4. Build the new `.xpi`
5. Upload to GitHub Releases with tag `v{version}`
6. Update `update_link` in `update.json` to the new release URL

## License

[MIT License](LICENSE)

## Author

Trae
