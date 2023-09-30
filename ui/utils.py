"""This module implements utilities for custom ui."""

from textension.utils import _context, noop, CFuncPtr, namespace, _check_type, defaultdict_list, consume, _region_types, inline, close_cells, _system
from textension.btypes import get_ARegionType, cast
from textension import btypes

from collections import defaultdict
from itertools import repeat
from types import FunctionType
from typing import Callable, Union
from operator import methodcaller

import bpy


__all__ = [
    "add_hit_test",
    "get_mouse_region",
    "idle_update",
    "remove_hit_test",
    "runtime",
    "set_hit",
    "add_draw_hook",
    "remove_draw_hook"
]


_editors = defaultdict(repeat(defaultdict(tuple)).__next__)
_capsules = []
# _leave_handlers = {}

runtime = namespace(hit=None, main_draw=None, cursor_key=(None, None))

# TODO: Rename this to something more descriptive.
_visible = []

# A list of widgets that take input focus per space data.
_focus_stack = defaultdict_list()
_draw_hook_index_map = {}


# Not sure how to get this via RNA.
space_map = {
    "CLIP_EDITOR":      bpy.types.SpaceClipEditor,
    "CONSOLE":          bpy.types.SpaceConsole,
    "DOPESHEET_EDITOR": bpy.types.SpaceDopeSheetEditor,
    "FILE_BROWSER":     bpy.types.SpaceFileBrowser,
    "GRAPH_EDITOR":     bpy.types.SpaceGraphEditor,
    "IMAGE_EDITOR":     bpy.types.SpaceImageEditor,
    "INFO":             bpy.types.SpaceInfo,
    "NLA_EDITOR":       bpy.types.SpaceNLA,
    "NODE_EDITOR":      bpy.types.SpaceNodeEditor,
    "OUTLINER":         bpy.types.SpaceOutliner,
    "PREFERENCES":      bpy.types.SpacePreferences,
    "PROPERTIES":       bpy.types.SpaceProperties,
    "SEQUENCE_EDITOR":  bpy.types.SpaceSequenceEditor,
    "SPREADSHEET":      bpy.types.SpaceSpreadsheet,
    "TEXT_EDITOR":      bpy.types.SpaceTextEditor,
    "VIEW_3D":          bpy.types.SpaceView3D,
}


def main_draw(context, region):
    st = _context.space_data

    # Unlock line snapping.
    st.flags |= btypes.defs.ST_SCROLL_SELECT

    runtime.main_draw(context, region)

    dna = st.internal
    rt = dna.runtime
    line_height = max(1, int(rt._lheight_px * 1.3))

    view_half = _context.region.height / line_height * 0.5
    max_top = st.drawcache.total_lines - view_half

    if max_top < 0:
        max_top = 0
    offset = rt.scroll_ofs_px[1] / line_height

    if dna.top + offset > max_top:
        dna.top = int(max_top)
        rt.scroll_ofs_px[1] = int(max_top % 1.0 * line_height)
        runtime.main_draw(context, region)


def patch_main_draw():
    _check_type(runtime.main_draw, type(None))
    cfunc = btypes.ARegionType.get_member_type("draw")
    art = get_ARegionType('TEXT_EDITOR', 'WINDOW')
    runtime.main_draw = cast(art.draw, cfunc)
    art.draw = cfunc(main_draw)


def unpatch_main_draw():
    cfunc = btypes.ARegionType.get_member_type("draw")
    _check_type(runtime.main_draw, cfunc)

    art = get_ARegionType('TEXT_EDITOR', 'WINDOW')
    art.draw = runtime.main_draw
    runtime.main_draw = None


def idle_update():
    if (win := _context.window):
        win.cursor_warp(*win.mouse)


def _hit_test(clear=False):
    window = _context.window
    region = _context.region

    try:
        handler = _editors[_context.area.type][region.type]
    except:  # AttributeError. Area or region is None. Nothing to hit test.
        return None

    if handler:
        x, y = window.mouse
        if not any(map(methodcaller("__call__", x - region.x, y - region.y), handler)):
            if clear and runtime.hit:
                runtime.hit.on_leave()


def set_widget_focus(widget):
    stack = _focus_stack[_context.space_data]

    if widget in stack:
        stack.remove(widget)

    stack += widget,
    widget.on_focus()


def get_widget_focus():
    if stack := _focus_stack[_context.space_data]:
        return stack[-1]


def clear_widget_focus(space_data=None):
    if space_data is None:
        consume(map(clear_widget_focus, _focus_stack))

    else:
        while stack := _focus_stack[space_data]:
            widget = stack.pop()
            widget.on_defocus()


class HitTestHandler(list):
    space:  str
    region: str

    real:   CFuncPtr
    default: Callable

    def __call__(self, dna_win, dna_area, dna_region):
        try:
            region = _context.region
            event  = _context.window.event

            x = event.posx - region.x
            y = event.posy - region.y
        except AttributeError:  # Not in screen context.
            return None


        for hook in self:
            if hit := hook(x, y):
                break  # The hook is blocking.
        else:
            # Call the default region mouse handler.
            self.default(dna_win, dna_area, dna_region)
            hit = None

        if hit is not runtime.hit:
            set_hit(hit)

    @classmethod
    def iter_hooks(cls):
        try:
            yield from _editors[_context.area.type][_context.region.type]
        except:  # AttributeError. Area or region is None. Nothing to hit test.
            return None


def set_hit(hit):
    if runtime.hit:
        runtime.hit.on_leave()

    runtime.hit = hit

    if hit:
        _context.window.cursor_set(hit.cursor)
        hit.on_enter()

        # When a Widget changes the cursor we need to sync the new cursor to
        # prevent the leave handler from exiting the Widget.
        runtime.cursor_key = _context.window.internal.cursor, _context.region.as_pointer()


def add_hit_test(hit_test_func: Callable, space_type: str, region_type: str):
    if space_type not in _space_types:
        raise ValueError(f"Bad space type {space_type!r}")

    if region_type not in _region_types:
        raise ValueError(f"Bad region type {region_type!r}")

    editor = _editors.setdefault(space_type, {})
    handler = editor.get(region_type)

    if not handler:
        art = get_ARegionType(space_type, region_type)

        handler = HitTestHandler()
        handler.space = space_type
        handler.region = region_type

        cfunc = btypes.ARegionType.get_member_type("cursor")
        handler.real = default = cast(art.cursor, cfunc)

        # If ``default`` is NULL, use a dummy handler we can safely call.
        handler.default = default or noop
        art.cursor = cfunc(handler)
        editor[region_type] = handler

    handler += hit_test_func,
    _capsules.append((handler, hit_test_func))


def remove_hit_test(hit_test_func: Callable):
    for capsule in _capsules:
        if capsule[1] is hit_test_func:
            break
    else:
        raise ValueError(f"Function not registered\n{_capsules}")

    handler = capsule[0]
    handler.remove(capsule[1])

    if len(handler) == 0:  # No more hooks, remove handler.
        space = handler.space
        region = handler.region
        art = get_ARegionType(space, region)

        # Restore art.cursor.
        art.cursor = handler.real

        del handler.real
        del handler.default

        editor = _editors[space]
        del editor[region]
        if not editor:
            del _editors[space]
    return None


def get_mouse_region() -> tuple[int, int]:
    try:
        event = _context.window.event

    # Editors aren't ready yet.
    except AttributeError:
        return -1, -1

    region = _context.region
    return (event.posx - region.x, event.posy - region.y)


@inline
def add_region_change_handler(handler):
    return _region_change_handlers.append


@inline
def remove_region_change_handler(handler):
    return _region_change_handlers.remove



def _region_draw_handler_factory(hooks):
    from .utils import runtime, _system

    def region_draw_handler():
        runtime.wu = _system.wu
        runtime.wu_norm = runtime.wu * 0.05
        
        for draw_callback in hooks:
            draw_callback()

    return region_draw_handler


def add_draw_hook(
        hook:        FunctionType,
        args:        tuple = (),
        space_type:  str = 'TEXT_EDITOR',
        region_type: str = 'WINDOW',
        draw_type:   str = 'POST_PIXEL',
        draw_index:  int = -1):

    if not isinstance(args, tuple):
        args = (args,)

    space = space_map.get(space_type)

    _check_type(hook, FunctionType)
    _check_type(space, bpy.types.Space)
    _check_type(draw_index, int)

    if args and hook.__defaults__ is None:
        hook.__defaults__ = args

    _draw_hook_index_map[id(hook)] = draw_index

    regions = _editors.setdefault(space, {})

    if region_type not in regions:
        # Add a new draw callback for this region.
        hooks = []
        handler = _region_draw_handler_factory(hooks)
        handle = space.draw_handler_add(handler, (), region_type, draw_type)
        regions[region_type] = (handle, hooks)

    hooks = regions[region_type][1]

    if draw_index != -1:
        for i, hk in enumerate(hooks):
            hook_index = _draw_hook_index_map.get(id(hk), -1)
            if hook_index > -1 and hook_index > draw_index:
                hooks.insert(i, hook)
                return None

    hooks += hook,


def remove_draw_hook(fn: Callable, region: str='WINDOW'):
    found = False

    for space, regions in list(_editors.items()):
        if region in regions:
            handle, hooks = regions[region]
            if fn in hooks:
                found = True
                hooks.remove(fn)
                if not hooks:
                    space.draw_handler_remove(handle, region)
                    del regions[region]
            if not regions:
                del _editors[space]
        if found:
            break
    if not found:
        raise RuntimeError(f"'{fn.__name__}' not a registered hook")
    _draw_hook_index_map.pop(id(fn), None)
    return found
