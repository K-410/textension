"""Utilities for custom ui like drawing and hit testing."""

from textension.utils import _context, noop, CFuncPtr, namespace, _check_type
from textension.btypes import get_area_region_type, cast, SpaceType, get_space_type
from textension import btypes

from collections import defaultdict
from itertools import repeat
from typing import Optional, Callable, Union
from operator import methodcaller
import bpy


__all__ = [
    "add_hit_test",
    "get_mouse_region",
    "idle_update",
    "remove_hit_test",
    "runtime",
    "set_hit"
]


_editors = defaultdict(repeat(defaultdict(tuple)).__next__)
_capsules = []
# _leave_handlers = {}

runtime = namespace(hit=None, main_draw=None, cursor_key=(None, None))

# TODO: Rename this to something more descriptive.
_visible = []

# A list of widgets that take input focus per space data.
_focus_stack = {}


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


# TODO: Remove this when textension is unregistered.
def patch_main_draw():
    _check_type(runtime.main_draw, type(None))
    cfunc = btypes.ARegionType.get_member_type("draw")
    art = get_area_region_type('TEXT_EDITOR', 'WINDOW')
    runtime.main_draw = cast(art.draw, cfunc)
    art.draw = cfunc(main_draw)


def unpatch_main_draw():
    cfunc = btypes.ARegionType.get_member_type("draw")
    _check_type(runtime.main_draw, cfunc)

    art = get_area_region_type('TEXT_EDITOR', 'WINDOW')
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
    space_data = _context.space_data
    _check_type(space_data, bpy.types.Space)

    stack = _focus_stack.setdefault(space_data, [])

    if widget in stack:
        stack.remove(widget)

    stack += widget,
    widget.on_focus()


def get_widget_focus():
    space_data = _context.space_data
    if stack := _focus_stack.get(space_data):
        return stack[-1]


def clear_widget_focus(space_data=None):
    _check_type(space_data, bpy.types.Space, type(None))

    if space_data is None:
        any(map(clear_widget_focus, tuple(_focus_stack)))

    else:
        stack = _focus_stack.pop(space_data, None)
        while stack:
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


def add_hit_test(hook: Callable, space: str = 'TEXT_EDITOR', region: str = 'WINDOW'):
    editor = _editors.setdefault(space, {})
    handler = editor.get(region)

    if not handler:
        art = get_area_region_type(space, region)

        handler = HitTestHandler()
        handler.space = space
        handler.region = region

        cfunc = btypes.ARegionType.get_member_type("cursor")
        handler.real = default = cast(art.cursor, cfunc)

        # If ``default`` is NULL, use a dummy handler we can safely call.
        handler.default = default or noop
        art.cursor = cfunc(handler)
        editor[region] = handler

    handler += hook,
    _capsules.append((handler, hook))
    return id(_capsules[-1])


def remove_hit_test(ref: Union[Callable, int]):
    if callable(ref):  
        finder = lambda c: c[1] is ref
    elif isinstance(ref, int):
        finder = lambda c: id(c) == ref
    else:
        raise TypeError("Expected hook or capsule id")

    try:
        _capsules.remove(capsule := next(filter(finder, _capsules)))
    except (StopIteration, ValueError):
        raise ValueError(f"{ref} not registered\n{_capsules}")

    handler = capsule[0]
    handler.remove(capsule[1])

    if len(handler) == 0:  # No more hooks, remove handler.
        space = handler.space
        region = handler.region
        art = get_area_region_type(space, region)

        # Restore art.cursor.
        art.cursor = handler.real

        del handler.real
        del handler.default

        editor = _editors[space]
        del editor[region]
        if not editor:
            del _editors[space]


def get_mouse_region() -> tuple[int, int]:
    try:
        event = _context.window.event

    # Editors aren't ready yet.
    except AttributeError:
        return -1, -1

    region = _context.region
    return (event.posx - region.x, event.posy - region.y)
