import bpy
import ctypes


class StructBase(ctypes.Structure):
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