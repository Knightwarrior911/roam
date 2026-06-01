class RoamError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def ok(data):
    return {"ok": True, "data": data}


def err(e: RoamError):
    return {"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}}
