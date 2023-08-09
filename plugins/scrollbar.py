# This plugin implements new scrollbar for the text editor.

from textension.ui.widgets import Scrollbar, Thumb, TextDraw, Widget
from textension.utils import _context, _system, make_space_data_instancer, _forwarder, noop, inline
from textension.btypes.defs import ST_SCROLL_SELECT
from textension import utils, ui
import bpy


class Editor(TextDraw):
    x = 0
    y = 0
    top           = _forwarder("st.top")
    visible_lines = _forwarder("st.visible_lines")
    reset_cache = None.__init__  # Satisfy TextDraw.set_view()
    scrollbar_width = 20

    def __init__(self, st):
        self.st = st
        self.children = []  # Unused, but needed for scrollbar widget.
        self.scrollbar = EditorScrollbar(self)

    @property
    def top(self):
        return self.st.top + (self.st.runtime._offs_px[1] / self.line_height)

    @top.setter
    def top(self, new_top):
        self.st.top = int(new_top)
        self.st.runtime._offs_px[1] = int((new_top % 1.0) * self.line_height) 

    @property
    def line_height(self):
        return max(1, int(self.st.runtime._lheight_px * 1.3))

    @property
    def num_lines(self):
        return self.st.drawcache.total_lines + ((_context.region.height / self.line_height) * 0.5)


    @property
    def width(self):
        scrollbar_width = int(_system.pixel_size * 8.0 * 0.21) - (_system.wu * 0.05)
        return int(_context.region.width - scrollbar_width)


    @property
    def lines(self):
        return _context.edit_text.lines

    @property
    def max_top(self):
        return max(0, self.st.drawcache.total_lines - ((_context.region.height / self.line_height) * 0.5))

    @property
    def position_inner(self) -> tuple[float, float]:
        return 0.0, 0.0

    @inline
    def draw(self):
        return _forwarder("scrollbar.draw")

    size   = _forwarder("context.region.width", "context.region.height", rtype=tuple[int, int])
    height = _forwarder("context.region.height", rtype=int)
    width_inner  = width
    height_inner = height


class Overlay(Widget):
    hit_test = noop

    def draw(self, y, height):
        self.rect.draw(self.parent.x, y, self.parent.rect.width, height)


class Cursor(Overlay):
    background_color = 0.4, 0.4, 0.4, 1.0
    border_color     = 0.4, 0.4, 0.4, 1.0


class Selection(Overlay):
    background_color = 0.7, 0.13, 0.135, 0.3
    border_color     = 0.7, 0.13, 0.135, 0.3


class EditorScrollbarThumb(Thumb):
    background_color = 0.24, 0.24, 0.24, 1.0
    border_color     = 0.24, 0.24, 0.24, 1.0


# UserDef.themes is persistent. The contents are not.
themes = _context.preferences.themes


class EditorScrollbar(Scrollbar):

    @property
    def is_passthrough(self):
        return True

    def init_thumb(self):
        self.thumb = EditorScrollbarThumb(scroll=self)

    @property
    def background_color(self):
        return tuple((v + 0.02) for v in themes["Default"].text_editor.space.back) + (1.0,)

    def on_leave(self):
        self.thumb.set_highlight(False)
        utils.safe_redraw_from_space_data(self.parent.st)

    def __init__(self, parent):
        super().__init__(parent)
        self.selection_overlay = Selection(parent=self)
        self.cursor_overlay = Cursor(parent=self)
        self.thumb.on_leave = self.on_leave

    def set_view(self, ratio: float):
        self.parent.st.internal.flags |= ST_SCROLL_SELECT
        return super().set_view(ratio)

    def draw(self):
        st = self.parent.st
        
        # The gutter background is updated on every redraw. Not particularly
        # ideal - we could use RNA subscription here.
        self.update_uniforms(background_color=self.background_color)

        if text := st.text:
            super().draw()

            # Draw the cursor and selection range.
            y, h = st.scroll_select_y
            h -= y
            curl = text.current_line_index
            sell = text.select_end_line_index
            if curl != sell:
                self.selection_overlay.draw(y, h)
            if curl > sell:
                y += h
            self.cursor_overlay.draw(y, (2.0 * (_system.wu * 0.05)))

    def on_activate(self):
        bpy.ops.textension.ui_scroll_jump('INVOKE_DEFAULT')

get_editor = make_space_data_instancer(Editor)


def draw_scrollbar():
    editor = get_editor()
    editor.draw()


def test_scrollbar(x, y):
    editor = get_editor()
    return editor.scrollbar.hit_test(x, y)


def enable():
    utils.add_draw_hook(draw_scrollbar)
    ui.add_hit_test(test_scrollbar)

def disable():
    utils.remove_draw_hook(draw_scrollbar)
    ui.remove_hit_test(test_scrollbar)
