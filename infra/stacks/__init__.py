"""Pacote de stacks do app CDK do backend.

Cada stack corresponde a uma camada da arquitetura descrita no ``design.md`` e
é preenchida em tarefas seguintes do plano de implementação:

- :class:`~stacks.security_stack.SecurityStack`         → tarefas 2.2, 2.5
- :class:`~stacks.data_stack.DataStack`                 → tarefas 2.3, 2.6, 4.1, 4.2, 4.3
- :class:`~stacks.frontend_stack.FrontendStack`         → tarefa 2.4
- :class:`~stacks.compute_stack.ComputeStack`           → tarefas 5.x, 6.x
- :class:`~stacks.knowledge_base_stack.KnowledgeBaseStack` → tarefas 8.x
- :class:`~stacks.agents_stack.AgentsStack`             → tarefas 9.x
- :class:`~stacks.observability_stack.ObservabilityStack`  → tarefas 11.x
- :class:`~stacks.api_stack.ApiStack`                   → proxy HTTP do frontend
"""

from stacks.agents_stack import AgentsStack
from stacks.api_stack import ApiStack
from stacks.compute_stack import ComputeStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack
from stacks.knowledge_base_stack import KnowledgeBaseStack
from stacks.observability_stack import ObservabilityStack
from stacks.security_stack import SecurityStack

__all__ = [
    "SecurityStack",
    "DataStack",
    "FrontendStack",
    "ComputeStack",
    "KnowledgeBaseStack",
    "AgentsStack",
    "ObservabilityStack",
]
