
def is_enabled(name):
    from .. import prefs
    if preferences := prefs():
        return getattr(preferences.plugins.get(name), "enabled", False)
