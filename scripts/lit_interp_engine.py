#!/usr/bin/env python3
"""
Trae Literature Interpretation Engine
======================================
Extracts PDF content (text + figures/tables), calls DeepSeek LLM API
(or generates prompt for manual mode), and produces a self-contained
HTML report following the 10-section 文献解读 standard.

Usage:
  Auto mode:
    python lit_interp_engine.py --pdf paper.pdf --output report.html \
        --mode auto --api-key sk-xxx --api-base https://api.deepseek.com/v1 \
        --model deepseek-chat

  Manual mode:
    python lit_interp_engine.py --pdf paper.pdf --output template.html \
        --mode manual --prompt-file prompt.txt
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)


# ============================================================
# 1. PDF Text Extraction
# ============================================================

def extract_text(pdf_path):
    """Extract full text from PDF, return (full_text, per_page_text)."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    full_text = "\n".join(pages)
    doc.close()
    return full_text, pages


# ============================================================
# 2. Figure/Table Detection and Cropping
# ============================================================

def detect_figures_and_tables(pdf_path):
    """
    Detect figure and table regions in PDF using heuristic approach.
    Returns list of dicts: {page, rect, name, caption}
    """
    doc = fitz.open(pdf_path)
    results = []
    seen_names = set()

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_w = page.rect.width
        page_h = page.rect.height
        blocks = page.get_text("blocks")
        images = page.get_images(full=True)

        for block in blocks:
            x0, y0, x1, y1 = block[0], block[1], block[2], block[3]
            text = block[4] if len(block) > 4 else ""
            text_stripped = text.strip()

            # Detect figure caption: "Fig. 1", "Figure 1", etc.
            fig_match = re.match(
                r'^(?:Fig\.?\s*(\d+)|Figure\s*(\d+))[\.\s]', 
                text_stripped, re.IGNORECASE
            )
            # Detect table caption: "Table 1", "Table 2", etc.
            table_match = re.match(
                r'^Table\s*(\d+)[\.\s]', 
                text_stripped, re.IGNORECASE
            )

            if fig_match:
                num = fig_match.group(1) or fig_match.group(2)
                name = f"fig{num}"
                if name in seen_names:
                    continue
                crop = _find_figure_crop(
                    page, x0, y0, x1, y1, images, blocks, page_idx, num, page_w, page_h
                )
                if crop:
                    seen_names.add(name)
                    results.append(crop)

            elif table_match:
                num = table_match.group(1)
                name = f"table{num}"
                if name in seen_names:
                    continue
                crop = _find_table_crop(
                    page, x0, y0, x1, y1, blocks, page_idx, num, page_w, page_h
                )
                if crop:
                    seen_names.add(name)
                    results.append(crop)

    doc.close()
    return results


def _find_figure_crop(page, cap_x0, cap_y0, cap_x1, cap_y1, 
                      images, blocks, page_idx, num, page_w, page_h):
    """Find the image region for a figure caption."""
    zoom = 4
    mat = fitz.Matrix(zoom, zoom)

    # Strategy 1: Find image bbox near caption
    for img in images:
        try:
            img_rect = page.get_image_bbox(img)
            if img_rect and img_rect.is_valid:
                # Image should be above or near caption
                if abs(img_rect.y1 - cap_y0) < 60 or abs(img_rect.y0 - cap_y1) < 60:
                    x0 = max(0, min(img_rect.x0, cap_x0) - 10)
                    y0 = max(0, min(img_rect.y0, cap_y0) - 10)
                    x1 = min(page_w, max(img_rect.x1, cap_x1) + 10)
                    y1 = min(page_h, max(img_rect.y1, cap_y1) + 10)
                    rect = fitz.Rect(x0, y0, x1, y1)
                    pix = page.get_pixmap(matrix=mat, clip=rect)
                    img_data = pix.tobytes("png")
                    return {
                        "page": page_idx,
                        "name": f"fig{num}",
                        "caption": page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))[:200].strip(),
                        "b64": base64.b64encode(img_data).decode(),
                        "mime": "image/png"
                    }
        except Exception:
            continue

    # Strategy 2: Crop region above caption (figure panels often above)
    above_blocks = [b for b in blocks if b[3] <= cap_y0 and b[1] >= cap_y0 - 300]
    if above_blocks:
        min_y0 = min(b[1] for b in above_blocks)
        min_x0 = min(b[0] for b in above_blocks)
        max_x1 = max(b[2] for b in above_blocks)
        x0 = max(0, min_x0 - 10)
        y0 = max(0, min_y0 - 10)
        x1 = min(page_w, max(max_x1, cap_x1) + 10)
        y1 = min(page_h, cap_y1 + 10)
        rect = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        img_data = pix.tobytes("png")
        return {
            "page": page_idx,
            "name": f"fig{num}",
            "caption": page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))[:200].strip(),
            "b64": base64.b64encode(img_data).decode(),
            "mime": "image/png"
        }

    return None


def _find_table_crop(page, cap_x0, cap_y0, cap_x1, cap_y1, 
                     blocks, page_idx, num, page_w, page_h):
    """Find the table region for a table caption."""
    zoom = 4
    mat = fitz.Matrix(zoom, zoom)

    # Table content is usually below the caption (or the caption is above)
    # Also handle case where caption is below the table
    below_blocks = [b for b in blocks if b[1] >= cap_y1 and b[1] < cap_y1 + 400]
    
    if below_blocks:
        min_x0 = min(min(b[0] for b in below_blocks), cap_x0)
        min_y0 = cap_y0
        max_x1 = max(max(b[2] for b in below_blocks), cap_x1)
        max_y1 = max(b[3] for b in below_blocks)
        
        x0 = max(0, min_x0 - 10)
        y0 = max(0, min_y0 - 10)
        x1 = min(page_w, max_x1 + 10)
        y1 = min(page_h, max_y1 + 10)
        
        rect = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        img_data = pix.tobytes("png")
        return {
            "page": page_idx,
            "name": f"table{num}",
            "caption": page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))[:200].strip(),
            "b64": base64.b64encode(img_data).decode(),
            "mime": "image/png"
        }

    # Fallback: caption might be above, table below
    # Or caption below, table above
    above_blocks = [b for b in blocks if b[3] <= cap_y0 and b[1] >= cap_y0 - 300]
    if above_blocks:
        min_y0 = min(b[1] for b in above_blocks)
        min_x0 = min(min(b[0] for b in above_blocks), cap_x0)
        max_x1 = max(max(b[2] for b in above_blocks), cap_x1)
        
        x0 = max(0, min_x0 - 10)
        y0 = max(0, min_y0 - 10)
        x1 = min(page_w, max_x1 + 10)
        y1 = min(page_h, cap_y1 + 10)
        
        rect = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        img_data = pix.tobytes("png")
        return {
            "page": page_idx,
            "name": f"table{num}",
            "caption": page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))[:200].strip(),
            "b64": base64.b64encode(img_data).decode(),
            "mime": "image/png"
        }

    return None


# ============================================================
# 3. LLM Prompt Building
# ============================================================

def build_prompt(full_text, figure_info, max_text_len=12000):
    """Build the 10-section interpretation prompt."""
    text_preview = full_text[:max_text_len]
    if len(full_text) > max_text_len:
        text_preview += "\n\n[... 文本已截断 ...]"

    fig_list = "\n".join([
        f"- {f['name']}: {f['caption']}" 
        for f in figure_info
    ]) if figure_info else "（未检测到明确图表）"

    prompt = f"""你是一位医学文献解读专家。请按照以下10个板块对这篇论文进行系统性解读。

## 输出格式要求
- 每个板块用 `## N. 标题` 开头（N为1-10的数字）
- 中文为主，专业术语保留英文原文
- 数据对比优先用Markdown表格
- 方法学每个维度给出明确评级（优势/不足/中立）
- 写作提炼需附原文例句作为佐证
- 全篇杜绝营销式语言，直接陈述

## 10个板块结构

## 1. 论文基本信息
标题、作者（第一作者+通讯作者）、期刊、年份、DOI、一句话概括核心发现

## 2. 研究背景与问题
领域现状、既往研究空白、研究假设/目的

## 3. 实验设计
研究类型、模型与对象、分组与对照、时间点、关键技术手段

## 4. 核心结果（按 Figure/Table 展开）
以文献中呈现的每个 Figure/Table 为单位组织，包含：图表展示内容→观察现象→量化数据→统计意义

## 5. 讨论要点
核心结论、与既往研究对比、机制解释

## 6. 方法学评价
从以下10个维度系统评价，每项给出优势/不足/中立判断并附理由，结尾给出整体评级（A/B/C）：
研究设计合理性、对照组设置、样本量与统计功效、随机化与盲法、模型/人群代表性、
测量方法可靠性、混杂因素控制、统计方法恰当性、外部效度/泛化性、报告完整性

## 7. 写作提炼
Introduction 叙事结构与段落逻辑功能、Discussion 组织策略、可复用写作模板（附原文例句佐证）

## 8. 局限性与未解决问题
原文自述局限性 + 解读者补充（标注"解读补充"）+ 尚未解决的科学问题

## 9. 临床/研究意义
临床实践指导价值、后续研究方向、转化潜力

## 10. 总结（逻辑链）
将文献从背景→假设→设计→结果→结论的完整逻辑链以流程化方式呈现

## 论文全文
{text_preview}

## 检测到的图表
{fig_list}

请按以上结构输出完整解读。"""

    return prompt


# ============================================================
# 4. DeepSeek API Call
# ============================================================

def call_llm_api(prompt, api_key, api_base, model, max_tokens=8000):
    """Call DeepSeek (OpenAI-compatible) API."""
    url = api_base.rstrip("/") + "/chat/completions"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一位专业的医学文献解读专家，擅长系统性分析和评价学术论文。"},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + api_key)

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        print(f"API Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"API call failed: {e}", file=sys.stderr)
        sys.exit(1)


# ============================================================
# 5. Response Parsing
# ============================================================

def parse_sections(response):
    """Parse LLM response into sections by ## headers."""
    sections = {}
    current_num = None
    current_title = ""
    current_content = []

    for line in response.split("\n"):
        match = re.match(r'^##\s*(\d+)\.\s*(.+)', line)
        if match:
            if current_num:
                sections[current_num] = {
                    "title": current_title,
                    "content": "\n".join(current_content).strip()
                }
            current_num = int(match.group(1))
            current_title = match.group(2).strip()
            current_content = []
        else:
            current_content.append(line)

    if current_num:
        sections[current_num] = {
            "title": current_title,
            "content": "\n".join(current_content).strip()
        }

    return sections


# ============================================================
# 6. HTML Generation
# ============================================================

CSS = """
  :root {
    --bg: #f8fafc; --card-bg: #ffffff; --text: #1e293b;
    --text-secondary: #64748b; --border: #e2e8f0;
    --border-active: #93c5fd; --accent: #2563eb;
    --accent-light: #dbeafe; --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.08); --radius: 10px;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: var(--font-sans); background: var(--bg);
    color: var(--text); line-height: 1.7; padding: 40px 20px;
    max-width: 960px; margin: 0 auto; font-size: 15px; }
  .hero { background: linear-gradient(135deg, #1e40af 0%, #3b82f6 50%, #06b6d4 100%);
    color: white; padding: 48px 40px; border-radius: var(--radius);
    margin-bottom: 32px; box-shadow: var(--shadow-md); }
  .hero h1 { font-size: 22px; font-weight: 700; line-height: 1.4; margin-bottom: 16px; }
  .hero-summary { margin-top: 16px; font-size: 14px; opacity: 0.95;
    padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.25); }
  .toc { background: var(--card-bg); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px 28px; margin-bottom: 28px;
    box-shadow: var(--shadow-sm); }
  .toc h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
  .toc-list { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px; }
  .toc-list a { color: var(--text-secondary); text-decoration: none; font-size: 13px;
    padding: 4px 0; border-bottom: 1px dashed transparent; transition: all 0.15s; }
  .toc-list a:hover { color: var(--accent); border-bottom-color: var(--accent); }
  .toc-controls { display: flex; gap: 8px; margin-top: 14px; }
  .toc-btn { font-size: 12px; padding: 6px 14px; border: 1px solid var(--border);
    border-radius: 6px; background: var(--card-bg); color: var(--text-secondary);
    cursor: pointer; font-weight: 500; transition: all 0.15s;
    text-decoration: none; display: inline-flex; align-items: center; }
  .toc-btn:hover { border-color: var(--accent); color: var(--accent); }
  details.accordion-item { background: var(--card-bg); border: 1px solid var(--border);
    border-radius: var(--radius); margin-bottom: 12px; overflow: hidden;
    transition: box-shadow 0.2s, border-color 0.2s; }
  details.accordion-item[open] { border-color: var(--border-active); box-shadow: var(--shadow-md); }
  details.accordion-item[open] summary.accordion-header { background: #f8fafc; }
  summary.accordion-header { list-style: none; padding: 18px 24px; cursor: pointer;
    display: flex; align-items: center; justify-content: space-between;
    font-weight: 600; font-size: 15px; user-select: none; }
  summary.accordion-header::-webkit-details-marker { display: none; }
  summary.accordion-header::marker { display: none; }
  .accordion-title { display: flex; align-items: center; gap: 10px; }
  .accordion-num { background: var(--accent-light); color: var(--accent);
    width: 28px; height: 28px; border-radius: 50%; display: inline-flex;
    align-items: center; justify-content: center; font-size: 13px; font-weight: 700; }
  .accordion-chevron { width: 20px; height: 20px; transition: transform 0.2s;
    color: var(--text-secondary); }
  details.accordion-item[open] .accordion-chevron { transform: rotate(180deg); }
  .accordion-content { padding: 0 24px 24px; border-top: 1px solid var(--border); padding-top: 20px; }
  .accordion-content h3 { font-size: 14px; font-weight: 600; color: var(--accent); margin: 18px 0 10px; }
  .accordion-content h3:first-child { margin-top: 0; }
  .accordion-content p { margin-bottom: 10px; }
  .accordion-content ul { margin: 8px 0 12px 20px; }
  .accordion-content li { margin-bottom: 4px; }
  .table-wrap { overflow-x: auto; margin: 16px 0; border: 1px solid var(--border); border-radius: 8px; }
  .data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .data-table th { background: #f1f5f9; padding: 10px 12px; text-align: left;
    font-weight: 600; border-bottom: 2px solid var(--border); white-space: nowrap; }
  .data-table td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; }
  .data-table tr:last-child td { border-bottom: none; }
  .figure-block { margin: 18px 0; text-align: center; }
  .figure-block img { max-width: 100%; height: auto; border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid var(--border); }
  .figure-caption { font-size: 12px; color: var(--text-secondary); margin-top: 8px;
    text-align: center; font-style: italic; }
  .footer { text-align: center; color: var(--text-secondary); font-size: 12px;
    margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border); }
  @media (max-width: 640px) {
    body { padding: 20px 12px; font-size: 14px; }
    .toc-list { grid-template-columns: 1fr; }
    .accordion-content { padding: 0 16px 20px; padding-top: 16px; }
    summary.accordion-header { padding: 14px 16px; font-size: 14px; }
  }
"""

SECTION_TITLES = [
    "论文基本信息",
    "研究背景与问题",
    "实验设计",
    "核心结果",
    "讨论要点",
    "方法学评价",
    "写作提炼",
    "局限性与未解决问题",
    "临床/研究意义",
    "总结（逻辑链）"
]

def generate_html(sections, figures, paper_title="文献解读"):
    """Generate self-contained HTML with base64-embedded images."""
    
    # Build TOC
    toc_links = ""
    for i in range(1, 11):
        title = sections.get(i, {}).get("title", SECTION_TITLES[i-1])
        toc_links += f'<a href="#sec{i}" onclick="document.getElementById(\'sec{i}\').open=true;">{i}. {title}</a>\n'

    # Build accordion sections
    accordion = ""
    for i in range(1, 11):
        title = sections.get(i, {}).get("title", SECTION_TITLES[i-1])
        content = sections.get(i, {}).get("content", "（待补充）")
        
        # Insert figures in section 4 (核心结果)
        if i == 4 and figures:
            content = _insert_figures_into_content(content, figures)
        
        # Convert markdown tables to HTML
        content = _convert_markdown_tables(content)
        
        # Convert markdown bold to HTML
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        
        # Convert markdown headers
        content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=re.MULTILINE)
        
        # Convert markdown lists
        content = re.sub(r'^- (.+)$', r'<li>\1</li>', content, flags=re.MULTILINE)
        content = re.sub(r'(<li>.*?</li>\n?)+', lambda m: f'<ul>{m.group(0)}</ul>', content, flags=re.MULTILINE)
        
        # Convert paragraphs
        content = re.sub(r'^([^<\n].+)$', r'<p>\1</p>', content, flags=re.MULTILINE)
        
        open_attr = " open" if i == 1 else ""
        accordion += f'''
<details class="accordion-item" id="sec{i}"{open_attr}>
  <summary class="accordion-header">
    <div class="accordion-title"><span class="accordion-num">{i}</span>{title}</div>
    <svg class="accordion-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
  </summary>
  <div class="accordion-content">
{content}
  </div>
</details>'''

    # Build figure blocks for section 4 if no inline match
    figure_blocks = ""
    if figures:
        for fig in figures:
            figure_blocks += f'''
    <div class="figure-block">
      <img src="data:{fig['mime']};base64,{fig['b64']}" alt="{fig['name']}">
      <div class="figure-caption">{fig['name']} · {fig['caption']}</div>
    </div>'''

    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{paper_title}</title>
<style>{CSS}</style>
</head>
<body>

<div class="hero">
  <h1>{paper_title}</h1>
  <div class="hero-summary">
    <strong>解读日期：</strong>{date_str}　|　<strong>解读标准：</strong>10板块文献解读
  </div>
</div>

<div class="toc">
  <h2>📋 目录</h2>
  <div class="toc-list">
{toc_links}
  </div>
  <div class="toc-controls">
    <a class="toc-btn" href="javascript:void(0)" onclick="var d=document.querySelectorAll('details.accordion-item');for(var i=0;i<d.length;i++){{d[i].open=true;}}return false;">全部展开</a>
    <a class="toc-btn" href="javascript:void(0)" onclick="var d=document.querySelectorAll('details.accordion-item');for(var i=0;i<d.length;i++){{d[i].open=false;}}return false;">全部折叠</a>
  </div>
</div>

{accordion}

{f'<details class="accordion-item" id="sec-figures" open><summary class="accordion-header"><div class="accordion-title"><span class="accordion-num">📋</span>原文图表</div><svg class="accordion-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg></summary><div class="accordion-content">{figure_blocks}</div></details>' if figures else ''}

<div class="footer">
  <p>文献解读 · 按照「文献解读」标准执行 · 生成于 {date_str}</p>
</div>

</body>
</html>'''

    return html


def _insert_figures_into_content(content, figures):
    """Insert figure blocks into section 4 content at appropriate positions."""
    for fig in figures:
        # Try to find where the figure is mentioned in the content
        pattern = re.compile(
            r'(Fig\.?\s*' + re.escape(fig['name'].replace('fig', '')) + 
            r'|Figure\s*' + re.escape(fig['name'].replace('fig', '')) + 
            r'|' + re.escape(fig['name']) + ')',
            re.IGNORECASE
        )
        fig_html = f'''
<div class="figure-block">
  <img src="data:{fig['mime']};base64,{fig['b64']}" alt="{fig['name']}">
  <div class="figure-caption">{fig['name']} · {fig['caption']}</div>
</div>'''
        match = pattern.search(content)
        if match:
            pos = match.start()
            content = content[:pos] + fig_html + "\n" + content[pos:]
        else:
            # Append at end
            content += fig_html + "\n"
    return content


def _convert_markdown_tables(content):
    """Convert markdown tables to HTML tables."""
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and "---" in lines[i+1]:
            # Found a table header
            table_lines = [line]
            i += 1
            # Skip separator line
            if i < len(lines) and "---" in lines[i]:
                i += 1
            # Collect table rows
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            # Convert to HTML
            html_table = _markdown_table_to_html(table_lines)
            result.append(html_table)
        else:
            result.append(line)
            i += 1
    return "\n".join(result)


def _markdown_table_to_html(table_lines):
    """Convert markdown table lines to HTML."""
    if not table_lines:
        return ""
    
    headers = [h.strip() for h in table_lines[0].split("|")[1:-1]]
    rows = []
    for line in table_lines[1:]:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        rows.append(cells)
    
    html = '<div class="table-wrap"><table class="data-table"><tr>'
    for h in headers:
        html += f"<th>{h}</th>"
    html += "</tr>"
    for row in rows:
        html += "<tr>"
        for cell in row:
            html += f"<td>{cell}</td>"
        html += "</tr>"
    html += "</table></div>"
    return html


# ============================================================
# 7. Manual Mode: Generate Prompt + Template
# ============================================================

def generate_manual_output(full_text, figures, prompt_path, output_path):
    """Generate prompt file and HTML template for manual mode."""
    
    # Write prompt file
    prompt = build_prompt(full_text, figures)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    
    # Generate HTML template with figures embedded but empty text sections
    sections = {i: {"title": SECTION_TITLES[i-1], "content": "（请将 LLM 解读结果粘贴到此板块）"} for i in range(1, 11)}
    html = generate_html(sections, figures, paper_title="文献解读（模板）")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    # Also save figures as individual files for reference
    fig_dir = os.path.join(os.path.dirname(output_path), "figures")
    os.makedirs(fig_dir, exist_ok=True)
    for fig in figures:
        fig_path = os.path.join(fig_dir, f"{fig['name']}.png")
        with open(fig_path, "wb") as f:
            f.write(base64.b64decode(fig["b64"]))
    
    print(f"Prompt written to: {prompt_path}")
    print(f"Template HTML written to: {output_path}")
    print(f"Individual figures saved to: {fig_dir}")
    print(f"\n下一步:")
    print(f"1. 打开 {prompt_path}")
    print(f"2. 将内容粘贴到 TRAE / ChatGPT / DeepSeek 处理")
    print(f"3. 将 LLM 返回的解读结果保存为 response.txt")
    print(f"4. 运行: python lit_interp_engine.py --build-final --response response.txt --output final.html" +
          (f" --pdf-figures {fig_dir}" if figures else ""))
    print(f"   或直接在 Zotero 中用「导入HTML报告」导入生成的 HTML")


# ============================================================
# 8. Build Final HTML from Response
# ============================================================

def build_final_html(response_path, figures, output_path, paper_title="文献解读"):
    """Build final HTML from LLM response."""
    with open(response_path, "r", encoding="utf-8") as f:
        response = f.read()
    
    sections = parse_sections(response)
    html = generate_html(sections, figures, paper_title)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"Final HTML written to: {output_path}")


# ============================================================
# 9. Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Trae Literature Interpretation Engine")
    parser.add_argument("--pdf", help="Path to PDF file")
    parser.add_argument("--output", required=True, help="Output HTML path")
    parser.add_argument("--mode", choices=["auto", "manual", "build-final"], default="manual")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-base", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--prompt-file", default="prompt.txt")
    parser.add_argument("--response", help="LLM response file (for build-final mode)")
    
    args = parser.parse_args()
    
    if args.mode == "build-final":
        # Build final HTML from response
        if not args.response:
            print("ERROR: --response required for build-final mode", file=sys.stderr)
            sys.exit(1)
        
        # Try to load figures from the PDF if provided
        figures = []
        if args.pdf:
            print("Extracting figures...")
            figures = detect_figures_and_tables(args.pdf)
        
        build_final_html(args.response, figures, args.output)
        return
    
    # For auto and manual modes, we need a PDF
    if not args.pdf:
        print("ERROR: --pdf required", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing: {args.pdf}")
    
    # Extract text
    print("Extracting text...")
    full_text, pages = extract_text(args.pdf)
    print(f"  Extracted {len(full_text)} chars from {len(pages)} pages")
    
    # Detect and crop figures
    print("Detecting figures and tables...")
    figures = detect_figures_and_tables(args.pdf)
    print(f"  Found {len(figures)} figures/tables: {[f['name'] for f in figures]}")
    
    if args.mode == "manual":
        # Manual mode: generate prompt + template
        print("\n--- Manual Mode ---")
        generate_manual_output(full_text, figures, args.prompt_file, args.output)
    
    elif args.mode == "auto":
        # Auto mode: call LLM API
        if not args.api_key:
            print("ERROR: --api-key required for auto mode", file=sys.stderr)
            sys.exit(1)
        
        print("\n--- Auto Mode ---")
        print("Building prompt...")
        prompt = build_prompt(full_text, figures)
        
        print(f"Calling DeepSeek API ({args.model})...")
        response = call_llm_api(prompt, args.api_key, args.api_base, args.model)
        print(f"  Response: {len(response)} chars")
        
        print("Parsing sections...")
        sections = parse_sections(response)
        print(f"  Parsed {len(sections)} sections")
        
        print("Generating HTML...")
        html = generate_html(sections, figures)
        
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        
        file_size = os.path.getsize(args.output)
        print(f"\nDone! HTML written to: {args.output}")
        print(f"  File size: {file_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
