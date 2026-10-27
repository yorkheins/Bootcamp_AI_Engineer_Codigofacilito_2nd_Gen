"""Agentes escritos a mano: ReAct y plan-and-execute, con presupuesto y traza."""

from agents.budget import AgentBudget, StopReason
from agents.planner import Paso, Plan, PlanAndExecute
from agents.react_agent import AgentStep, ReActAgent
from agents.trace import AgentResult, TraceStep
from agents.verifier import Verificacion, VerifiedAnswer, verify_and_revise

__all__ = [
    "AgentBudget",
    "AgentResult",
    "AgentStep",
    "Paso",
    "Plan",
    "PlanAndExecute",
    "ReActAgent",
    "StopReason",
    "TraceStep",
    "VerifiedAnswer",
    "Verificacion",
    "verify_and_revise",
]
