"""This module implements an experimental undo stack for texts.

This affects the operators:
- ED_OT_undo:         Resync when undoing (ctrl z)
- ED_OT_redo:         Resync when redoing (ctrl shift z)
- ED_OT_undo_history: Resync when undoing a specific undo step.
"""

from textension.btypes import defs
from textension.utils import text_from_id, iter_spaces, consume, map_not
from textension.core import ensure_cursor_view
from textension import utils, overrides
from itertools import compress

import bpy


_context = utils._context
_data    = utils._data

undo_stacks: dict[int, utils.UndoStack] = {}


# Dict of python operators using the new undo system
_pyop_store: dict[bpy.types.Operator, list[str]] = {}

# List of Default overrides with undos.
_applied_default_undos = []


_stack_sync_ids = set()
_stack_sync_map = {}


# Examines the last type character and determine if an undo that would
# otherwise be grouped, should be split.
def _should_split(text: bpy.types.Text) -> bool:
    # If the cursor has a selection, treat it as a split.
    curl, curc, sell, selc = text.cursor_sorted
    if curc != selc or curl != sell or curc < 1:
        return True
    separators = {*" !\"#$%&\'()*+,-/:;<=>?@[\\]^`{|}~."}
    return text.lines[curl].body[curc - 1] in separators


class TextAdapter(utils.Adapter):
    metadata: tuple[str, float, int]

    def __init__(self, text: bpy.types.Text) -> None:
        utils._check_type(text, bpy.types.Text)
        self.name = text.name
        self.id   = text.id
        self.metadata = ("", 0.0, 0)

    @property
    def text(self) -> bpy.types.Text:
        texts = _data.texts
        try:
            text = texts[self.name]
            text_id = text.id
            assert text_id == self.id

        except (KeyError, AssertionError):

            # Find the text by its id.
            if text := text_from_id(self.id):
                self.name = text.name

            # Find the text by its name.
            elif text := texts.get(self.name):
                remap_id = text.id
                assert remap_id not in undo_stacks
                undo_stacks[remap_id] = undo_stacks.pop(self.id)
                self.id = remap_id

            # Blender removed the text. Restore it.
            else:
                text = texts.new(self.name)
                text.id_proxy = self.id
        return text

    @utils.inline
    def get_string(self):
        return utils._forwarder("text.as_string")

    @utils.inline
    def get_cursor(self):
        return utils._forwarder("text.cursor.copy")

    def set_cursor(self, cursor):
        self.text.cursor = cursor

    @utils.inline
    def set_string(self, string: str) -> None:
        return utils._forwarder("text.from_string")

    def get_should_split(self, hint: bool) -> bool:
        return _should_split(self.text)

    def on_update(self, restore=False) -> None:
        text = self.text
        internal = text.internal
        if restore:
            text.filepath, internal.mtime, internal.flags = self.metadata
        else:
            self.metadata = (text.filepath, internal.mtime, internal.flags)

    @property
    def is_valid(self):
        return bool(self.text)


# Dummy stack. Makes it easier to write generic code for undo/redo.
@utils.inline_class(utils.Adapter())
class NO_STACK(utils.UndoStack):
    undo = ()
    redo = ()

    __bool__ = bool

    @utils.inline
    def update_cursor(self):
        return utils.noop_noargs


def get_undo_stack(text: bpy.types.Text) -> utils.UndoStack:
    assert text.__class__ is bpy.types.Text
    try:
        return undo_stacks[text.id]
    except KeyError:
        return undo_stacks.setdefault(text.id, utils.UndoStack(TextAdapter(text)))


@utils.inline
def update_cursors(stacks: list[utils.UndoStack]):
    return utils.methodcaller("update_cursor")

@utils.inline
def map_synced_ids(ids):
    return utils.partial_map(_stack_sync_ids.__contains__)

@utils.inline
def map_ids_from_texts(texts):
    return utils.partial_map(utils.attrgetter("id"))

@utils.inline
def map_undo_stacks(texts):
    return utils.partial_map(get_undo_stack)

@utils.inline
def map_cursor_updates(stacks):
    return utils.partial_map(update_cursors)


# Called before undo/redo/undo_history.
# This preserves the texts so they can be recovered.
@utils.unsuppress
def sync_pre() -> None:
    texts  = _data.texts
    consume(map_cursor_updates(map_undo_stacks(texts)))
    _stack_sync_ids.update(map_ids_from_texts(texts))

    for st in iter_spaces(space_type='TEXT_EDITOR'):
        _stack_sync_map[st] = getattr(st.text, "id", None)


# Called after undo/redo/undo_history.
@utils.unsuppress
def sync_post() -> None:
    # Remove texts whose ids dont match those from sync_pre.
    saved_ids = map_synced_ids(map_ids_from_texts(_data.texts))
    _data.batch_remove(compress(_data.texts, map_not(saved_ids)))

    # Restore texts Blender removed from its undo/redo step.
    for stack in tuple(undo_stacks.values()):
        stack.restore_last()

    # Restore the assigned texts to open editors.
    for st in iter_spaces(space_type='TEXT_EDITOR'):
        st.text = text_from_id(_stack_sync_map.get(st))

    _stack_sync_map.clear()
    _stack_sync_ids.clear()


# Purge stacks and create new for any text blocks.
# This is called via bpy.app.handlers.load_post.
@bpy.app.handlers.persistent
def purge(*unused_args) -> None:
    for stack in undo_stacks.values():
        stack.undo.clear()
        stack.redo.clear()

    undo_stacks.clear()
    consume(map_undo_stacks(_data.texts))


# Method wrapper for python operators. Similar to the C-method wrapper below.
def pyop_wrapper(context, op=None, event=None, method=None) -> int:
    # Ensure a stack exists before calling any operators.
    stack = get_active_stack()
    try:
        result = method(*utils.filtertrue((context, op, event)))
    except:
        import traceback
        traceback.print_exc()
        return defs.OPERATOR_CANCELLED

    if result == defs.OPERATOR_FINISHED:
        stack.push_undo(tag=op.contents.idname.decode())
        # Prevent Blender from pushing its own undo.
        result = defs.OPERATOR_CANCELLED
    return result


def pyop_enable_new_undo(cls) -> None:
    utils._check_type(cls, bpy.types.Operator)

    # TODO: We could support unregistered python operators by defining a
    # ``register/unregister`` methods or wrap them if they already exist.
    assert cls.is_registered, f"Operator '{cls}' is not registered"

    if 'UNDO' in getattr(cls, "bl_options", ()):
        idname = cls.bl_rna.identifier
        methods = _pyop_store.setdefault(cls, [])

        for method in ("invoke", "execute", "modal"):
            if hasattr(cls, method):
                if method == "execute":
                    method = "exec"
                methods += overrides.override(idname, method, pyop_wrapper),


def pyop_disable_new_undo(cls) -> None:
    if cls in _pyop_store:
        overrides.restore_capsules(_pyop_store[cls])
        _pyop_store[cls].clear()
        del _pyop_store[cls]


def get_active_stack() -> utils.UndoStack:
    if text := getattr(_context, "edit_text", None):
        return get_undo_stack(text)
    return NO_STACK


def undo_poll() -> bool:
    return get_active_stack().poll_undo()


def redo_poll() -> bool:
    return get_active_stack().poll_redo()


def undo_pre() -> bool:
    if stack := get_active_stack():
        if stack.pop_undo():
            ensure_cursor_view(speed=2)
        return True
    return False


# XXX: This is nearly identical to ``undo_pre``.
def redo_pre() -> bool:
    if stack := get_active_stack():
        if stack.pop_redo():
            ensure_cursor_view(speed=2)
        return True
    return False


def unlink_pre() -> None:
    undo_stacks.pop(_context.edit_text.id, None)


def new_post(result) -> None:
    if result == defs.OPERATOR_FINISHED:
        get_undo_stack(_context.edit_text)


def save_post(result=None) -> None:
    if result in {None, defs.OPERATOR_FINISHED}:
        # Update filepath, modified time and flags from text.
        stack = get_undo_stack(_context.edit_text)
        stack.adapter.update()


# This applies the new undo for all Default operator overrides.
def _apply_default_undo() -> None:
    from textension.overrides.default import Default
    for cls in Default.operators:
        ot = overrides._get_wmOperatorType(cls.__name__)

        if not ot.flag & defs.OPTYPE_UNDO:
            continue

        for method in cls.get_defined_methods():
            name = method.__name__

            if name == "poll":
                continue

            @utils.close_cells(cls, method)
            @utils.set_name(f"{name} (Undo wrapped)")
            def wrapper(self: cls):
                stack = get_active_stack()

                # If the cursor was moved, push the current position.
                stack.update_cursor()

                try:
                    result = method(self)
                except:
                    result = defs.OPERATOR_CANCELLED
                else:
                    # Operator finished, push a step, ignore native undo.
                    if result == defs.OPERATOR_FINISHED:
                        stack.push_undo(tag=cls.__name__)
                        result = defs.OPERATOR_CANCELLED
                finally:
                    return result

            wrapper.__name__ = name
            wrapper.__qualname__ = name
            setattr(cls, name, wrapper)
            _applied_default_undos.append((cls, name, method))


def _remove_default_undo() -> None:
    for restore_args in _applied_default_undos:
        setattr(*restore_args)
    _applied_default_undos.clear()


def enable() -> None:
    # Registered TextOperators.
    for cls in utils.TextOperator.__subclasses__():
        if cls.is_registered:
            pyop_enable_new_undo(cls)

    utils.TextOperator._register_hooks += pyop_enable_new_undo,
    utils.TextOperator._unregister_hooks += pyop_disable_new_undo,

    # Internal operators.
    _apply_default_undo()

    # Override ED_OT_undo and ED_OT_redo's poll/exec methods to use the new
    # undo stack when the cursor is inside the text editor.
    from textension.overrides.default import ED_OT_undo, ED_OT_redo, ED_OT_undo_history
    ED_OT_undo.add_poll(undo_poll)
    ED_OT_undo.add_pre(undo_pre, is_global=True)
    ED_OT_undo._sync_pre_hooks += sync_pre,
    ED_OT_undo._sync_post_hooks += sync_post,

    ED_OT_redo.add_poll(redo_poll)
    ED_OT_redo.add_pre(redo_pre, is_global=True)
    ED_OT_redo._sync_post_hooks += sync_post,
    ED_OT_redo._sync_pre_hooks += sync_pre,

    ED_OT_undo_history._sync_pre_hooks += sync_pre,
    ED_OT_undo_history._sync_post_hooks += sync_post,

    # Register handler that purges all undo states between blend file loads.
    bpy.app.handlers.load_post.append(purge)

    from textension.overrides.default import TEXT_OT_unlink, TEXT_OT_new, TEXT_OT_save, TEXT_OT_save_as, TEXT_OT_resolve_conflict
    TEXT_OT_unlink.add_pre(unlink_pre, is_global=True)
    TEXT_OT_new.add_post(new_post, is_global=True)
    TEXT_OT_save.add_post(save_post, is_global=True)
    TEXT_OT_save_as.add_post(save_post, is_global=True)
    TEXT_OT_resolve_conflict.add_post(save_post, is_global=True)


def disable() -> None:
    utils.TextOperator._register_hooks.remove(pyop_enable_new_undo)
    utils.TextOperator._unregister_hooks.remove(pyop_disable_new_undo)

    # Restore Python-based operators.
    for cls in list(_pyop_store):
        pyop_disable_new_undo(cls)

    # Restore C operators.
    from textension.overrides.default import ED_OT_undo, ED_OT_redo
    ED_OT_undo.remove_poll(undo_poll)
    ED_OT_undo.remove_pre(undo_pre)
    ED_OT_undo._sync_post_hooks.remove(sync_post)
    ED_OT_undo._sync_pre_hooks.remove(sync_pre)

    ED_OT_redo.remove_poll(redo_poll)
    ED_OT_redo.remove_pre(redo_pre)
    ED_OT_redo._sync_post_hooks.remove(sync_post)
    ED_OT_redo._sync_pre_hooks.remove(sync_pre)

    from textension.overrides.default import TEXT_OT_unlink, TEXT_OT_new, TEXT_OT_save, TEXT_OT_save_as, TEXT_OT_resolve_conflict
    TEXT_OT_unlink.remove_pre(unlink_pre)
    TEXT_OT_new.remove_post(new_post)
    TEXT_OT_save.remove_post(save_post)
    TEXT_OT_save_as.remove_post(save_post)
    TEXT_OT_resolve_conflict.remove_post(save_post)

    _remove_default_undo()

    purge()
    bpy.app.handlers.load_post.remove(purge)
