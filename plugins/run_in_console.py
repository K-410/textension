from console_python import get_console
from .. import types, utils
from io import StringIO
import io
import bpy
import sys
from ..km_utils import kmi_new
from time import perf_counter
from types import ModuleType


# Print statements from scripts run from text editor are redirected here.
def _print(*args, sep=" ", end="\n", file=None, flush=False):
    if not _add_scrollback(sep.join([str(a) for a in args]).rstrip("\n"), type='OUTPUT'):
        # Default to regular print if no console is found.
        print(*args, sep=sep, end=end)
    return None


fake_builtins = ModuleType(sys.modules["builtins"].__name__)
fake_builtins.__dict__.update(sys.modules["builtins"].__dict__, print=_print)


@types._inject_const(op="CONSOLE_OT_scrollback_append", call=bpy.ops._op_call)
def _add_scrollback(string: str, type: str):
    # We always check the area, because changing areas in script is possible.
    if (area := get_active_console()) is None:
        return False
    for ln in string.replace("\t", "    ").splitlines():
        "const call"("const op", {"area": area}, {"text": ln, "type": type})
    return True


class TEXTENSION_OT_add_scrollback(types.TextOperator):
    @classmethod
    def poll(cls, context):
        return True

    string: bpy.props.StringProperty()
    type: bpy.props.EnumProperty(items=(('OUTPUT', 'Output', ''), ('ERROR', 'Error', ''), ('INFO', 'Info', '')), default='OUTPUT')
    def execute(self, context):
        if self.string:
            _add_scrollback(self.string, type=self.type)
        return {'CANCELLED'}



class Redirect(io.StringIO):
    def __init__(self, old: io.StringIO, type: str):
        super().__init__()
        self._buf = []
        self._old = old
        self.type = type


    def write(self, s: str):
        buf = self._buf
        if s is "\n":
            _add_scrollback("".join(buf), type=self.type)
            buf.clear()
        else:
            buf.append(s)
        return super().write(s)

# sys.stdout = Redirect(sys.stdout, 'OUTPUT')
# sys.stderr = Redirect(sys.stderr, 'ERROR')


class TEXTENSION_OT_run_in_console(types.TextOperator):
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
            t = perf_counter()
            exec(code, namespace)
            end_t = perf_counter() - t
        except:
            # SyntaxErrors aren't redirected, so write it to stderr.
            import traceback
            bpy.r = sys.exc_info()
            trace = traceback.format_exception(*sys.exc_info())
            # err.write("".join(trace))
                # trace = format_exception(*exc_info())
                # tb = "".join(trace[:1] + trace[2:])
            err.write("".join(trace[:1] + trace[2:]) + "\n")

        finally:
            console.locals.update(namespace)
            sys.stdout, sys.stderr, sys.stdin = old_io

        if (tmp := out.getvalue()):
            _add_scrollback(tmp, type='OUTPUT')

        if (tmp := err.getvalue()):
            _add_scrollback(tmp, type='ERROR')
        else:
            ms = end_t * 1000
            perf_fmt = f"{ms:.{max(0, 4 - len(repr(int(ms))))}f} ms"
            _add_scrollback(perf_fmt, 'INFO')
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'R', 'PRESS', ctrl=True)


def get_index(self):
    type_index = 0
    for area in self.id_data.areas:
        if area.type == 'CONSOLE':
            if area.spaces.active == self:
                return type_index
            type_index += 1
    assert False  # Cannot reach here.
    

@types._inject_const(context=utils._context)
def get_active_console() -> bpy.types.Area | None:
    try:
        windex, condex = "const context".screen["console_index"]
        # The console index is area-based and costs a small loop, but is more
        # reliable than direct area indexing.
        return [a for a in "const context".window_manager.windows[windex].screen.areas
                if a.type == 'CONSOLE'][condex]
    except:
        # Current screen does not store an active (or valid) console.
        # Find a new console and do some housekeeping while at it.
        windows = "const context".window_manager.windows
        console = substitute = win_index = None
        for win in windows:
            try:
                windex, condex = win.screen["console_index"]
                console = [a for a in windows[windex].screen.areas
                           if a.type == 'CONSOLE'][condex]
                win_index = windows[:].index(win)
                break
            except:
                win.screen.pop("console_index", None)
                if substitute is None:
                    for a in win.screen.areas:
                        if a.type == 'CONSOLE':
                            substitute = a
                            win_index = windows[:].index(win)
        if win_index is not None:
            assert (console := console or substitute)
            "const context".screen["console_index"] = win_index, console.spaces.active.index
            return console
        return None

import io
io.TextIOWrapper


def enable():
    utils.register_class(TEXTENSION_OT_run_in_console)
    utils.register_class(TEXTENSION_OT_add_scrollback)
    bpy.types.SpaceConsole.index = property(get_index)


def disable():
    utils.unregister_class(TEXTENSION_OT_run_in_console)
    utils.unregister_class(TEXTENSION_OT_add_scrollback)
    del bpy.types.SpaceConsole.index
