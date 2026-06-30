"""Chat API package — split from the former 5516-line api/chat.py (Wave 5).
Public API preserved: `from api.chat import router` (+ `_build_mental_model_block`)."""
from ._router import router  # noqa: F401
# Side-effect imports: endpoint modules register their @router routes on import.
from . import _curator, _collector, _correspondent, _research, _studio  # noqa: F401
from . import _chat, _context  # noqa: F401  — these carry the 6 @router decorators
from ._common import _build_mental_model_block  # noqa: F401  — public API (verify script)
