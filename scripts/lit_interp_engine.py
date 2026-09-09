#!/usr/bin/env python3
"""
LITIT Engine
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


# Symbol 字体 PUA 字符 → Unicode 希腊字母（PDF 常见 Symbol 字体编码问题，ToUnicode 缺失时提取成 PUA 区字符）
_SYMBOL_MAP = {
    0xF061: 'α', 0xF062: 'β', 0xF063: 'χ', 0xF064: 'δ', 0xF065: 'ε',
    0xF066: 'φ', 0xF067: 'γ', 0xF068: 'η', 0xF069: 'ι', 0xF06A: 'ϑ',
    0xF06B: 'κ', 0xF06C: 'λ', 0xF06D: 'μ', 0xF06E: 'ν', 0xF06F: 'ο',
    0xF070: 'π', 0xF071: 'θ', 0xF072: 'ρ', 0xF073: 'σ', 0xF074: 'τ',
    0xF075: 'υ', 0xF076: 'ϖ', 0xF077: 'ω', 0xF078: 'ξ', 0xF079: 'ψ',
    0xF07A: 'ζ',
}


def _clean_symbol(text):
    """把 Symbol 字体的 PUA 字符映射回正确的希腊字母。"""
    return text.translate(_SYMBOL_MAP)


# ============================================================
# 1. PDF Text Extraction
# ============================================================

def extract_text(pdf_path):
    """Extract full text from PDF, return (full_text, per_page_text)."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(_clean_symbol(page.get_text()))
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
                        "caption": _clean_symbol(re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))))[:200].strip(),
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
            "caption": _clean_symbol(re.sub(r"\s+", " ", page.get_text("text", clip=fitz.Rect(x0, y0, x1, y1))))[:200].strip(),
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
    else:
        # Fallback: caption might be above, table below; or caption below, table above
        above_blocks = [b for b in blocks if b[3] <= cap_y0 and b[1] >= cap_y0 - 300]
        if not above_blocks:
            return None
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

    # 表格区域完整文本：保留换行结构（每行一个记录），供 LLM 解读表格具体数据。
    # caption 只取前 200 字符用于图片下方展示，text 存完整数据内容。
    region_text = _clean_symbol(page.get_text("text", clip=rect)).strip()
    caption = re.sub(r"\s+", " ", region_text)[:200].strip()

    return {
        "page": page_idx,
        "name": f"table{num}",
        "caption": caption,
        "text": region_text[:3000],  # 表格数据内容（供 LLM 解读）
        "b64": base64.b64encode(img_data).decode(),
        "mime": "image/png"
    }


# ============================================================
# 3. Paper Type Classification (adaptive template)
# ============================================================

# 各研究类型对应的专用方法学评价框架
# label 用于 Hero 徽章与元数据表；framework 注入 prompt 第 6 板块
PAPER_TYPE_PROFILES = {
    "meta": {
        "label": "系统综述 / Meta分析",
        "framework_name": "AMSTAR-2 + PRISMA",
        "framework": (
            "- 方案预注册：是否预先注册综述方案（PROSPERO 等），有无偏离\n"
            "- 检索策略：数据库覆盖是否全面、检索式是否可复现、是否纳入灰色文献\n"
            "- 纳排标准与筛选流程：标准是否明确、筛选是否双人独立进行\n"
            "- 纳入研究的偏倚风险评估：是否逐篇评估（如 RoB 2 / NOS），结果是否用于敏感性分析\n"
            "- 统计合并方法：效应量选择、固定/随机效应模型选择是否合理，I² 与 Q 检验的异质性评估\n"
            "- 发表偏倚：漏斗图 / Egger 检验是否报告\n"
            "- 敏感性分析与亚组分析、证据质量分级（GRADE）"
        ),
    },
    "rct": {
        "label": "随机对照试验 (RCT)",
        "framework_name": "RoB 2",
        "framework": (
            "- 域1 随机化过程：随机序列生成、分配隐藏、基线是否均衡（Low / Some concerns / High）\n"
            "- 域2 偏离预期干预：受试者与实施者盲法、依从性、干预偏离的处理（ITT / per-protocol）\n"
            "- 域3 结局数据缺失：失访比例、缺失数据处理方式（如多重插补）\n"
            "- 域4 结局测量：结局评估者盲法、结局指标客观性\n"
            "- 域5 选择性报告：是否预注册、实际报告与方案是否一致"
        ),
    },
    "cohort": {
        "label": "队列研究",
        "framework_name": "Newcastle-Ottawa Scale (NOS)",
        "framework": (
            "- 选择：暴露队列代表性、非暴露队列来源、暴露确定方法、研究开始时结局未发生\n"
            "- 可比性：是否控制关键混杂因素（匹配/多因素调整）\n"
            "- 结局：结局评估方式、随访时间是否充分、失访控制"
        ),
    },
    "case_control": {
        "label": "病例对照研究",
        "framework_name": "Newcastle-Ottawa Scale (NOS)",
        "framework": (
            "- 选择：病例定义、病例代表性、对照来源、对照定义\n"
            "- 可比性：是否控制关键混杂因素\n"
            "- 暴露：暴露确定方法、病例与对照采用相同调查方式、无应答率"
        ),
    },
    "cross_sectional": {
        "label": "横断面研究",
        "framework_name": "JBI 横断面研究评价工具",
        "framework": (
            "- 抽样框与抽样方法是否恰当、样本量是否充足\n"
            "- 研究对象与情境描述是否清晰、纳入标准是否一致\n"
            "- 暴露与结局测量的信效度、客观可靠的结局测量标准\n"
            "- 混杂因素的识别与处理、统计方法恰当性、应答率"
        ),
    },
    "case_report": {
        "label": "病例报告 / 病例系列",
        "framework_name": "CARE Checklist",
        "framework": (
            "- 患者信息：人口学、主诉、现病史与既往史是否完整、时间线是否清晰\n"
            "- 临床发现与诊断评估：体格检查、辅助检查、诊断挑战与鉴别诊断\n"
            "- 干预措施：类型、剂量、疗程及调整是否详尽，伦理/知情同意声明\n"
            "- 随访与结局：临床结局、不良事件、患者视角的结局\n"
            "- 讨论：与既往文献对比、本病例的独特价值与局限、结论的合理性"
        ),
    },
    "basic": {
        "label": "基础研究（体内外实验）",
        "framework_name": "SYRCLE / 实验严谨性清单",
        "framework": (
            "- 动物实验（如适用）：随机分组、分配隐藏、实施者盲法、结局评估者盲法、\n"
            "  结局评估时的动物随机选取、不完整数据处理、选择性报告\n"
            "- 细胞/分子实验（如适用）：细胞系鉴定（STR）与支原体检测、抗体/试剂验证、\n"
            "  生物学重复数、阳性与阴性对照设置\n"
            "- 统计：样本量依据、多重比较校正、图像数据的量化与呈现规范"
        ),
    },
}

# 分类关键词（小写匹配）。head 区（标题+摘要）权重 3，全文权重 1
_TYPE_PATTERNS = {
    "meta": [r"meta-analysis", r"meta analysis", r"systematic review", r"prisma", r"pooled analysis"],
    "rct": [r"randomized controlled", r"randomised controlled", r"randomization",
            r"randomisation", r"randomly assigned", r"randomly allocated",
            r"placebo-controlled", r"double-blind"],
    "case_report": [r"case report", r"case series", r"we report a case",
                    r"we present a case", r"a rare case of"],
    "cohort": [r"cohort study", r"prospective cohort", r"retrospective cohort",
               r"cohort analysis", r"population-based cohort"],
    "case_control": [r"case-control", r"case control study", r"nested case-control"],
    "cross_sectional": [r"cross-sectional", r"cross sectional study"],
    "basic": [r"in vitro", r"in vivo", r"cell line", r"western blot",
              r"immunohistochemistry", r"knockout", r"mouse model", r"murine model",
              r"signaling pathway", r"single-cell rna"],
}

# 优先级（得分相同时靠前者优先）
_TYPE_PRIORITY = ["meta", "rct", "case_report", "cohort", "case_control",
                  "cross_sectional", "basic"]


def classify_paper_type(full_text):
    """启发式论文类型分类：关键词加权评分。

    返回 PAPER_TYPE_PROFILES 中对应的 profile dict；无法识别时返回 None
    （此时解读沿用通用 10 维度方法学评价）。
    """
    head = full_text[:6000].lower()
    whole = full_text.lower()
    scores = {}
    for t, patterns in _TYPE_PATTERNS.items():
        score = 0
        for p in patterns:
            score += 3 * len(re.findall(p, head)) + len(re.findall(p, whole))
        scores[t] = score

    best_type = None
    best_score = 0
    for t in _TYPE_PRIORITY:
        if scores.get(t, 0) > best_score:
            best_type, best_score = t, scores[t]

    if best_type and best_score >= 3:
        return PAPER_TYPE_PROFILES[best_type]
    return None


# ============================================================
# 3.1 LLM Prompt Building
# ============================================================

def build_prompt(full_text, figure_info, max_text_len=12000, paper_type=None):
    """Build the 10-section interpretation prompt."""
    text_preview = full_text[:max_text_len]
    if len(full_text) > max_text_len:
        text_preview += "\n\n[... 文本已截断 ...]"

    # 按原文顺序列出所有图表：表格附数据文本，图附 caption（保持 Fig/Table 交叉顺序）
    chart_list_parts = []
    for f in figure_info:
        if f['name'].lower().startswith('table'):
            data = (f.get('text') or '').strip()
            chart_list_parts.append(
                f"- {f['name']}: {f['caption']}\n  表格数据：{data if data else '（未提取到表格数据文本）'}"
            )
        else:
            chart_list_parts.append(f"- {f['name']}: {f['caption']}")

    chart_list = "\n".join(chart_list_parts) if chart_list_parts else "（未检测到明确图表）"

    # 第 6 板块：识别出研究类型时用对应的专用评价框架，否则用通用 10 维度
    if paper_type:
        type_note = (f"\n> 论文类型识别：{paper_type['label']}。"
                     f"方法学评价请采用 {paper_type['framework_name']} 框架。\n")
        sec6 = f"""## 6. 方法学评价
本论文已识别为「{paper_type['label']}」。请采用 **{paper_type['framework_name']}** 框架，逐项评价以下维度，每项给出优势/不足/中立判断并附理由，结尾给出整体评级（A/B/C）：
{paper_type['framework']}

另请补充以下通用维度：样本量与统计功效、统计方法恰当性、外部效度/泛化性、报告完整性。"""
    else:
        type_note = ""
        sec6 = """## 6. 方法学评价
从以下10个维度系统评价，每项给出优势/不足/中立判断并附理由，结尾给出整体评级（A/B/C）：
研究设计合理性、对照组设置、样本量与统计功效、随机化与盲法、模型/人群代表性、
测量方法可靠性、混杂因素控制、统计方法恰当性、外部效度/泛化性、报告完整性"""

    prompt = f"""你是一位医学文献解读专家。请按照以下10个板块对这篇论文进行系统性解读。
{type_note}
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
按原文出现顺序，以每个 Figure/Table 为单位依次组织。对每个 Figure 说明其展示内容与观察现象；对每个 Table，务必结合下方「检测到的图表」中该表的数据内容逐项解读，说明关键数据、组间差异、趋势与统计意义，不要只复述表题。每个图表包含：展示内容→观察现象→量化数据→统计意义。

## 5. 讨论要点
核心结论、与既往研究对比、机制解释

{sec6}

## 7. 写作提炼
Introduction 叙事结构与段落逻辑功能、Discussion 组织策略、可复用写作模板（附原文例句佐证）

## 8. 局限性与未解决问题
原文自述局限性 + 解读者补充（标注"解读补充"）+ 尚未解决的科学问题

## 9. 临床/研究意义
临床实践指导价值、后续研究方向、转化潜力

## 10. 总结（逻辑链）
以"研究背景→研究假设→实验设计→核心结果→机制解释→结论与意义"为骨架，输出5-7个逻辑节点，深度剖析整篇文献的论证链条。严格按以下格式输出（每个节点固定3行：论断/证据/推进）：

### 节点1：研究背景
- 论断：该步骤的核心论断（1-2句，直接陈述）
- 证据：支撑该论断的原文证据（引用具体 Fig/Table/量化数据/既往文献）
- 推进：该步骤如何逻辑地导向下一步（指出推理桥梁或尚未解决的张力）

（节点2-7 同上格式，节点标题依次为：研究假设、实验设计、核心结果、机制解释、结论与意义等）

最后用一行 `> 关键前提与边界条件：...` 总结该逻辑链成立所依赖的关键前提与适用边界。

## 论文全文
{text_preview}

## 检测到的图表（按原文顺序，含表格数据）
{chart_list}

请按以上结构输出完整解读。"""

    return prompt


# ============================================================
# 3.2 Multi-Paper Comparison Prompt
# ============================================================

COMPARE_SECTION_TITLES = [
    "对比总览",
    "研究设计对比",
    "核心结果对比",
    "方法学质量对比",
    "一致性与矛盾点",
    "综合结论"
]


def build_compare_prompt(papers, max_text_len=8000):
    """构建多篇论文对比分析的 prompt。

    papers: list of dict，每项含 title/authors/journal/doi/text（全文）。
    """
    paper_blocks = ""
    for i, p in enumerate(papers, 1):
        text_preview = p["text"][:max_text_len]
        if len(p["text"]) > max_text_len:
            text_preview += "\n\n[... 文本已截断 ...]"
        paper_blocks += f"""
### 论文{i}：{p['title'] or '未命名'}
- 作者：{p.get('authors') or '未知'}
- 期刊：{p.get('journal') or '未知'}
- DOI：{p.get('doi') or '无'}

{text_preview}
"""

    n = len(papers)
    prompt = f"""你是一位医学文献解读专家。以下是 {n} 篇同主题论文，请进行系统性对比分析。

## 输出格式要求
- 每个板块用 `## N. 标题` 开头（N为1-6的数字）
- 中文为主，专业术语保留英文原文
- 对比内容优先用 Markdown 表格（首列为对比维度，后续各列对应论文1/论文2/...）
- 全篇杜绝营销式语言，直接陈述
- 引用具体论文时用「论文1」「论文2」等编号指代

## 6个板块结构

## 1. 对比总览
用一张表格列出每篇论文的：标题、研究类型、研究对象/模型、样本量、核心结论（一句话）。
随后用 2-3 句话概括这组论文共同关注的科学问题。

## 2. 研究设计对比
表格对比：研究设计类型、研究对象/人群、干预或暴露、对照设置、主要结局指标、随访/时间点。
表格后用简短文字点评设计层面的关键差异及其对结论的影响。

## 3. 核心结果对比
表格对比各论文的主要发现（效应量/关键数据），指出结果方向是否一致、量级差异。

## 4. 方法学质量对比
逐篇给出方法学质量评级（A 严格 / B 基本可靠但有局限 / C 存在明显缺陷），
并用表格列出各论文在样本量、对照设置、盲法/偏倚控制、统计方法上的主要优缺点。

## 5. 一致性与矛盾点
- 各论文结论相互支持之处
- 相互矛盾或结果不一致之处，并分析可能原因（人群差异、设计差异、统计方法、发表偏倚等）

## 6. 综合结论
基于这组论文的整体证据：当前证据强度判断、对临床/科研实践的启示、证据缺口与后续研究方向。
最后用一行 `> 综合判断：...` 给出对这组论文整体证据价值的一句话结论。
{paper_blocks}
请按以上结构输出完整对比分析。"""

    return prompt


# ============================================================
# 4. LLM API Call (multi-provider)
# ============================================================

SYSTEM_PROMPT = "你是一位专业的医学文献解读专家，擅长系统性分析和评价学术论文。"

# 各 provider 默认 base / model（设置面板切换 provider 时自动填充）
PROVIDER_DEFAULTS = {
    "deepseek": {"base": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai":   {"base": "https://api.openai.com/v1", "model": "gpt-4o"},
    "claude":   {"base": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
    "ollama":   {"base": "http://localhost:11434/v1", "model": "llama3"},
}


def _post_json(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        print(f"API Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"API call failed: {e}", file=sys.stderr)
        sys.exit(1)


def call_llm_api(prompt, api_key, api_base, model, provider="deepseek", max_tokens=8000):
    """统一 LLM 调用层：deepseek/openai/ollama 走 OpenAI 兼容格式，claude 走 Anthropic Messages 格式。"""
    provider = (provider or "deepseek").lower()

    if provider == "claude":
        url = api_base.rstrip("/") + "/v1/messages"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        result = _post_json(url, payload, headers)
        try:
            return "".join(
                b.get("text", "") for b in result.get("content", [])
                if b.get("type") == "text"
            )
        except Exception as e:
            print(f"Parse Claude response failed: {e}", file=sys.stderr)
            sys.exit(1)

    # deepseek / openai / ollama 均为 OpenAI 兼容格式
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False
    }
    headers = {"Authorization": "Bearer " + api_key}
    result = _post_json(url, payload, headers)
    return result["choices"][0]["message"]["content"]


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
.figure-block img { width: 100%; display: block; max-height: 70vh; object-fit: contain; }
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

/* Logic flow（垂直流程图） */
.logic-flow { margin: 16px 0; }
.lc-node { background: var(--card-bg); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 10px; padding: 14px 18px; box-shadow: var(--shadow-sm); }
.lc-node-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.lc-num { font-family: var(--font-mono); font-weight: 700; font-size: 12px; color: white; background: var(--accent); width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.lc-title { font-size: 15px; font-weight: 700; color: var(--accent-dark); }
.lc-row { display: flex; gap: 8px; margin: 6px 0; font-size: 13px; line-height: 1.7; color: var(--text); align-items: flex-start; }
.lc-tag { flex-shrink: 0; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; margin-top: 2px; }
.lc-tag-claim { background: var(--accent-light); color: var(--accent-dark); }
.lc-tag-evid { background: var(--green-light); color: var(--green); }
.lc-tag-adv { background: var(--amber-light); color: var(--amber); }
.lc-arrow { text-align: center; color: var(--accent); margin: 4px 0; }

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

def _build_logic_flow(content):
    """把 LLM 输出的逻辑链节点（### 节点N：标题 + - 论断/证据/推进）渲染为垂直流程图。

    解析失败（LLM 未遵循格式）时返回 None，调用方回退为普通 markdown 渲染。
    """
    headers = list(re.finditer(r'^###\s*(.+?)$', content, re.MULTILINE))
    if len(headers) < 2:
        return None

    nodes = []
    spans = []
    for idx, h in enumerate(headers):
        start = h.end()
        if idx + 1 < len(headers):
            end = headers[idx + 1].start()
        else:
            # 最后一个节点：body 到第一个 > 行（rest，如关键前提 callout）或结尾
            mrest = re.search(r'^>', content[start:], re.MULTILINE)
            end = start + mrest.start() if mrest else len(content)
        spans.append((h.start(), end))

        title = h.group(1).strip()
        mnum = re.match(r'^节点\s*(\d+)\s*[:：]\s*(.+)$', title)
        num = mnum.group(1) if mnum else str(len(nodes) + 1)
        if mnum:
            title = mnum.group(2)
        body = content[start:end]
        rows = []
        for tag, cls in (("论断", "lc-tag-claim"), ("证据", "lc-tag-evid"), ("推进", "lc-tag-adv")):
            mrow = re.search(r'^[-*]\s*' + tag + r'\s*[:：]\s*(.+)$', body, re.MULTILINE)
            if mrow:
                rows.append((tag, cls, mrow.group(1).strip()))
        if not rows:
            return None  # 节点结构不完整，回退普通渲染
        nodes.append((num, title, rows))

    html = ['<div class="logic-flow">']
    arrow = ('<div class="lc-arrow"><svg width="20" height="20" fill="none" stroke="currentColor" '
             'viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" '
             'stroke-width="2" d="M19 14l-7 7-7-7M12 3v18"/></svg></div>')
    for idx, (num, title, rows) in enumerate(nodes):
        if idx:
            html.append(arrow)
        html.append('<div class="lc-node"><div class="lc-node-head">'
                    f'<span class="lc-num">{num}</span>'
                    f'<span class="lc-title">{title}</span></div>')
        for tag, cls, text in rows:
            html.append(f'<div class="lc-row"><span class="lc-tag {cls}">{tag}</span>'
                        f'<span>{text}</span></div>')
        html.append('</div>')
    html.append('</div>')

    # 节点区间之外的其余内容（如 "> 关键前提与边界条件" callout）保留在流程图之后
    rest_parts = []
    prev = 0
    for s, e in spans:
        rest_parts.append(content[prev:s])
        prev = e
    rest_parts.append(content[prev:])
    rest = "".join(rest_parts)
    return "\n".join(html) + "\n" + rest


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
    if meta.get("paper_type"):
        rows.append(f'<tr><td><strong>研究类型</strong></td><td>{meta["paper_type"]}</td></tr>')
    if not rows:
        return ""
    return ('<table class="data-table"><tr><th style="width:140px">项目</th><th>内容</th></tr>'
            + "".join(rows) + '</table>')


def _escape_html(content):
    """转义 LLM 原始内容中的 & 和 <，防止 p<0.05 / A & B 等文本破坏 HTML 结构。

    只做转义，不做结构转换——这样调用方可以先在纯文本上插入图片 HTML，
    再做 markdown 结构转换，避免已插入的 <img>/<div> 标签被转义成文本。
    """
    return content.replace("&", "&amp;").replace("<", "&lt;")


def _md_convert(content):
    """把（已转义的）markdown 文本转换为 HTML 结构（表格/粗体/斜体/标题/列表/引用/段落）。

    注意：入参必须已经过 _escape_html 转义；本函数不再转义，
    以免把调用方先行插入的 HTML 标签（如图片块）转义成文本。
    """
    # Convert markdown tables to HTML
    content = _convert_markdown_tables(content)

    # Convert markdown bold to HTML
    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)

    # Convert markdown italic to HTML（单星号，在粗体之后处理）
    content = re.sub(r'\*([^*\n]+)\*', r'<em>\1</em>', content)

    # 过滤孤立的 # 标记行（LLM 输出的空标题，如 "###"）
    content = re.sub(r'^#{1,6}[ \t]*$', '', content, flags=re.MULTILINE)

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

    # Convert paragraphs（先过滤掉 LLM 用来分隔板块的 --- 水平线）
    content = re.sub(r'^-{3,}[ \t]*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^([^<\n].+)$', r'<p>\1</p>', content, flags=re.MULTILINE)
    return content


def _md_to_html(content):
    """转义 + markdown 转换（供无图片插入的场景，如多篇对比报告使用）。"""
    return _md_convert(_escape_html(content))


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

        # 先转义 LLM 原始内容，再在纯文本上做后续处理
        content = _escape_html(content)

        # sec1 论文基本信息：用 meta 构建结构化表格（参考文件风格）
        if i == 1:
            meta_table = _build_meta_table(paper_title, meta)
            if meta_table:
                content = meta_table + "\n" + content

        # Insert figures in section 4 (核心结果)：在转义后、markdown 转换前的纯文本上定位
        if i == 4 and figures:
            content = _insert_figures_into_content(content, figures)

        # sec10 逻辑链：优先渲染为垂直流程图（解析失败回退普通 markdown）
        if i == 10:
            flow = _build_logic_flow(content)
            if flow:
                content = flow
            else:
                content = _md_convert(content)
        else:
            content = _md_convert(content)

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

    # 论文类型徽章（识别成功时显示在 Hero 区）
    type_badge = (f'  <span class="hero-badge" style="background:rgba(255,255,255,0.28)">{meta["paper_type"]}</span>'
                  if meta.get("paper_type") else "")

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
  <span class="hero-badge">文献解读 · Literature Interpretation</span>{type_badge}
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


def generate_compare_html(sections, papers, report_title="多篇文献对比分析"):
    """生成多篇论文对比报告的自包含 HTML（6 板块折叠布局）。

    sections: parse_sections 解析出的 {1..6: {title, content}}
    papers: 参与对比的论文元数据列表（用于 Hero 区展示）
    """
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")

    # TOC
    toc_links = ""
    for i in range(1, 7):
        title = sections.get(i, {}).get("title", COMPARE_SECTION_TITLES[i-1])
        num = f"{i:02d}"
        toc_links += (f'<li><a href="#sec{i}" onclick="var el=document.getElementById(\'sec{i}\');'
                      f'if(el){{el.open=true;}}return false;"><span>{num}</span><span>{title}</span></a></li>\n')

    # Accordion sections
    accordion = ""
    for i in range(1, 7):
        title = sections.get(i, {}).get("title", COMPARE_SECTION_TITLES[i-1])
        content = sections.get(i, {}).get("content", "（待补充）")

        # 第 1 板块顶部附上参与对比的论文清单表
        if i == 1:
            rows = ""
            for idx, p in enumerate(papers, 1):
                t = (p.get("title") or "未命名").replace("&", "&amp;").replace("<", "&lt;")
                a = (p.get("authors") or "").replace("&", "&amp;").replace("<", "&lt;")
                j = (p.get("journal") or "").replace("&", "&amp;").replace("<", "&lt;")
                d = (p.get("doi") or "").replace("&", "&amp;").replace("<", "&lt;")
                rows += f'<tr><td>论文{idx}</td><td>{t}</td><td>{a}</td><td>{j}</td><td><code>{d}</code></td></tr>'
            if rows:
                content = ('<table class="data-table"><tr><th style="width:60px">编号</th><th>标题</th>'
                           '<th>作者</th><th>期刊</th><th>DOI</th></tr>' + rows + '</table>\n' + content)

        content = _md_to_html(content)

        open_attr = " open" if i == 1 else ""
        accordion += f'''
<details class="accordion-item" id="sec{i}"{open_attr}>
<summary class="accordion-header"><span class="accordion-num">{i}</span><span class="accordion-title">{title}</span><svg class="accordion-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg></summary>
  <div class="accordion-content">
{content}
  </div>
</details>'''

    safe_title = report_title.replace("&", "&amp;").replace("<", "&lt;")
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>多篇对比 - {safe_title}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">

<div class="hero">
  <span class="hero-badge">多篇对比 · Comparative Analysis</span>
  <h1>{safe_title}</h1>
  <div class="hero-meta"><strong>对比论文数：</strong>{len(papers)} 篇</div>
  <div class="hero-summary"><strong>生成日期：</strong>{date_str}　|　<strong>分析框架：</strong>6板块多篇对比</div>
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
  <p>多篇文献对比分析 · 生成于 {date_str}</p>
</footer>

</div>
</body>
</html>'''

    return html


def _insert_figures_into_content(content, figures):
    """Insert figure blocks into section 4 content at appropriate positions."""
    # 先在【原始文本】上定位所有图的插入点，再按位置倒序插入。
    # （若边插边搜，后续图的匹配串如 "fig2" 会命中已插入 base64 图数据里的
    #   随机子串，把新图插进旧图 base64 中间，导致旧图被截断损坏）
    inserts = []
    for fig in figures:
        name = fig['name']
        # 提取编号：fig1 -> 1, table2 -> 2
        num_match = re.match(r'^(?:fig|table)(\d+)$', name, re.IGNORECASE)
        num = num_match.group(1) if num_match else name

        if name.lower().startswith('table'):
            # 表格：匹配 "Table 2" / "Table2" / "table2"（大小写、空格均兼容）
            pattern = re.compile(
                r'(Table\.?\s*' + re.escape(num) + r'|' + re.escape(name) + r')',
                re.IGNORECASE
            )
        else:
            # 图：匹配 "Fig. 1" / "Figure 1" / "fig1"
            pattern = re.compile(
                r'(Fig\.?\s*' + re.escape(num) +
                r'|Figure\s*' + re.escape(num) +
                r'|' + re.escape(name) + r')',
                re.IGNORECASE
            )

        fig_html = f'''
<div class="figure-block">
<img src="data:{fig['mime']};base64,{fig['b64']}" alt="{fig['name']}">
<div class="figure-caption">{fig['name']} · {fig['caption']}</div>
</div>'''
        m = pattern.search(content)
        inserts.append((m.start() if m else len(content), fig_html))
    # 按位置从大到小插入，保证先插入的不影响前面的定位
    for pos, fig_html in sorted(inserts, key=lambda t: -t[0]):
        content = content[:pos] + fig_html + "\n" + content[pos:]
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
    parser = argparse.ArgumentParser(description="LITIT Engine")
    parser.add_argument("--pdf", help="Path to PDF file")
    parser.add_argument("--output", required=True, help="Output HTML path")
    parser.add_argument("--mode", choices=["auto", "manual", "build-final", "compare"], default="manual")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-base", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--prompt-file", default="prompt.txt")
    parser.add_argument("--response", help="LLM response file (for build-final mode)")
    # 多篇对比模式：JSON 文件，内容为 [{pdf, title, authors, journal, doi}, ...]
    parser.add_argument("--papers-json", help="Papers list JSON for compare mode")
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

    if args.mode == "compare":
        # Compare mode: 多篇论文对比分析
        if not args.papers_json:
            print("ERROR: --papers-json required for compare mode", file=sys.stderr)
            sys.exit(1)
        if not args.api_key:
            print("ERROR: --api-key required for compare mode", file=sys.stderr)
            sys.exit(1)

        with open(args.papers_json, "r", encoding="utf-8") as f:
            paper_list = json.load(f)
        if len(paper_list) < 2:
            print("ERROR: compare mode needs at least 2 papers", file=sys.stderr)
            sys.exit(1)

        print(f"\n--- Compare Mode ({len(paper_list)} papers) ---")
        papers = []
        for idx, p in enumerate(paper_list, 1):
            print(f"Extracting text [{idx}/{len(paper_list)}]: {p.get('title') or p['pdf']}")
            text, _ = extract_text(p["pdf"])
            papers.append({
                "title": p.get("title", ""),
                "authors": p.get("authors", ""),
                "journal": p.get("journal", ""),
                "doi": p.get("doi", ""),
                "text": text,
            })

        print("Building compare prompt...")
        prompt = build_compare_prompt(papers)

        print(f"Calling {args.provider} API ({args.model})...")
        response = call_llm_api(prompt, args.api_key, args.api_base, args.model,
                                provider=args.provider, max_tokens=8000)
        print(f"  Response: {len(response)} chars")

        print("Parsing sections...")
        sections = parse_sections(response)
        print(f"  Parsed {len(sections)} sections")

        print("Generating HTML...")
        html = generate_compare_html(sections, papers, paper_title)

        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)

        file_size = os.path.getsize(args.output)
        print(f"\nDone! Compare report written to: {args.output}")
        print(f"  File size: {file_size / 1024:.0f} KB")
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
        # 论文类型自适应：先启发式分类，再选择对应的方法学评价框架
        paper_type = classify_paper_type(full_text)
        if paper_type:
            print(f"Paper type detected: {paper_type['label']} "
                  f"(framework: {paper_type['framework_name']})")
            meta["paper_type"] = paper_type["label"]
        else:
            print("Paper type: not detected, using generic evaluation framework")

        print("Building prompt...")
        prompt = build_prompt(full_text, figures, paper_type=paper_type)
        
        print(f"Calling {args.provider} API ({args.model})...")
        response = call_llm_api(prompt, args.api_key, args.api_base, args.model,
                                provider=args.provider)
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
