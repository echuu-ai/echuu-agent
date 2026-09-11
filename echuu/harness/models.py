"""Serializable contracts for the content-production harness."""
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class HarnessFlags:
    reference_mode: str = 'pattern'
    intent_candidates: int = 3
    outline_candidates: int = 3
    unit_candidates: int = 1
    local_repair: bool = True
    humor: bool = False
    humor_candidates: int = 3
    max_repairs: int = 2
    max_plan_attempts: int = 2
    max_calls: int = 48
    min_chars: int = 400
    max_chars: int = 700
    seed: int = 42

    def __post_init__(self):
        if self.reference_mode not in {'none','raw','pattern'}: raise ValueError('unknown reference mode')
        for key in ('intent_candidates','outline_candidates','unit_candidates','humor_candidates'):
            if not 1<=getattr(self,key)<=3: raise ValueError('candidate count must be 1..3')
        if not 0<=self.max_repairs<=2 or not 1<=self.max_plan_attempts<=3: raise ValueError('unbounded retry config')
        if not 1<=self.max_calls<=100 or not 0<self.min_chars<self.max_chars: raise ValueError('invalid budget')

    def to_dict(self): return asdict(self)

class GateBlocked(RuntimeError):
    """Content did not qualify; caller must not play it."""

class BudgetExceeded(GateBlocked): pass
