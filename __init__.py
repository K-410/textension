bl_info = {
    "name": "*Textension",
    "description": "Convenience operators for text editor",
    "author": "kaio",
    "version": (1, 0, 3),
    "blender": (3, 5, 0),
    "location": "Text Editor",
    "category": "*Text Editor"
}

if "bpy" in locals():
    import sys
    if __name__ in sys.modules:
        del sys.modules[__name__]

    for name in tuple(sys.modules):
        if name.startswith("textension."):
            del sys.modules[name]


from . import api, core, overrides, prefs, ui, utils, operators
import bpy


@utils.unsuppress
def register():
    if bpy.app.version < (3, 0):
        raise Exception(f"\nTextension needs 3.0+. Found {bpy.app.version}")

    api.init()
    overrides.init()
    ui.init()
    prefs.init()

    # # Add New Text operator to right-click
    # functions.set_text_context_menu(True)

    utils.register_classes(operators.classes)
    # ui.add_hit_test('TEXT_EDITOR', 'WINDOW', functions.test_line_numbers)

    bpy.types.TEXT_HT_footer.append(core.draw_syntax_footer)


@utils.unsuppress
def unregister():
    prefs.cleanup()      # Remove preferences and plugins.
    api.cleanup()        # Remove API extensions.
    ui.cleanup()         # Cleanup GPU-related resources.
    overrides.cleanup()  # Remove overrides (run after prefs.cleanup).

    utils.unregister_classes(operators.classes)

    bpy.types.TEXT_HT_footer.remove(core.draw_syntax_footer)
    # ui.remove_hit_test(functions.test_line_numbers)
    # functions.set_text_context_menu(False)
