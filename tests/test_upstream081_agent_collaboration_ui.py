# [UPSTREAM-081-004] Agent 协同图平移与节点完整显示
"""Tests for UPSTREAM-081-004: AgentCollaboration workflow panning + node visibility.

Adapted from upstream commits (local repo):
- 74b22ad (#118): group-sources box 760 -> 860, canvas taller, translateExtent y 660 -> 780
- 06a0e28 (#203): fully pannable workflow — onInit + flowInstanceRef, FIT_VIEW_OPTIONS,
  re-fit after analysis settles, debounced re-fit on window resize, relaxed translateExtent

本仓库无 Playwright，采用仓库既有模式：读 tsx 源码做结构化断言（解析
NODE_POSITIONS / GROUP_LABELS / translateExtent 等字面量）+ 纯几何逻辑单测，
验证 translateExtent 覆盖全部节点坐标边界、窄屏回退视图覆盖全部 15 个 agent。

参考组织方式：tests/test_ta_ui_001_horizon_intent.py、tests/test_config_ds_schedule_r1.py
"""

from __future__ import annotations

import os
import re
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "AgentCollaboration.tsx")


def _load_component_src() -> str:
    assert os.path.exists(COMPONENT), f"AgentCollaboration.tsx not found at {COMPONENT}"
    with open(COMPONENT, encoding="utf-8") as fh:
        return fh.read()


def _extract_block(src: str, start_marker: str) -> str:
    """Extract source from start_marker to the closing '}' at column 0."""
    start = src.index(start_marker)
    end = src.index("\n}", start)
    return src[start:end]


# ── 纯逻辑：从源码字面量解析布局数据 ──────────────────────────────────────────


def parse_node_positions(src: str) -> dict[str, tuple[int, int]]:
    """Parse NODE_POSITIONS literal into {name: (x, y)}."""
    block = _extract_block(src, "const NODE_POSITIONS")
    pairs = re.findall(r"'([^']+)':\s*\{\s*x:\s*(-?\d+),\s*y:\s*(-?\d+)\s*\}", block)
    assert pairs, "NODE_POSITIONS entries not found"
    return {name: (int(x), int(y)) for name, x, y in pairs}


def parse_group_labels(src: str) -> list[dict]:
    """Parse GROUP_LABELS literal into dicts with id/x/y/width/height."""
    block = _extract_block(src, "const GROUP_LABELS")
    entries = re.findall(
        r"id:\s*'([^']+)',\s*label:\s*'[^']*',\s*position:\s*"
        r"\{\s*x:\s*(-?\d+),\s*y:\s*(-?\d+)\s*\},\s*width:\s*(\d+),\s*height:\s*(\d+)",
        block,
    )
    assert entries, "GROUP_LABELS entries not found"
    return [
        {"id": gid, "x": int(x), "y": int(y), "width": int(w), "height": int(h)}
        for gid, x, y, w, h in entries
    ]


def parse_translate_extent(src: str) -> tuple[tuple[int, int], tuple[int, int]]:
    m = re.search(
        r"translateExtent=\{\s*\[\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*,\s*"
        r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*\]\s*\}",
        src,
    )
    assert m, "translateExtent prop not found"
    x0, y0, x1, y1 = (int(g) for g in m.groups())
    return ((x0, y0), (x1, y1))


def parse_node_max_width(src: str) -> int:
    """Agent node card max width, e.g. max-w-[218px]."""
    m = re.search(r"max-w-\[(\d+)px\]", src)
    assert m, "node max-w-[..px] not found"
    return int(m.group(1))


def parse_meta_names(src: str) -> list[str]:
    """Parse agent names from the META literal (source order)."""
    block = _extract_block(src, "const META: AgentMeta[]")
    names = re.findall(r"\{\s*name:\s*'([^']+)'", block)
    assert names, "META entries not found"
    return names


def resolve_mobile_group_names(src: str) -> set[str]:
    """Pure logic: resolve mobileGroups entries to agent names.

    Handles both `META.slice(0, N)` and explicit ['A', 'B'] styles so the
    narrow-screen fallback is verified against the real META set.
    """
    meta_names = parse_meta_names(src)
    block = _extract_block(src, "const mobileGroups")
    names: set[str] = set()
    for entry in re.findall(r"names:\s*(.+?),?\n", block):
        slice_m = re.search(r"META\.slice\(0,\s*(\d+)\)", entry)
        if slice_m:
            names.update(meta_names[: int(slice_m.group(1))])
            continue
        names.update(re.findall(r"'([^']+)'", entry))
    return names


def content_bounds(
    positions: dict[str, tuple[int, int]],
    groups: list[dict],
    node_width: int,
    node_height_upper: int = 140,
) -> tuple[int, int, int, int]:
    """Pure geometry: bounding box of all flow content in flow coordinates.

    node_height_upper is a conservative upper bound for a verdict-expanded
    card (base ~100px + two clamp-2 verdict lines); the exact rendered height
    is content-driven, so the estimate must stay comfortably above reality.
    """
    min_x = min_y = 10**9
    max_x = max_y = -(10**9)
    for x, y in positions.values():
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x + node_width), max(max_y, y + node_height_upper)
    for g in groups:
        min_x, min_y = min(min_x, g["x"]), min(min_y, g["y"])
        max_x, max_y = max(max_x, g["x"] + g["width"]), max(max_y, g["y"] + g["height"])
    return (min_x, min_y, max_x, max_y)


# ── 布局常量（上游 #118 适配） ────────────────────────────────────────────────


class TestLayoutConstants:
    def test_group_sources_box_covers_seven_analysts(self):
        src = _load_component_src()
        groups = {g["id"]: g for g in parse_group_labels(src)}
        assert "group-sources" in groups
        # 上游 #118：7 个分析师 + verdict 展开需要 860 高（原 760 会裁掉量价节点）
        assert groups["group-sources"]["height"] == 860

    def test_group_sources_box_contains_volume_price_node(self):
        src = _load_component_src()
        positions = parse_node_positions(src)
        groups = {g["id"]: g for g in parse_group_labels(src)}
        vpa_x, vpa_y = positions["Volume Price Analyst"]
        box = groups["group-sources"]
        assert box["x"] <= vpa_x
        assert vpa_y + 140 <= box["y"] + box["height"], (
            "量价节点（含 verdict 展开余量）必须完整落在技术分析分组框内"
        )

    def test_fit_view_options_constant(self):
        src = _load_component_src()
        m = re.search(
            r"const FIT_VIEW_OPTIONS = \{\s*padding:\s*([\d.]+),\s*"
            r"minZoom:\s*([\d.]+),\s*maxZoom:\s*([\d.]+),?\s*\}",
            src,
        )
        assert m, "FIT_VIEW_OPTIONS constant not found"
        padding, min_zoom, max_zoom = (float(g) for g in m.groups())
        # 上游 #203 取值：收边距 + 限制最小缩放，其余靠平移访问
        assert padding == pytest.approx(0.06)
        assert min_zoom == pytest.approx(0.72)
        assert max_zoom == pytest.approx(1.0)
        assert 0 < padding < 0.5 and 0 < min_zoom <= max_zoom <= 1

    def test_fit_view_wired_to_react_flow(self):
        src = _load_component_src()
        assert re.search(r"\n\s+fitView\n", src), "fitView prop must stay enabled"
        assert "fitViewOptions={FIT_VIEW_OPTIONS}" in src

    def test_old_clipping_values_removed(self):
        src = _load_component_src()
        assert "[[-40, -40], [1730, 660]]" not in src, (
            "旧 translateExtent y 上限 660 会裁掉底部节点"
        )
        for g in parse_group_labels(src):
            assert g["height"] != 760 or g["id"] != "group-sources"


# ── 平移范围覆盖全部节点边界（纯几何单测） ────────────────────────────────────


class TestTranslateExtentCoverage:
    def test_extent_covers_all_content_bounds(self):
        src = _load_component_src()
        positions = parse_node_positions(src)
        groups = parse_group_labels(src)
        node_width = parse_node_max_width(src)
        (min_x, min_y, max_x, max_y) = content_bounds(positions, groups, node_width)
        (ex0, ey0), (ex1, ey1) = parse_translate_extent(src)
        assert ex0 <= min_x, f"extent 左界 {ex0} 必须覆盖内容左界 {min_x}"
        assert ey0 <= min_y, f"extent 上界 {ey0} 必须覆盖内容上界 {min_y}"
        assert ex1 >= max_x, f"extent 右界 {ex1} 必须覆盖内容右界 {max_x}"
        assert ey1 >= max_y, f"extent 下界 {ey1} 必须覆盖内容下界 {max_y}"

    def test_extent_is_upstream_relaxed_value(self):
        src = _load_component_src()
        # 上游 #203 放宽值：四边均留出平移余量
        assert parse_translate_extent(src) == ((-300, -160), (2050, 900))

    def test_extent_covers_all_agent_nodes_individually(self):
        src = _load_component_src()
        positions = parse_node_positions(src)
        node_width = parse_node_max_width(src)
        (ex0, ey0), (ex1, ey1) = parse_translate_extent(src)
        assert len(positions) == 15, "协同图应有 15 个 agent 节点"
        for name, (x, y) in positions.items():
            assert ex0 <= x and x + node_width <= ex1, f"{name} x 超出 translateExtent"
            assert ey0 <= y and y + 140 <= ey1, f"{name} y 超出 translateExtent"


# ── 分析结束 / 窗口 resize 后重新 fit（上游 #203 适配） ───────────────────────


class TestRefitBehavior:
    def test_flow_instance_ref_and_type(self):
        src = _load_component_src()
        assert "type ReactFlowInstance" in src, "须引入 ReactFlowInstance 类型"
        assert re.search(
            r"useRef<ReactFlowInstance<CollaborationNode,\s*Edge>\s*\|\s*null>\(null\)",
            src,
        ), "flowInstanceRef 未按类型化方式声明"

    def test_on_init_captures_instance(self):
        src = _load_component_src()
        assert re.search(
            r"onInit=\{\(instance\) => \{\s*flowInstanceRef\.current = instance\s*\}\}",
            src,
        ), "onInit 必须把实例写入 flowInstanceRef"

    def test_fit_graph_uses_fit_view_options(self):
        src = _load_component_src()
        m = re.search(
            r"const fitGraph = useCallback\(\(duration = 300\) => \{.*?\}\, \[\]\)",
            src,
            re.DOTALL,
        )
        assert m, "fitGraph 回调缺失"
        assert "fitView(" in m.group(0)
        assert "...FIT_VIEW_OPTIONS" in m.group(0)
        assert "duration" in m.group(0)

    def test_refit_after_analysis_finishes(self):
        src = _load_component_src()
        # isAnalyzing true->false 后 requestAnimationFrame 里重新 fit
        effect = re.search(
            r"useEffect\(\(\) => \{\s*if \(isAnalyzing\) return\s*"
            r"const frameId = window\.requestAnimationFrame\(\(\) => fitGraph\(\)\)\s*"
            r"return \(\) => window\.cancelAnimationFrame\(frameId\)\s*\}, "
            r"\[isAnalyzing, fitGraph\]\)",
            src,
        )
        assert effect, "分析结束（isAnalyzing false）后重新 fitView 的 effect 缺失"

    def test_refit_on_window_resize_debounced(self):
        src = _load_component_src()
        effect = re.search(
            r"window\.addEventListener\('resize', handleResize\)", src
        )
        assert effect, "resize 监听缺失"
        debounce = re.search(
            r"window\.setTimeout\(\(\) => fitGraph\(0\),\s*120\)", src
        )
        assert debounce, "resize 需 120ms 防抖后以 duration=0 重新 fit"
        cleanup = re.search(
            r"window\.removeEventListener\('resize', handleResize\)", src
        )
        assert cleanup, "resize 监听须在卸载时移除"
        clear = re.search(
            r"if \(timeoutId !== undefined\) window\.clearTimeout\(timeoutId\)", src
        )
        assert clear, "防抖定时器须被清理（监听卸载与重复触发两处）"
        assert len(re.findall(r"clearTimeout\(timeoutId\)", src)) >= 2


# ── 平移手势与节点点击互不误触 ────────────────────────────────────────────────


class TestPanVersusClick:
    def test_panning_enabled_explicitly(self):
        src = _load_component_src()
        assert "panOnDrag={true}" in src, "画布必须显式允许拖拽平移"

    def test_nodes_not_draggable_or_connectable(self):
        src = _load_component_src()
        # 节点本身不可拖动：按住节点拖动 = 平移画布（React Flow 以位移阈值
        # 区分 click 与 drag，拖拽后的 click 被抑制，不会误触 onNodeClick）
        assert "nodesDraggable={false}" in src
        assert "nodesConnectable={false}" in src

    def test_node_click_still_wired(self):
        src = _load_component_src()
        assert "onNodeClick={handleNodeClick}" in src
        m = re.search(
            r"const handleNodeClick = useCallback\(\(.*?\}, \[.*?\]\)",
            src,
            re.DOTALL,
        )
        assert m, "handleNodeClick 回调缺失"
        # 仅完成/进行中的节点可点，未完成的点击无副作用
        body = m.group(0)
        assert "completed" in body and "in_progress" in body


# ── 窄屏（<lg）回退视图覆盖全部节点 ──────────────────────────────────────────


class TestNarrowScreenFallback:
    def test_canvas_hidden_below_lg_with_fallback_visible(self):
        src = _load_component_src()
        assert 'className="hidden lg:block h-[620px] xl:h-[810px] w-full"' in src, (
            "桌面画布须保持 <lg 隐藏 + 响应式高度（xl 提升至 810 对齐上游 #118）"
        )
        assert '<div className="lg:hidden space-y-3">' in src, (
            "窄屏回退视图（mobile compact workflow）必须存在"
        )

    def test_fallback_covers_all_agents(self):
        src = _load_component_src()
        meta_names = set(parse_meta_names(src))
        assert len(meta_names) == 15
        fallback_names = resolve_mobile_group_names(src)
        missing = meta_names - fallback_names
        assert not missing, f"窄屏回退视图缺少节点: {sorted(missing)}"
        extra = fallback_names - meta_names
        assert not extra, f"窄屏回退视图引用了不存在的节点: {sorted(extra)}"

    def test_fallback_items_are_clickable_buttons(self):
        src = _load_component_src()
        fallback = _extract_block(src, '<div className="lg:hidden space-y-3">')
        assert "<button" in fallback, "回退视图节点须为可点击 button"
        assert "handleNodeClick" in fallback, "回退视图须复用节点点击逻辑（报告/辩论可达）"
        assert "disabled={card.status !== 'completed' && card.status !== 'in_progress'}" in (
            fallback
        ), "回退视图与桌面一致：仅完成/进行中的节点可交互"
