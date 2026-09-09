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
                        "caption": re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))[:200].strip(),
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
            "caption": re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))[:200].strip(),
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
            "caption": re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))[:200].strip(),
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
            "caption": re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))[:200].strip(),
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

格式要求：研究假设、关键结论、重要发现等，请用 `> 标题：内容` 的引用格式（独占一行）呈现，将渲染为高亮提示框；具体数据尽量用列表（- 开头）或 Markdown 表格呈现。

## 1. 论文基本信息
一句话概括核心发现（标题/作者/期刊/DOI 已由系统自动填入，无需重复输出）

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
  --bg: #f8f9fa; --card-bg: #ffffff; --border: #e2e8f0; --border-active: #3b82f6;
  --text: #1a202c; --text-secondary: #64748b; --text-muted: #94a3b8;
  --accent: #2563eb; --accent-light: #dbeafe; --accent-dark: #1e40af;
  --green: #16a34a; --green-light: #dcfce7;
  --amber: #d97706; --amber-light: #fef3c7;
  --red: #dc2626; --red-light: #fee2e2;
  --code-bg: #f1f5f9;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05);
  --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -4px rgba(0,0,0,0.05);
  --radius: 10px;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: "SF Mono", "Fira Code", "Fira Mono", "Roboto Mono", Consolas, monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font-sans); background: var(--bg); color: var(--text); line-height: 1.7; -webkit-font-smoothing: antialiased; }
.container { max-width: 900px; margin: 0 auto; padding: 40px 24px 80px; }

/* Hero */
.hero { background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%); color: white; padding: 40px 36px; border-radius: 16px; margin-bottom: 28px; box-shadow: var(--shadow-lg); }
.hero-badge { display: inline-block; background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.25); padding: 4px 14px; border-radius: 20px; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 16px; text-transform: uppercase; }
.hero h1 { font-size: 22px; font-weight: 700; line-height: 1.4; margin-bottom: 12px; }
.hero-meta { font-size: 13px; opacity: 0.85; line-height: 1.6; }
.hero-meta strong { font-weight: 600; }
.hero-doi { margin-top: 10px; font-size: 12px; opacity: 0.7; font-family: var(--font-mono); }
.hero-summary { margin-top: 16px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.15); font-size: 14px; line-height: 1.6; }

/* TOC */
.toc { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 24px; margin-bottom: 24px; box-shadow: var(--shadow-sm); }
.toc-title { font-size: 13px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
.toc-list { list-style: none; display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
.toc-list li a { display: flex; align-items: center; gap: 8px; text-decoration: none; color: var(--text); font-size: 13px; padding: 4px 8px; border-radius: 6px; transition: background 0.15s; }
.toc-list li a:hover { background: var(--accent-light); }
.toc-list li a span:first-child { font-family: var(--font-mono); font-size: 11px; color: var(--accent); font-weight: 700; min-width: 22px; }
.toc-controls { display: flex; gap: 8px; margin-top: 14px; }
.toc-btn { font-size: 12px; padding: 6px 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--card-bg); color: var(--text-secondary); cursor: pointer; font-weight: 500; transition: all 0.15s; text-decoration: none; display: inline-flex; align-items: center; }
.toc-btn:hover { border-color: var(--accent); color: var(--accent); }

/* Details/Accordion - pure HTML5, no JS needed */
details.accordion-item { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 12px; overflow: hidden; transition: box-shadow 0.2s, border-color 0.2s; }
details.accordion-item[open] { border-color: var(--border-active); box-shadow: var(--shadow-md); }
details.accordion-item:hover { box-shadow: var(--shadow-md); }
summary.accordion-header { display: flex; align-items: center; gap: 14px; padding: 18px 24px; cursor: pointer; user-select: none; transition: background 0.15s; list-style: none; }
summary.accordion-header:hover { background: #f8fafc; }
details.accordion-item[open] summary.accordion-header { background: #f8fafc; }
summary.accordion-header::-webkit-details-marker { display: none; }
.accordion-num { font-family: var(--font-mono); font-size: 13px; font-weight: 700; color: white; background: var(--accent); width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.accordion-title { font-size: 16px; font-weight: 600; color: var(--text); flex: 1; }
.accordion-chevron { width: 20px; height: 20px; color: var(--text-muted); transition: transform 0.25s ease; flex-shrink: 0; }
details.accordion-item[open] .accordion-chevron { transform: rotate(180deg); }
.accordion-content { padding: 0 24px 24px; }

/* Content */
.accordion-content h3 { font-size: 15px; font-weight: 700; color: var(--accent-dark); margin: 20px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
.accordion-content h3:first-child { margin-top: 0; }
.accordion-content h4 { font-size: 14px; font-weight: 600; color: var(--text); margin: 16px 0 8px; }
.accordion-content p { font-size: 14px; line-height: 1.75; color: var(--text); margin-bottom: 10px; }
.accordion-content ul, .accordion-content ol { margin: 8px 0 12px 20px; }
.accordion-content li { font-size: 14px; line-height: 1.75; margin-bottom: 4px; }
.accordion-content strong { font-weight: 600; color: var(--text); }
.accordion-content em { font-style: italic; color: var(--text-secondary); }
.accordion-content code { font-family: var(--font-mono); font-size: 12px; background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }

/* Tables */
.table-wrap { overflow-x: auto; margin: 12px 0; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 12px 0; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
.data-table th { background: #f1f5f9; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 2px solid var(--border); color: var(--text); font-size: 12px; white-space: nowrap; }
.data-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text); vertical-align: top; }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: #f8fafc; }
.data-table .highlight { background: #fffbeb; font-weight: 600; }

/* Figure blocks */
.figure-block { margin: 16px 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: white; box-shadow: var(--shadow-sm); transition: box-shadow 0.2s; }
.figure-block:hover { box-shadow: var(--shadow-md); }
.figure-block img { width: 100%; display: block; }
.figure-caption { padding: 10px 16px; font-size: 12px; color: var(--text-secondary); background: #f8fafc; border-top: 1px solid var(--border); line-height: 1.5; }
.figure-caption strong { color: var(--text); }

/* Callout */
.callout { border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 0 8px 8px 0; font-size: 13px; line-height: 1.7; }
.callout-info { border-color: var(--accent); background: var(--accent-light); }
.callout-warn { border-color: var(--amber); background: var(--amber-light); }
.callout-danger { border-color: var(--red); background: var(--red-light); }
.callout-title { font-weight: 700; margin-bottom: 4px; }

/* Rating */
.rating-grid { display: grid; grid-template-columns: 1fr; gap: 8px; margin: 12px 0; }
.rating-item { display: grid; grid-template-columns: 30px 1fr auto; gap: 10px; align-items: start; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; line-height: 1.6; }
.rating-num { font-family: var(--font-mono); font-weight: 700; font-size: 13px; color: var(--text-muted); text-align: center; }
.rating-text strong { display: block; margin-bottom: 2px; }
.rating-badge { font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 12px; white-space: nowrap; }
.rating-badge.positive { background: var(--green-light); color: var(--green); }
.rating-badge.neutral { background: var(--code-bg); color: var(--text-secondary); }
.rating-badge.negative { background: var(--red-light); color: var(--red); }
.overall-rating { margin-top: 14px; padding: 14px 18px; border-radius: 8px; font-size: 14px; font-weight: 600; text-align: center; }
.overall-rating.b { background: var(--amber-light); color: var(--amber); border: 1px solid #fcd34d; }

/* Logic chain */
.logic-chain { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 20px 24px; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.9; overflow-x: auto; white-space: pre; color: var(--text); }
.logic-chain .step { color: var(--accent-dark); font-weight: 600; }
.logic-chain .arrow { color: var(--text-muted); }
.logic-chain .note { color: var(--red); }

.writing-template { background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; font-size: 13px; line-height: 1.7; margin: 10px 0; color: var(--text-secondary); font-style: italic; }
.quote-block { border-left: 3px solid var(--accent); padding: 8px 14px; margin: 10px 0; font-size: 13px; font-style: italic; color: var(--text-secondary); background: var(--accent-light); border-radius: 0 6px 6px 0; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 12px 0; }
.stat-card { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 14px; text-align: center; }
.stat-card .value { font-size: 24px; font-weight: 700; color: var(--accent); font-family: var(--font-mono); }
.stat-card .label { font-size: 11px; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.3px; }
footer { text-align: center; padding: 30px 0 10px; font-size: 12px; color: var(--text-muted); }
@media (max-width: 640px) {
  .container { padding: 20px 12px 60px; }
  .toc-list { grid-template-columns: 1fr; }
  .accordion-content { padding: 0 16px 20px; }
  summary.accordion-header { padding: 14px 16px; }
  .accordion-title { font-size: 14px; }
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

def _build_meta_table(paper_title, meta):
    """用 Zotero 条目元数据构建论文基本信息表格（参考文件风格）。"""
    rows = []
    if paper_title and paper_title != "文献解读":
        rows.append(f'<tr><td><strong>标题</strong></td><td>{paper_title}</td></tr>')
    if meta.get("authors"):
        rows.append(f'<tr><td><strong>作者</strong></td><td>{meta["authors"]}</td></tr>')
    if meta.get("journal"):
        rows.append(f'<tr><td><strong>期刊</strong></td><td>{meta["journal"]}</td></tr>')
    if meta.get("doi"):
        rows.append(f'<tr><td><strong>DOI</strong></td><td><code>{meta["doi"]}</code></td></tr>')
    if not rows:
        return ""
    return ('<table class="data-table"><tr><th style="width:140px">项目</th><th>内容</th></tr>'
            + "".join(rows) + '</table>')


def generate_html(sections, figures, paper_title="文献解读", meta=None):
    """Generate self-contained HTML with base64-embedded images.

    meta: optional dict with keys authors / journal / doi / summary,
    shown in the hero block (from Zotero item metadata).
    """
    meta = meta or {}

    # Build TOC (numbered two-column list, click auto-expands target)
    toc_links = ""
    for i in range(1, 11):
        title = sections.get(i, {}).get("title", SECTION_TITLES[i-1])
        num = f"{i:02d}"
        toc_links += (f'<li><a href="#sec{i}" onclick="var el=document.getElementById(\'sec{i}\');'
                      f'if(el){{el.open=true;}}return false;"><span>{num}</span><span>{title}</span></a></li>\n')

    # Build accordion sections
    accordion = ""
    for i in range(1, 11):
        title = sections.get(i, {}).get("title", SECTION_TITLES[i-1])
        content = sections.get(i, {}).get("content", "（待补充）")

        # sec1 论文基本信息：用 meta 构建结构化表格（参考文件风格）
        if i == 1:
            meta_table = _build_meta_table(paper_title, meta)
            if meta_table:
                content = meta_table + "\n" + content

        # Insert figures in section 4 (核心结果)
        if i == 4 and figures:
            content = _insert_figures_into_content(content, figures)

        # Convert markdown tables to HTML
        content = _convert_markdown_tables(content)

        # Convert markdown bold to HTML
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)

        # Convert markdown headers
        content = re.sub(r'^#### (.+)$', r'<h4>\1</h4>', content, flags=re.MULTILINE)
        content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=re.MULTILINE)

        # Convert markdown lists
        content = re.sub(r'^- (.+)$', r'<li>\1</li>', content, flags=re.MULTILINE)
        content = re.sub(r'(<li>.*?</li>\n?)+', lambda m: f'<ul>{m.group(0)}</ul>', content, flags=re.MULTILINE)

        # Convert markdown blockquote to callout（> 标题：内容 或 > 内容）
        content = re.sub(r'^>\s*\*\*(.+?)\*\*\s*[:：]\s*(.+)$',
                         r'<div class="callout callout-info"><div class="callout-title">\1</div>\2</div>',
                         content, flags=re.MULTILINE)
        content = re.sub(r'^>\s*(.+)$',
                         r'<div class="callout callout-info">\1</div>',
                         content, flags=re.MULTILINE)

        # Convert paragraphs
        content = re.sub(r'^([^<\n].+)$', r'<p>\1</p>', content, flags=re.MULTILINE)

        open_attr = " open" if i == 1 else ""
        accordion += f'''
<details class="accordion-item" id="sec{i}"{open_attr}>
<summary class="accordion-header"><span class="accordion-num">{i}</span><span class="accordion-title">{title}</span><svg class="accordion-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg></summary>
  <div class="accordion-content">
{content}
  </div>
</details>'''

    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Hero meta lines (from Zotero item metadata when available)
    hero_meta = ""
    parts = []
    if meta.get("authors"):
        parts.append(f"<strong>作者：</strong>{meta['authors']}")
    if meta.get("journal"):
        parts.append(f"<strong>期刊：</strong>{meta['journal']}")
    if parts:
        hero_meta = f'<div class="hero-meta">{"　".join(parts)}</div>'
    hero_doi = f'<div class="hero-doi">DOI: {meta["doi"]}</div>' if meta.get("doi") else ""
    hero_summary = (f'<div class="hero-summary"><strong>核心发现：</strong>{meta["summary"]}</div>'
                    if meta.get("summary") else
                    f'<div class="hero-summary"><strong>解读日期：</strong>{date_str}　|　<strong>解读标准：</strong>10板块文献解读</div>')

    figures_section = ""
    if figures:
        figures_section = (
            '<details class="accordion-item" id="sec-figures">'
            '<summary class="accordion-header"><span class="accordion-num">F</span>'
            '<span class="accordion-title">原文图表</span>'
            '<svg class="accordion-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg></summary>'
            f'<div class="accordion-content">{figure_blocks}</div></details>')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文献解读 - {paper_title}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">

<div class="hero">
  <span class="hero-badge">文献解读 · Literature Interpretation</span>
  <h1>{paper_title}</h1>
  {hero_meta}
  {hero_doi}
  {hero_summary}
</div>

<div class="toc">
  <div class="toc-title">目录 / Table of Contents</div>
  <ul class="toc-list">
{toc_links}  </ul>
  <div class="toc-controls">
    <a class="toc-btn" href="javascript:void(0)" onclick="var d=document.querySelectorAll('details.accordion-item');for(var i=0;i<d.length;i++){{d[i].open=true;}}return false;">全部展开</a>
    <a class="toc-btn" href="javascript:void(0)" onclick="var d=document.querySelectorAll('details.accordion-item');for(var i=0;i<d.length;i++){{d[i].open=false;}}return false;">全部折叠</a>
  </div>
</div>

{accordion}

<footer>
  <p>文献解读 · 按照「文献解读」标准执行 · 生成于 {date_str}</p>
</footer>

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

def generate_manual_output(full_text, figures, prompt_path, output_path, paper_title="文献解读"):
    """Generate prompt file and HTML template for manual mode."""

    # Write prompt file
    prompt = build_prompt(full_text, figures)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    # Generate HTML template with figures embedded but empty text sections
    sections = {i: {"title": SECTION_TITLES[i-1], "content": "（请将 LLM 解读结果粘贴到此板块）"} for i in range(1, 11)}
    html = generate_html(sections, figures, paper_title=paper_title + "（模板）")
    
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

def build_final_html(response_path, figures, output_path, paper_title="文献解读", meta=None):
    """Build final HTML from LLM response."""
    with open(response_path, "r", encoding="utf-8") as f:
        response = f.read()

    sections = parse_sections(response)
    html = generate_html(sections, figures, paper_title, meta)
    
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
    # 论文元数据（由 Zotero 条目传入，用于 Hero 展示与文件命名）
    parser.add_argument("--title", default="")
    parser.add_argument("--authors", default="")
    parser.add_argument("--journal", default="")
    parser.add_argument("--doi", default="")

    args = parser.parse_args()

    meta = {
        "authors": args.authors,
        "journal": args.journal,
        "doi": args.doi,
    }
    paper_title = args.title or "文献解读"

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

        build_final_html(args.response, figures, args.output, paper_title, meta)
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
        html = generate_html(sections, figures, paper_title, meta)
        
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        
        file_size = os.path.getsize(args.output)
        print(f"\nDone! HTML written to: {args.output}")
        print(f"  File size: {file_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
