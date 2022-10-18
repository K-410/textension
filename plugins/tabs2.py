import bpy
import blf
from .. import utils, gl, types
from ..gl import _add_blend_4f_unclamped
from bpy.types import SpaceTextEditor


system = utils.system
import fnmatch
class Tabs:
    last_seq: list[tuple[int, int, str]] = []
    st: SpaceTextEditor
    tabs: list["Tab"] = []
    font_size: int = 12

    def __init__(self, st):
        self.st = st
        self.x = 100

    def draw(self) -> None:
        wu = system.wu
        Tab.width = 100 * wu // 20
        Tab.padding = 10 * wu // 20
        blf.size(0, self.font_size, int(system.pixel_size * system.dpi))
        offset = self.x
        for tab in self.validate_tabs():
            offset += tab.draw(self, offset) - 1

    @types._inject_const(data=utils._data, tabs=tabs)
    def validate_tabs(self) -> list["Tab"]:
        """Return a list of valid tab instances to draw
        """
        if [t.tab_index for t in "const data".texts] != self.last_seq:
            "const tabs"[:] = [Tab(*args) for args in _validate_tabs()]
            self.redraw_and_rebuild_cache(skip=self)
        return "const tabs"

    @classmethod
    def redraw_and_rebuild_cache(cls, skip: SpaceTextEditor = None) -> None:
        """Redraw all text editors and invalidate the instance cache.
        """
        (cache := get_instance.cache).clear()
        for space in utils.iter_spaces(space_type="TEXT_EDITOR"):
            cache[space] = (instance := get_instance(space))
            if skip is not instance:
                space.area.regions[0].tag_redraw()  # Redraw the header

    @classmethod
    def invalidate(cls):
        cls.last_seq.clear()


class Tab:
    color_default: tuple[float] = 0.23, 0.23, 0.23, 1.0
    color_active:  tuple[float] = 0.6,  0.6,  0.6,  1.0
    color_hover:   tuple[float] = 0.3,  0.5,  0.8,  0.15

    color_base:    tuple[float] = color_default
    color_mix:     tuple[float] = color_base

    rect = gl.GLRoundedRect(0.3, 0.3, 0.3, 1.0)

    active: bool = False
    hover: bool = False
    tab_index: int  # TODO: Maybe unused
    text_index: int
    cached_label: str = ""
    padding: int
    width: int = 100
    cached_data: tuple[str, int, int]

    def __init__(self, tab_index: int, text_index: int):
        self.tab_index = tab_index
        self.text_index = text_index

    @types._inject_const(dim=blf.dimensions, data=utils._data)
    def _compute(self, st: SpaceTextEditor):
        if (label := "const data".texts[self.text_index].name) != self.cached_label:
            self.cached_label = label
            max_width = self.width - (self.padding * 2)
            dots_width = "const dim"(0, "...")[0]
            base_height = "const dim"(0, "A")[1]
            if "const dim"(0, label)[0] > max_width:
                for i in range(1, len(label)):
                    if "const dim"(0, label[:i])[0] + dots_width <= max_width:
                        continue
                    label = label[:max(1, i - 1)] + "..."
                    break
            w = int("const dim"(0, label)[0])
            self.height = st.area.regions[0].height - 1
            x = (self.width  - w) // 2
            y = round((self.height - base_height) * 0.5)
            self.cached_data = label, x, y
        return self.cached_data

    def draw(self, instance: Tabs, offset: int):
        rect = self.rect
        color = self.color_default

        label, x, y = self._compute(instance.st)

        rect.set_background_color(*color)
        rect(offset, 0, self.width, self.height)
        blf.position(0, x + offset, y, 0)
        blf.color(0, 1.0, 1.0, 1.0, 1.0)
        blf.draw(0, label)
        return self.width

    def on_enter(self):
        if not self.active:
            self.color_mix = _add_blend_4f_unclamped(
                self.color_base, self.color_hover)

    def on_leave(self):
        self.color_mix = self.color_base

    def on_activate(self):
        self.color_base = self.color_active

    def on_deactivate(self):
        self.color_base = self.color_default


get_instance = utils.spacetext_cache_factory(Tabs)


def draw(context: bpy.types.Context):
    st = context.space_data
    tabs = get_instance(st)
    tabs.draw()


def _make_contiguous(seq):
    """Make a contiguous sequence of tab/text indices contiguous and
    make corrections to the text's tab indices.
    Expects the input sequence's first element to be sorted ascending.
    """
    texts = bpy.data.texts
    for head, (tab_index, text_index) in enumerate(tuple(seq)):
        if tab_index != head:
            texts[text_index].tab_index = tab_index = head
        seq[head] = (tab_index, text_index)
    return seq


@types._inject_const(last_seq=Tabs.last_seq, cached_seq=[])
def _validate_tabs() -> list[tuple[int, int]]:
    """Perform housekeeping of text tabs.
    Returns a copy of validated tab/text index pairs.
    """
    # Compare sequences to see if tabs changed since last time.
    texts = bpy.data.texts
    current_sequence = [(t.tab_index) for t in texts]
    if current_sequence != "const last_seq":
        new = []
        current = []
        for text_index, tab_index in enumerate(current_sequence):
            if tab_index is -1:
                new.append((tab_index, text_index))
            else:
                current.append((tab_index, text_index))
        # Sort the list by the first element in the pair (tab_index), then
        # make it a solid, contiguous sequence from 0 to n.
        "const cached_seq"[:] = _make_contiguous(sorted(current) + new)

        # The compare sequence is sorted by text index
        "const last_seq"[:] = [tab_index for tab_index, _ in sorted("const cached_seq", key=lambda x: x[1])]
    return "const cached_seq"


def move_tab(from_index, to_index) -> None:
    """Move a tab by index and shift the tabs inbetween.
    """
    if from_index >= 0:
        if from_index < len(seq := _validate_tabs()):
            seq.insert(to_index, seq.pop(from_index))
            return _make_contiguous(seq)
    raise ValueError(f"from_index out of bounds")


def _run_script_button(layout, p):
    row = layout.row()
    row.emboss = 'NORMAL' if p.run_script_emboss else 'NONE'
    row.operator("text.run_script",
                 text="Run Script" if p.run_script_text else "",
                 icon='PLAY' if p.run_script_icon else 'NONE')


# Draw new header.
def draw_header(self, context):
    layout = self.layout
    # p = prefs()
    # ptabs = utils.prefs().tabs

    # # Default ui elements.
    # default_row = layout.row()
    # default_row.prop(context.area, "ui_type", text="", icon_only=True)
    # default_row = layout.row(align=True)
    # default_row.alignment = 'LEFT'
    # menu = default_row.menu

    # if ptabs.show_new_menus:
    #     menu("TEXT_MT_text")
    #     menu("TEXTENSION_MT_edit")

    # else:
    #     for m in ("view", "text", "edit", "select", "format"):
    #         menu("TEXT_MT_" + m)

    # menu("TEXT_MT_templates")

    # # Run script left.
    # if ptabs.run_script_position == 'LEFT':
    #     _run_script_button(layout, ptabs)

    # # New tab, text tabs.
    # tabs_row = layout.row(align=True)
    # if ptabs.use_large_tabs:
    #     tabs_row.scale_y = 1.43

    # # tabs = Tabs()
    # row = layout.row()

    # if ptabs.run_script_position == 'RIGHT':
    #     layout.separator(factor=0.5)
    #     _run_script_button(layout, ptabs)

    # row.separator(factor=0.5)
    # wu = context.preferences.system.wu
    # # Pad the header to make space for tabs
    # # row.ui_units_x = tabs.width / wu  # XXX


def set_new_header_layout(enable: bool):
    funcs = bpy.types.TEXT_HT_header._dyn_ui_initialize()
    if enable and draw_header not in funcs:
        for i, func in enumerate(funcs):
            if func.__module__ == 'bl_ui.space_text':
                draw_header._org_func = func
                funcs[i] = draw_header
                break
    elif draw_header in funcs:
        index = funcs.index(draw_header)
        funcs[index] = funcs[index]._org_func


def enable():
    # Workspaces changes can cause spaces and regions to be recycled.
    # The instance cache must be cleared when this happens.
    utils.watch_rna((bpy.types.Window, "workspace"), get_instance.clear)
    utils.watch_rna((bpy.types.PreferencesView, "ui_scale"), Tabs.invalidate)

    bpy.types.Text.tab_index = bpy.props.IntProperty(default=-1)
    utils.add_draw_hook(draw, SpaceTextEditor, region='HEADER', args=utils._context)
    set_new_header_layout(True)


def disable():
    utils.remove_draw_hook(draw, region='HEADER')
    utils.unwatch_rna(get_instance.clear)
    utils.unwatch_rna(Tabs.invalidate)
    set_new_header_layout(False)
    get_instance.clear()
    Tabs.last_seq.clear()