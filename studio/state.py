# -*- coding: utf-8 -*-
"""会话状态初始化与读写、模型配置、批次切分。"""

import streamlit as st

from .tasks import TASK_MAP, TASK_ORDER
from .employees import EMPLOYEES
from .llm_service import LLMService


def init_state():
    if "outputs" not in st.session_state:
        st.session_state.outputs = {i: None for i in TASK_ORDER}
        st.session_state.outputs[8] = {}
    if "memory" not in st.session_state:
        st.session_state.memory = {k: [] for k in EMPLOYEES}
    if "running_task" not in st.session_state:
        st.session_state.running_task = None
    if "total_episodes" not in st.session_state:
        st.session_state.total_episodes = 50
    if "script_mode" not in st.session_state:
        st.session_state.script_mode = "standard"
    if "seed" not in st.session_state:
        st.session_state.seed = ""
    if "global_cfg" not in st.session_state:
        st.session_state.global_cfg = {"provider": "Mock (演示)", "key": "", "model": "mock-studio-model"}
    if "per_emp" not in st.session_state:
        st.session_state.per_emp = False
    if "emp_cfg" not in st.session_state:
        st.session_state.emp_cfg = {}


def reset_studio():
    st.session_state.outputs = {i: None for i in TASK_ORDER}
    st.session_state.outputs[8] = {}
    st.session_state.memory = {k: [] for k in EMPLOYEES}
    st.session_state.running_task = None


def get_emp_config(emp_key):
    """读取某数字员工的模型配置（个性化优先，否则用全局默认）。"""
    if st.session_state.per_emp and emp_key in st.session_state.emp_cfg:
        return st.session_state.emp_cfg[emp_key]
    return st.session_state.global_cfg


def make_service(emp_key):
    cfg = get_emp_config(emp_key)
    svc = LLMService()
    svc.set_config(cfg["provider"], cfg["key"], cfg["model"])
    return svc


def task_done(tid):
    v = st.session_state.outputs.get(tid)
    if tid == 8:
        return isinstance(v, dict) and len(v) > 0
    return isinstance(v, str) and v.strip() != "" and not v.startswith("❌")


def is_ready(tid):
    return all(task_done(d) for d in TASK_MAP[tid]["deps"])


def add_memory(emp_key, note):
    mem = st.session_state.memory.setdefault(emp_key, [])
    if note not in mem:
        mem.append(note)


def get_batches(total, size=10):
    out = []
    for i in range(1, total + 1, size):
        out.append((i, min(i + size - 1, total)))
    return out
