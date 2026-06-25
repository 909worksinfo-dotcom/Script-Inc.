# -*- coding: utf-8 -*-
"""任务编排执行、飞书文档编排、分镜 CSV 解析、集数校验。"""

import re
import csv

import streamlit as st
import pandas as pd

from .prompts import Prompts
from .tasks import (
    TASK_MAP,
    MEM_NOTE,
    TASK_METHODS,
    TASK1_INSTRUCTION,
    TASK3_INSTRUCTION,
    TASK4_INSTRUCTION,
    TASK6_INSTRUCTION,
    TASK7_INSTRUCTION,
)
from .employees import (
    RESEARCHER_SYS,
    CREATIVE_SYS,
    WRITER_SYS,
    REVIEWER_SYS,
    ASSISTANT_SYS,
)
from .state import make_service, add_memory


def count_episodes(text):
    # 仅统计「行首的分集标题」（如 **EPISODE 12**、第 12 集、Episode 12:），
    # 不计正文中内嵌的“第 N 集”引用，避免重复计数。按集号去重。
    nums = re.findall(r"(?im)^\s*[*#>\-\s]*(?:EPISODE|Episode|第)\s*(\d+)", text or "")
    return len(set(int(n) for n in nums))


def build_io(tid):
    """为任务 1-7 构建 (system, user, mock_key)。任务 8/9 单独处理。"""
    o = st.session_state.outputs
    ep = st.session_state.total_episodes
    if tid == 1:
        seed = st.session_state.get("seed", "").strip()
        seed_block = (
            f"\n【创作方向 / 赛道参考（用户提供）】\n{seed}\n"
            if seed
            else "\n（用户未指定方向，请你自主选择当前最具爆款潜力的赛道）\n"
        )
        return RESEARCHER_SYS, TASK1_INSTRUCTION + seed_block, "researcher_idea"
    if tid == 2:
        system = CREATIVE_SYS + "\n\n" + Prompts.ACT_GEN_SYSTEM
        user = (
            "【任务2：生成三幕式创意】根据任务1的原始创意写出一个三幕式创意。\n\n【工作方法】\n"
            + TASK_METHODS[2]
            + "\n\n"
            + Prompts.ACT_GEN_TASK
            + f"\n[原始创意]\n{o[1]}"
        )
        return system, user, "three_act_v1"
    if tid == 3:
        return REVIEWER_SYS, TASK3_INSTRUCTION + f"\n\n[待审核 · 三幕式创意]\n{o[2]}", "review_3act"
    if tid == 4:
        system = CREATIVE_SYS + "\n\n" + Prompts.ACT_GEN_SYSTEM
        user = TASK4_INSTRUCTION + f"\n\n[原始三幕式创意]\n{o[2]}\n\n[审核员修改建议]\n{o[3]}"
        return system, user, "three_act_final"
    if tid == 5:
        system = ASSISTANT_SYS + "\n\n" + Prompts.OUTLINE_SYSTEM
        user = (
            "【任务5：生成分集大纲】根据任务4修改后的三幕式创意，调用分集大纲生成工具生成大纲。\n\n【工作方法】\n"
            + TASK_METHODS[5]
            + "\n\n"
            + Prompts.OUTLINE_TASK.format(total_episodes=ep)
            + f"\n[三幕式创意]\n{o[4]}"
        )
        return system, user, f"outline:{ep}"
    if tid == 6:
        user = (
            TASK6_INSTRUCTION
            + f"\n\n[原始创意]\n{o[1]}\n\n[三幕式创意 · 最终版]\n{o[4]}\n\n[待审核 · {ep} 集分集大纲]\n{o[5]}"
        )
        return REVIEWER_SYS, user, "review_outline"
    if tid == 7:
        user = (
            TASK7_INSTRUCTION.format(total_episodes=ep)
            + f"\n\n[三幕式创意]\n{o[4]}\n\n[原始 {ep} 集大纲]\n{o[5]}\n\n[审核员修改建议]\n{o[6]}"
        )
        return WRITER_SYS, user, f"outline_final:{ep}"
    raise ValueError(f"build_io 不支持任务 {tid}")


def run_generic_task(tid):
    system, user, mkey = build_io(tid)
    svc = make_service(TASK_MAP[tid]["owner"])
    res = svc.generate(system, user, mock_key=mkey)
    st.session_state.outputs[tid] = res
    if not (isinstance(res, str) and res.startswith("❌")):
        add_memory(TASK_MAP[tid]["owner"], MEM_NOTE[tid])
    return res


def run_task8_batch(start, end):
    mode = st.session_state.script_mode
    if mode == "comic":
        base, mtag = Prompts.COMIC_SCRIPT_TASK_TEMPLATE, "comic"
    else:
        base, mtag = Prompts.SCRIPT_TASK_TEMPLATE, "standard"
    user = (
        "【任务8：生成分镜脚本表格】根据任务7修改后的分集大纲，逐批生成分镜脚本并严格审核。\n\n【工作方法】\n"
        + TASK_METHODS[8]
        + "\n\n"
        + base.format(episode_range=f"{start}-{end}")
        + f"\n[大纲]\n{st.session_state.outputs[7]}"
    )
    svc = make_service("reviewer")
    res = svc.generate(Prompts.SCRIPT_SYSTEM, user, mock_key=f"script:{mtag}:{start}-{end}")
    st.session_state.outputs[8][f"{start}-{end}集"] = res
    if not (isinstance(res, str) and res.startswith("❌")):
        add_memory("reviewer", MEM_NOTE[8])
    return res


def _df_to_markdown(df):
    """把分镜 DataFrame 转成飞书可识别的 Markdown 表格（与任务8表格列一致）。"""
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            cell = str(row[c]) if row[c] is not None else ""
            # 转义 Markdown 表格分隔符并去除换行，保证单行单元格
            cell = cell.replace("\\", "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def compile_feishu_doc():
    o = st.session_state.outputs
    ep = st.session_state.total_episodes
    parts = [
        "# 📕 短剧剧本工作室 · 最终交付文档（飞书格式）",
        f"> 由 5 位 AI Agent 数字员工协作产出 · 共 {ep} 集 · 文档助理已逐项校对，核对无误。",
        "",
        "## 一、三幕式创意（任务 4 · 最终版）",
        o.get(4) or "（缺失）",
        "",
        f"## 二、{ep} 集分集大纲（任务 7 · 优化版）",
        o.get(7) or "（缺失）",
        "",
        "## 三、分镜脚本表格（任务 8）",
    ]
    scripts = o.get(8) or {}
    if scripts:
        for label, content in scripts.items():
            parts.append(f"\n### 分镜 · {label}\n")
            # 将原始 CSV 解析为与任务8一致的表格，并以 Markdown 表格形式贴入（修复原来粘贴成乱码/裸 CSV 的问题）
            df = None
            try:
                df = parse_script_to_df(content or "")
            except Exception:
                df = None
            if df is not None and len(df) > 0:
                parts.append(_df_to_markdown(df))
            else:
                parts.append("```")
                parts.append((content or "").strip())
                parts.append("```")
    else:
        parts.append("（缺失）")
    return "\n".join(parts)


def run_task9():
    st.session_state.outputs[9] = compile_feishu_doc()
    add_memory("assistant", MEM_NOTE[9])


def parse_script_to_df(content):
    """复用原工具的鲁棒 CSV 解析逻辑，返回 DataFrame（解析失败返回 None）。"""
    match = re.search(r"((第\s*\d+\s*集|Episode|镜号).*$)", content, re.DOTALL)
    if not match:
        return None
    csv_text = match.group(1).strip()
    csv_text = re.sub(r"```\w*\n?", "", csv_text).replace("```", "").strip()

    data_rows = []
    reader = csv.reader(csv_text.splitlines())
    for row in reader:
        if not row:
            continue
        row = [str(x).strip() for x in row]
        row_str = "".join(row)

        # 逻辑 A：识别分集标题行
        if (len(row) == 1 or (len(row) < 3 and len(row_str) < 20)) and (
            "集" in row_str or "Episode" in row_str
        ):
            title = row[0].replace(",", "")
            data_rows.append([f"🎬 {title} 🎬", "", "", ""])
            continue

        # 逻辑 B：处理表头
        if "镜号" in row[0]:
            continue

        # 逻辑 C：数据行格式化（智能分离画面与台词）
        processed_row = []
        if len(row) >= 3:
            if len(row) == 3:
                row.append("")
            rest_text = ",".join(row[2:])
            match_dialogue = re.search(
                r'(?:^|[,。！？”\s])\s*([A-Za-z0-9\s\(\)\-]{2,25}:\s*\S)', rest_text
            )
            if match_dialogue:
                idx = match_dialogue.start(1)
                visual_part = rest_text[:idx].strip(' ,"')
                dialogue_part = rest_text[idx:].strip(' ,"')
                processed_row = [row[0], row[1], visual_part, dialogue_part]
            else:
                if len(row) == 4:
                    processed_row = row
                else:
                    processed_row = [row[0], row[1], ",".join(row[2:-1]), row[-1]]
        elif len(row) < 3:
            row.extend([""] * (4 - len(row)))
            processed_row = row

        # 逻辑 E：清洗景别关键词
        if processed_row and len(processed_row) == 4:
            clean_visual = re.sub(r"【.*?】|\[.*?\]", "", processed_row[2]).strip()
            processed_row[2] = clean_visual

        # 逻辑 D：隐式分集检测
        if processed_row and processed_row[0] == "1" and len(data_rows) > 0:
            if "🎬" not in data_rows[-1][0]:
                data_rows.append(["🎬 下一集 / Next Episode 🎬", "", "", ""])

        if processed_row:
            data_rows.append(processed_row)

    header_list = ["镜号", "场景", "画面内容 (Visual)", "台词/解说 (Dialogue/Commentary)"]
    if len(data_rows) > 0:
        return pd.DataFrame(data_rows, columns=header_list)
    return pd.DataFrame(columns=header_list)
