"""This module implements script outputs in the interactive console."""

from console_python import get_console
from textension import utils
from io import StringIO
from time import perf_counter

import bpy
import sys


_context = utils._context


# Print statements from scripts run from text editor are redirected here.
def _print(*args, sep=" ", end="\n", file=None, flush=False):
    if not _add_scrollback(sep.join([str(a) for a in args]).rstrip("\n"), type='OUTPUT'):
        # If no console is found, print normally..
        print(*args, sep=sep, end=end)
    return None


fake_builtins = type(bpy)(sys.modules["builtins"].__name__)
fake_builtins.__dict__.update(sys.modules["builtins"].__dict__, print=_print)
_op_call = bpy.ops._op_call


def _add_scrollback(string: str, type: str):
    # Check the area each time because a script may change it.
    if (area := get_active_console()) is not None:
        for ln in string.replace("\t", "    ").splitlines():
            _op_call("CONSOLE_OT_scrollback_append", {"area": area}, {"text": ln, "type": type})
        return True
    return False


class TEXTENSION_OT_add_scrollback(utils.TextOperator):

    string: bpy.props.StringProperty()
    type: bpy.props.EnumProperty(items=(('OUTPUT', 'Output', ''),
                                        ('ERROR',  'Error',  ''),
                                        ('INFO',   'Info',   '')),
                                 default='OUTPUT')
    def execute(self, context):
        if self.string:
            _add_scrollback(self.string, type=self.type)
        return {'CANCELLED'}


class TEXTENSION_OT_run_in_console(utils.TextOperator):
    poll = utils.text_poll
    utils.km_def("Text", 'R', 'PRESS', ctrl=True)

    def execute(self, context):
        if (area := get_active_console()) is None:
            return {'CANCELLED'}

        text = context.edit_text
        _add_scrollback(f"\n{text.name}:", 'INFO')
        console = get_console(hash(area.regions[-1]))[0]

        old_io = sys.stdout, sys.stderr, sys.stdin
        sys.stdout, sys.stderr, sys.stdin = (out, err, _) = \
            [StringIO(), StringIO(), None]

        namespace = {
            "__name__":     "__main__",
            "__builtins__": fake_builtins,
            "__file__":     text.name
        }

        try:
            code = compile(text.as_string(), text.name, "exec")
            # Time the execution.
            t = perf_counter()
            exec(code, namespace)
            end_t = perf_counter() - t
        except:
            # SyntaxErrors aren't redirected, write manually it to stderr.
            import traceback
            trace = traceback.format_exception(*sys.exc_info())
            err.write("".join([trace[0]] + trace[2:]) + "\n")

        finally:
            console.locals.update(namespace)

            del namespace
            sys.stdout, sys.stderr, sys.stdin = old_io

        if (tmp := out.getvalue()):
            _add_scrollback(tmp, type='OUTPUT')

        if (tmp := err.getvalue()):
            _add_scrollback(tmp, type='ERROR')
        else:
            ms = end_t * 1000
            perf_fmt = f"{ms:.{max(0, 4 - len(repr(int(ms))))}f} ms"
            _add_scrollback(perf_fmt, 'INFO')

        # Scroll to bottom
        with utils.context_override(area=area, region=area.regions[-1]):
            bpy.ops.console.history_cycle(reverse=True)
            bpy.ops.console.history_cycle(reverse=False)
        return {'FINISHED'}


def get_console_index(self):
    type_index = 0
    for area in self.id_data.areas:
        if area.type == 'CONSOLE':
            if area.spaces.active == self:
                return type_index
            type_index += 1
    assert False  # Cannot reach here.


def _get_active_console():
    windex, condex = _context.screen["console_index"]
    # The console index is area-based and costs a small loop, but is more
    # reliable than direct area indexing.
    return [a for a in _context.window_manager.windows[windex].screen.areas
            if a.type == 'CONSOLE'][condex]


def get_active_console() -> bpy.types.Area | None:
    wm = _context.window_manager
    try:
        return _get_active_console()
    except:
        pass

    # Blender is running headless.
    if bpy.app.background:
        return None

    # Current screen does not store an active or valid console.
    # Find a new console and do some housekeeping while at it.
    console = substitute = windex = None
    windows = wm.windows[:]
    for win in wm.windows:
        try:
            console = _get_active_console()
            windex = windows.index(win)
            break
        except:
            # Try pop the invalid index
            win.screen.pop("console_index", None)
            if substitute is None:
                for area in win.screen.areas:
                    if area.type == 'CONSOLE':
                        substitute = area
                        windex = windows.index(win)
    
    if (console := console or substitute):
        _context.screen["console_index"] = windex, console.spaces.active.index
        return console
    return None


def enable():
    utils.register_classes(classes)
    bpy.types.SpaceConsole.index = property(get_console_index)


def disable():
    utils.unregister_classes(classes)
    del bpy.types.SpaceConsole.index


classes = (
    TEXTENSION_OT_run_in_console,
    TEXTENSION_OT_add_scrollback
)
