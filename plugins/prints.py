import bpy
from ..types import TextOperator


class TEXTENSION_PT_prints(bpy.types.Panel):

    bl_label = "Prints"
    bl_space_type = "TEXT_EDITOR"
    bl_region_type = "UI"
    bl_category = "Text"

    texts = {}

    @classmethod
    def register(cls):
        bpy.types.WindowManager.check_print = bpy.props.BoolProperty(
            default=False, name="Print")

    @classmethod
    def rebuild_cache(cls, text):
        string = text.as_string()
        cache = []
        for idx, line in enumerate(string.splitlines()):
            # TODO: Add more checks, eg. string state.
            if "print(" in line and line.strip("# ").startswith("print"):
                line = line.strip()
                cache.append((idx, line, not line.startswith("#")))

        cache_hash = hash(string)
        cls.texts[text.name] = cache, cache_hash

        # Clean up cache
        for key in list(cls.texts):
            if key not in bpy.data.texts:
                del cls.texts[key]
        return cls.texts[text.name]

    @classmethod
    def fetch(cls, context):
        lines = []
        text = context.edit_text
        try:
            lines, cache_hash = cls.texts[text.name]
        except AttributeError:
            return
        except KeyError:
            lines, cache_hash = cls.rebuild_cache(text)
        else:
            if cache_hash != hash(text.as_string()):
                lines = TEXTENSION_PT_prints.rebuild_cache(text)[0]
        return lines

    def draw(self, context):
        layout = self.layout
        layout.prop(context.window_manager, "check_print", toggle=True)
        layout.alignment = 'LEFT'
        if not context.window_manager.check_print:
            return
        items = self.fetch(context)
        if not items:
            layout.label(text="Nothing to show here")
            return

        row = layout.row(align=True)
        row.operator("textension.toggle_print", text="On", emboss=False).state = 1
        row.operator("textension.toggle_print", text="Off", emboss=False).state = 0

        fac = 50 / context.region.width
        split = layout.split(factor=fac, align=True)
        col = split.column(align=True)
        col2 = split.column(align=True)

        for idx, line, state in items:
            row = col.row(align=True)
            row.alignment = 'EXPAND'
            op = row.operator("textension.toggle_print",
                              text=f"{idx + 1} ",
                              depress=state,
                              emboss=state).index = idx
            row = col2.row(align=True)
            op = row.operator("textension.scroll",
                              text=line.strip("# "),
                              emboss=False)
            op.type = "JUMP"
            op.jump = idx


class TEXTENSION_OT_toggle_print(TextOperator):
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Comment the line with the print statement"

    index: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})
    state: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})

    def execute(self, context):
        text = context.edit_text
        cursor = text.cursor
        lines = text.lines

        items = TEXTENSION_PT_prints.fetch(context)
        enable = self.state
        if self.index == -1:
            for idx, line, state in items:
                if (enable and state) or (not enable and not state):
                    continue
                elif enable and not state:
                    tmp = lines[idx].body.replace("#", "", 1)
                else:
                    tmp = "#" + lines[idx].body
                lines[idx].body = tmp

            TEXTENSION_PT_prints.rebuild_cache(context.edit_text)
            del TEXTENSION_PT_prints.texts[context.edit_text.name]
            text.from_string(text.as_string())
            text.cursor = cursor
            return {'FINISHED'}

        body = lines[self.index].body
        if body.strip(" ").startswith("#"):
            lines[self.index].body = body.replace("#", "", 1)
        else:
            lines[self.index].body = "#" + body
        text.from_string(text.as_string())
        text.cursor = cursor
        return {'FINISHED'}


classes = (
    TEXTENSION_OT_toggle_print,
    TEXTENSION_PT_prints
)


# def enable():
#     from ..utils import register_class_iter
#     register_class_iter(classes)

# def disable():
#     from ..utils import unregister_class_iter
#     unregister_class_iter(classes)
