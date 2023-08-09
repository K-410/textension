# This module implements widget operators.

from textension.utils import TextOperator, _system, safe_redraw, km_def, defer, is_spacetext, inline, set_name
from textension.core import ensure_cursor_view, find_word_boundary
from .utils import runtime, _editors, _hit_test, get_mouse_region, set_hit, clear_widget_focus, _visible, HitTestHandler
from .widgets import *

import time
import bpy


def _find_scrollbar(elem: Widget):
    while elem:
        if isinstance(elem, Scrollbar):
            return elem

        elif isinstance(elem, Widget):
            elem = (getattr(elem, "scrollbar", None) or
                    getattr(elem, "parent", None))
    return elem


class TEXTENSION_OT_ui_mouse(TextOperator):
    km_def("Screen Editing", 'LEFTMOUSE', 'PRESS', head=True)
    bl_options = {'INTERNAL'}

    active: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return is_spacetext(context.space_data) and context.region.type == 'WINDOW'

    def invoke(self, context, event):
        # A widget is being hovered. Activate it and eat the event.
        if runtime.hit and context.area and context.area.type in _editors:
            runtime.hit.on_activate()
            return {'CANCELLED'}

        x, y = get_mouse_region()
        # A widget was hit tested isn't set as a hit. This happens when an
        # action zone overlaps the area which the hit test handler doesn't
        # detect. In this case the action zone takes precedence.
        for hook in HitTestHandler.iter_hooks():
            if hook(x, y):
                return {'PASS_THROUGH'}

        # At this point regular mouse events in the text editor are processed.
        # If any Widget is on the space_data's focus stack, defocus them.
        clear_widget_focus(space_data=context.space_data)

        # Start modal, but propagate the event to text.cursor_set.
        if not self.active:
            defer(lambda ctx=context.copy(): bpy.ops.textension.ui_mouse(ctx, 'INVOKE_DEFAULT', active=True))
            return {'PASS_THROUGH'}

        self.end = False
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self.end:
            return {'CANCELLED'}
        elif event.value == 'RELEASE':
            self.end = True

        y = event.mouse_region_y

        if y < 0 or (y := y - context.region.height) > 0:
            a = y / -context.space_data.line_height * 0.5
            bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=a, speed=100)

        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if event.type == 'WHEELUPMOUSE':
                lines = -3
            else:
                lines = 3
            bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=lines)
            return {'PASS_THROUGH'}
        return {'PASS_THROUGH'}



class TEXTENSION_OT_ui_scroll_lines(TextOperator):
    km_def("Text Generic", 'WHEELDOWNMOUSE', 'PRESS', lines= 3)
    km_def("Text Generic", 'WHEELUPMOUSE',   'PRESS', lines=-3)
    bl_options = {'INTERNAL'}

    lines: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return runtime.hit

    def invoke(self, context, event):
        # Find it dynamically. The mouse doesn't have to be in the gutter to
        # activate it, as is the case for wheel scrolling and arrow/page keys.
        scroll = _find_scrollbar(runtime.hit)
        if not scroll or scroll.is_passthrough:
            return {'PASS_THROUGH'}

        self.repeat_delay = time.monotonic()
        self.view = scroll.parent
        self.one_shot = self.lines != 0

        if self.one_shot:
            self.apply_scroll()
            return {'CANCELLED'}

        # We clicked the scrollbar gutter.
        if 0.0 <= event.mouse_region_x - scroll.x <= scroll.width:
            self.lines = int(self.view.visible_lines)
            if event.mouse_region_y > scroll.thumb_y + scroll.y:
                self.lines = -self.lines

        self.apply_scroll()
        self.repeat_delay += 0.225
        self.timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def apply_scroll(self):
        curr = time.monotonic()
        if self.repeat_delay <= curr:
            if not self.one_shot:
                # Stop scrolling when the thumb reaches the mouse. Obviously
                # this only happens when clicking from the gutter.
                y = get_mouse_region()[1]
                thumb = self.view.scrollbar.thumb
                if (y > thumb.y and self.lines > 0) or \
                   (y < thumb.y + thumb.height and self.lines < 0):
                        return
            self.repeat_delay = curr + 0.04
            self.view.scroll(self.lines)
            _hit_test()

    def modal(self, context, event):
        self.apply_scroll()
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'RET', 'ESC', 'WINDOW_DEACTIVATE'}:
            context.window_manager.event_timer_remove(self.timer)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_ui_scroll_jump(TextOperator):
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        scroll = runtime.hit
        if isinstance(scroll, Scrollbar):
            middle = scroll.thumb.height / scroll.height * 0.5
            target = 1.0 - (event.mouse_region_y / context.region.height)

            scroll.set_view(target + middle * (target / 0.5 - 1.0))
            set_hit(scroll.thumb)
            bpy.ops.textension.ui_scrollbar('INVOKE_DEFAULT')
            return {'CANCELLED'}
        return {'PASS_THROUGH'}


class TEXTENSION_OT_ui_scrollbar(TextOperator):
    """Activate the scrollbar so it can be dragged to transform the view."""
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if not isinstance(runtime.hit, Thumb):
            return {'PASS_THROUGH'}

        self.scrollbar  = runtime.hit.parent
        self.init_value = self.scrollbar.parent.get_view_top()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return {'CANCELLED'}

        elif event.type == 'MOUSEMOVE':
            scrollbar = self.scrollbar
            value     = self.init_value

            if abs(event.mouse_prev_press_x - event.mouse_x) < 140:
                delta  = event.mouse_prev_press_y - event.mouse_y
                parent_height = scrollbar.get_parent_height()

                height = parent_height - scrollbar.thumb_height
                value += delta / max(1.0, height)
            scrollbar.set_view(value)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_ui_resize(TextOperator):
    bl_options = {'INTERNAL'}
    axis: bpy.props.EnumProperty(
        items=(('HORIZONTAL', "Horizontal", "Resize horizontally"),
               ('VERTICAL', "Vertical", "Resize vertically"),
               ('CORNER', "Corner", "Resize from corner")))
    
    @classmethod
    def poll(cls, context):
        return isinstance(runtime.hit, (EdgeResizer, BoxResizer))

    def invoke(self, context, event):
        self.subject = runtime.hit.get_subject()

        self.start_width, self.start_height = self.subject.rect.size
        self.min_height = self.subject.line_height
        self.min_width = int(150 * _system.wu * 0.05)

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            # Clear resizer hover if the edges were clamped to area edges.
            _hit_test(clear=True)
            return {'CANCELLED'}

        elif event.type == 'MOUSEMOVE':
            subject = self.subject
            x_delta = event.mouse_x - event.mouse_prev_press_x
            y_delta = event.mouse_y - event.mouse_prev_press_y

            x, y = subject.rect.position
            w, h = subject.rect.size

            if self.axis in {'CORNER', 'HORIZONTAL'}:
                w = max(self.min_width, self.start_width + x_delta)
                # Clamp width
                w -= max(0, (x + w) - context.region.width)

            if self.axis in {'CORNER', 'VERTICAL'}:
                rh = context.region.height
                # Clamp height
                max_height = min(rh - (rh - (y + h)), self.start_height - y_delta)
                h = max(self.min_height, max_height)

                if isinstance(subject, TextDraw):
                    # Vertically resizing past the bottom shifts the top.
                    if subject.top > 0:
                        # TODO: Not sure why -2 is needed.
                        visible_lines = (h - 2) / subject.line_height
                        shift = subject.top + visible_lines - len(subject.lines)
                        if shift > 0.0:
                            subject.set_top(subject.top - shift)
            # subject.rect.size = (w, h)
            # safe_redraw()
            if test_and_update(subject.rect, "size", (w, h)):
                safe_redraw()
        return {'RUNNING_MODAL'}


# Clears a hit Widget when the cursor or region changes.
class TEXTENSION_OT_ui_leave_handler(TextOperator):
    # Use "Window" (not "Screen Editing") so we can catch the cursor type
    # *after* wm_event_do_handlers has changed it.
    km_def("Window", 'MOUSEMOVE', 'NOTHING')
    bl_options = {'INTERNAL'}

    @inline
    def poll(cls, context) -> bool:
        from builtins import AttributeError
        from .utils import set_hit, runtime

        @classmethod
        @set_name("region_leave_handler")
        def poll(cls, context):
            try:
                key = context.window.internal.cursor, context.region.as_pointer()
            except (AttributeError, KeyboardInterrupt):
                key = None, None
            # If the cursor or region changes, leave any hovered Widget.
            if key != runtime.cursor_key:
                runtime.cursor_key = key
                set_hit(None)
            return False

        return poll


class TEXTENSION_OT_ui_dismiss(TextOperator):
    km_def("Text", 'ESC', 'PRESS')
    bl_options = {'INTERNAL'}

    def execute(self, context):
        if _visible:
            _visible.pop().dismiss()
            return {'CANCELLED'}

        # Pass on the event.
        return {'PASS_THROUGH'}


class TEXTENSION_OT_ui_input_set_cursor(TextOperator):
    bl_options = {'INTERNAL'}

    input: Input

    @classmethod
    def poll(cls, context):
        return isinstance(runtime.hit, Input)

    def invoke(self, context, event):
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        
        self.input = runtime.hit
        count = self.input.clicks.get_and_track()

        if count == 1:
            self.input.set_anchor(self.hit_test_column(event))
            self.modal = self.modal_select

        elif count == 2:
            self.modal = self.modal_snap_select
            self.init_range = self.hit_test_word_indices(event)

        elif count >= 3:
            self.input.select_all()
            return {'FINISHED'}

        # Call on the initial click.
        self.modal(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    
    def hit_test_column(self, event):
        x = event.mouse_region_x - self.input.rect.x
        return self.input.hit_test_column(x)
    
    def hit_test_word_indices(self, event):
        input = self.input
        column = self.hit_test_column(event)
        start = column - find_word_boundary(input.string[:column][::-1])
        return start, start + find_word_boundary(input.string[start:])

    def modal_snap_select(self, context, event):
        if event.value == 'RELEASE':
            return {'FINISHED'}

        start, end = self.hit_test_word_indices(event)
        init_start, init_end = self.init_range

        if start < init_start:
            init_start = init_end
            end = start
        self.input.set_cursor(init_start, end)
        return {'RUNNING_MODAL'}

    def modal_select(self, context, event):
        input = self.input

        x = event.mouse_region_x - input.rect.x
        self.input.focus = self.input.hit_test_column(x)
        context.area.tag_redraw()
        if event.value == 'RELEASE':
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return self.modal(context, event)


classes = (
    TEXTENSION_OT_ui_dismiss,
    TEXTENSION_OT_ui_input_set_cursor,
    TEXTENSION_OT_ui_leave_handler,
    TEXTENSION_OT_ui_mouse,
    TEXTENSION_OT_ui_resize,
    TEXTENSION_OT_ui_scroll_jump,
    TEXTENSION_OT_ui_scroll_lines,
    TEXTENSION_OT_ui_scrollbar,
)
