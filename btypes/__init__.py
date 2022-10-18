import bpy

from ctypes import c_int, c_uint, c_short, c_bool, c_char, \
    c_char_p, c_float, c_double, c_ubyte, c_byte, c_void_p, \
    Structure, sizeof, addressof, c_uint64, POINTER, CFUNCTYPE, Union, Array


class StructBase(Structure):
    _structs = []
    """For Blender structs.

    - Field members must be defined by annotation.
    - Self-referencing pointers must be wrapped in lambdas.
    - References to yet undefined classes must be wrapped in lambdas.

    Adhering to the above points allows us to put all member definitions
    within the class body, without needing a separate definition for the
    _fields_ attribute as is conventionally required. The only caveat is
    that after all classes have been defined _init_structs() must be called
    as this sets up the _fields_ attribute for all defined StructBase classes.

    Example:

    class A(StructBase):
        value: c_int

    class B(StructBase):
        a:          lambda: A
        a_ptr:      POINTER(A)
        b_ptr:      lambda: POINTER(B)
    """

    __annotations__ = {}

    def __init_subclass__(cls):
        cls._structs.append(cls)

    def __new__(cls, srna: bpy.types.bpy_struct = None):
        """When passing no arguments, creates an instance.
        
        When passing a StructRNA instance, instantiate the struct using the
        address provided by the StructRNA's as_pointer() method.
        """
        if srna is None:
            return super().__new__(cls)
        try:
            return cls.from_address(srna.as_pointer())
        except AttributeError:
            raise Exception("Not a StructRNA instance")

    # Required
    def __init__(self, *_): pass


class ListBase(Structure):
    _cache = {}
    """Generic (void pointer) ListBase used throughout Blender.
    
    ListBase stores the first/last pointers of a linked list.

    A Typed ListBase class is created using syntax:
        ListBase(c_type)  # Returns a new class, not an instance
    """

    _fields_ = (("first", c_void_p),
                ("last",  c_void_p))

    def __new__(cls, c_type=None):
        if c_type in cls._cache:
            return cls._cache[c_type]

        elif c_type is None:
            ListBase = cls

        else:
            class ListBase(Structure):
                __name__ = __qualname__ = f"ListBase{cls.__qualname__}"
                _fields_ = (("first", POINTER(c_type)),
                            ("last",  POINTER(c_type)))
                __iter__    = cls.__iter__
                __bool__    = cls.__bool__
                __getitem__ = cls.__getitem__
        return cls._cache.setdefault(c_type, ListBase)

    # Make it possible to loop over ListBase links.
    def __iter__(self):
        links_p = []
        # Some only have "last" member assigned, use it as a fallback.
        elem_n = self.first or self.last
        elem_p = elem_n and elem_n.contents.prev

        # Temporarily store reversed links and yield them in the right order.
        if elem_p:
            while elem_p:
                links_p.append(elem_p.contents)
                elem_p = elem_p.contents.prev
            yield from reversed(links_p)

        while elem_n:
            yield elem_n.contents
            elem_n = elem_n.contents.next

    # Make it possible to use subscript, for testing.
    def __getitem__(self, index):
        return list(self)[index]

    def __bool__(self):
        return bool(self.first or self.last)



class vec2Base(StructBase):
    """Base for Blender's vec2 short/int/float types"""
    def __getitem__(self, i):
        return getattr(self, ("x", "y")[i])

    def __setitem__(self, i, val):
        setattr(self, ("x", "y")[i], val)

    def __iter__(self):
        return iter((self.x, self.y))


class vec2i(vec2Base):
    x: c_int
    y: c_int


class vec2s(vec2Base):
    x: c_short
    y: c_short


class vec2f(vec2Base):
    x: c_float
    y: c_float


class rectBase(StructBase):
    """Base for Blender's rct int/float types"""
    def get_position(self):
        return self.xmin, self.ymin

    def set_position(self, x, y):
        self.xmax -= self.xmin - x
        self.ymax -= self.ymin - y
        self.xmin = x
        self.ymin = y


# source\blender\makesdna\DNA_vec_types.h
class rctf(rectBase):
    xmin:   c_float
    xmax:   c_float
    ymin:   c_float
    ymax:   c_float


# source\blender\makesdna\DNA_vec_types.h
class rcti(rectBase):
    xmin:   c_int
    xmax:   c_int
    ymin:   c_int
    ymax:   c_int