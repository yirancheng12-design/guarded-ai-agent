"""No desktop access or DPI changes occur on import."""
__version__ = '0.4.0'
from .model import Action, Condition, Policy, Selector, WindowIdentity, VisualSpec
from .engine import Executor
from .journal import Journal

__all__ = ["Action", "Condition", "Policy", "Selector", "WindowIdentity", "VisualSpec", "Executor", "Journal"]
