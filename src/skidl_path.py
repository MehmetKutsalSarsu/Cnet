

import os
import sys

_LOCAL_SKIDL_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir,
    "skidl-master",
    "src",
)
_LOCAL_SKIDL_SRC = os.path.normpath(_LOCAL_SKIDL_SRC)

if _LOCAL_SKIDL_SRC not in sys.path:
    sys.path.insert(0, _LOCAL_SKIDL_SRC)
